import asyncio

import pytest

from agent import diagnosis as dg
from agent.diagnosis import DiagnosisService, templated
from agent.incident_manager import IncidentConfig, process_cycle
from agent.detect import Breach
from db import incidents, outbox

T0 = 1_800_000_000_000


def make_incident():
    process_cycle("todo-app", [Breach("error_rate", "POST /todos", "Error rate 30%")], lambda r, k: "E", set(), True, T0,
                  IncidentConfig())
    return "INC-0001"


# ── outbox ──────────────────────────────────────────────────────────────────────────────

def test_enqueue_is_idempotent_on_the_dedupe_key():
    assert outbox.enqueue("discord", "k1", "INC-1", {"event": "opened"}, T0) is True
    assert outbox.enqueue("discord", "k1", "INC-1", {"event": "opened"}, T0) is False
    assert outbox.counts() == {"pending": 1}


def test_claim_returns_due_items_oldest_first_and_only_once():
    outbox.enqueue("discord", "a", "INC-1", {"n": 1}, T0)
    outbox.enqueue("discord", "b", "INC-2", {"n": 2}, T0)
    first = outbox.claim(T0, 60_000)
    second = outbox.claim(T0, 60_000)
    assert (first["payload"]["n"], second["payload"]["n"]) == (1, 2) and outbox.claim(T0, 60_000) is None
    assert first["attempts"] == 1


def test_items_not_yet_due_are_not_claimed():
    outbox.enqueue("discord", "a", None, {}, T0)
    item = outbox.claim(T0, 1000)
    outbox.retry(item["id"], 30_000, "boom", T0, 6)
    assert outbox.claim(T0 + 29_999, 1000) is None
    assert outbox.claim(T0 + 30_000, 1000)["attempts"] == 2


def test_later_items_for_an_incident_wait_for_earlier_ones():
    outbox.enqueue("discord", "opened", "INC-1", {"e": "opened"}, T0)
    outbox.enqueue("discord", "resolved", "INC-1", {"e": "resolved"}, T0)
    first = outbox.claim(T0, 60_000)
    assert first["payload"]["e"] == "opened"
    assert outbox.claim(T0, 60_000) is None                     # 'resolved' must not overtake 'opened'
    outbox.retry(first["id"], 10_000, "transient", T0, 6)
    assert outbox.claim(T0 + 1, 60_000) is None                 # still ordered behind the pending retry
    outbox.complete(first["id"])
    assert outbox.claim(T0 + 1, 60_000)["payload"]["e"] == "resolved"


def test_other_incidents_are_not_blocked_by_a_stuck_one():
    outbox.enqueue("discord", "a", "INC-1", {"n": 1}, T0)
    outbox.enqueue("discord", "b", "INC-2", {"n": 2}, T0)
    stuck = outbox.claim(T0, 60_000)
    assert outbox.claim(T0, 60_000)["payload"]["n"] == 2 and stuck["payload"]["n"] == 1


def test_a_crashed_sender_lease_expires_and_the_item_is_reclaimed():
    outbox.enqueue("discord", "a", "INC-1", {}, T0)
    outbox.claim(T0, 1000)                                        # sender dies without completing
    assert outbox.claim(T0 + 500, 1000) is None
    again = outbox.claim(T0 + 1000, 1000)
    assert again is not None and again["attempts"] == 2


def test_dead_letter_after_max_attempts_and_it_stays_visible():
    outbox.enqueue("discord", "a", None, {}, T0)
    status = None
    for n in range(3):
        item = outbox.claim(T0 + n * 10_000, 1000)
        status = outbox.retry(item["id"], 0, "HTTP 500", T0 + n * 10_000, 3)
    assert status == "dead" and outbox.counts() == {"dead": 1}
    assert outbox.claim(T0 + 10**7, 1000) is None


def test_skip_and_complete_remove_items_from_circulation():
    outbox.enqueue("discord", "a", None, {}, T0)
    outbox.enqueue("discord", "b", None, {}, T0)
    outbox.skip(outbox.claim(T0, 1000)["id"], "no webhook")
    outbox.complete(outbox.claim(T0, 1000)["id"])
    assert outbox.counts() == {"skipped": 1, "done": 1} and outbox.claim(T0, 1000) is None


# ── diagnosis service ───────────────────────────────────────────────────────────────────

class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def svc(diagnose, clock=None, **kw):
    done = []
    kw.setdefault("timeout_s", 2.0)
    s = DiagnosisService(diagnose, on_done=done.append, clock=clock or Clock(), **kw)
    return s, done


def run(coro):
    return asyncio.run(coro)


def test_success_stores_the_diagnosis_and_signals_done():
    iid = make_incident()

    async def ok(prompt):
        return "  Root cause: bad deploy  "

    s, done = svc(ok)
    assert run(s.process(iid, "p")) == "ok"
    inc = incidents.get(iid)
    assert inc["rca_text"] == "Root cause: bad deploy" and inc["rca_status"] == "ok" and done == [iid]


def test_failure_stores_a_templated_message_without_the_error_detail_and_still_signals_done():
    iid = make_incident()

    async def bad(prompt):
        raise RuntimeError("provider said: api key sk-SECRET is invalid")

    s, done = svc(bad)
    assert run(s.process(iid, "p")) == "failed"
    inc = incidents.get(iid)
    assert inc["rca_status"] == "failed" and "RuntimeError" in inc["rca_text"]
    assert "sk-SECRET" not in inc["rca_text"] and done == [iid]


def test_empty_answer_counts_as_a_failure():
    iid = make_incident()

    async def empty(prompt):
        return "   "

    s, _ = svc(empty)
    assert run(s.process(iid, "p")) == "failed"


def test_timeout_is_a_failure_not_a_hang():
    iid = make_incident()

    async def slow(prompt):
        await asyncio.sleep(5)
        return "late"

    s, _ = svc(slow, timeout_s=0.05)
    assert run(s.process(iid, "p")) == "failed" and "TimeoutError" in incidents.get(iid)["rca_text"]


def test_circuit_breaker_stops_calling_the_model_then_recovers():
    iid = make_incident()
    calls = []
    clock = Clock()

    async def bad(prompt):
        calls.append(1)
        raise ConnectionError("down")

    s, _ = svc(bad, clock, breaker_failures=2, breaker_pause_s=100, max_runs_per_hour=99)
    assert [run(s.process(iid, "p")) for _ in range(2)] == ["failed", "failed"]     # breaker opens after 2
    assert run(s.process(iid, "p")) == "breaker" and len(calls) == 2                 # model not called
    assert "paused" in incidents.get(iid)["rca_text"]
    clock.t += 101
    assert run(s.process(iid, "p")) == "failed" and len(calls) == 3                  # tried again after the pause


def test_a_success_resets_the_consecutive_failure_count():
    iid = make_incident()
    results = iter([False, True, False, False])

    async def flaky(prompt):
        if next(results):
            return "fine"
        raise ValueError("x")

    s, _ = svc(flaky, breaker_failures=2, max_runs_per_hour=99)
    out = [run(s.process(iid, "p")) for _ in range(4)]
    assert out == ["failed", "ok", "failed", "failed"]                               # never 2 in a row before the end
    assert run(s.process(iid, "p")) in ("breaker",)


def test_hourly_cap_uses_the_template_and_frees_up_after_an_hour():
    iid = make_incident()
    clock = Clock()

    async def ok(prompt):
        return "diagnosis"

    s, _ = svc(ok, clock, max_runs_per_hour=2)
    assert [run(s.process(iid, "p")) for _ in range(3)] == ["ok", "ok", "cap"]
    assert "hourly limit" in incidents.get(iid)["rca_text"]
    clock.t += 3601
    assert run(s.process(iid, "p")) == "ok"


def test_submit_is_deduplicated_per_incident():
    async def go():
        async def ok(p):
            return "x"
        s, _ = svc(ok)
        return s.submit("INC-1", "a"), s.submit("INC-1", "b"), s.pending
    assert run(go()) == (True, False, 1)


def test_worker_loop_survives_a_job_that_raises_and_processes_the_next():
    iid = make_incident()

    async def go():
        async def ok(p):
            return "done"
        s, done = svc(ok)
        s.submit("INC-9999", "for an incident that does not exist")   # set_rca on a missing row is harmless
        s.submit(iid, "real")
        worker = asyncio.create_task(s.run_forever())
        for _ in range(100):
            if iid in done:
                break
            await asyncio.sleep(0.02)
        worker.cancel()
        return done
    assert iid in run(go())


def test_templated_text_mentions_the_reason():
    assert "hourly limit" in templated("hourly limit of 12 diagnoses reached")


# ── rate limits are waited out, not treated as failures ───────────────────────────────────

class RateLimitError(Exception):
    pass


def slept():
    log = []

    async def fake_sleep(s):
        log.append(s)
    return log, fake_sleep


def test_a_rate_limit_is_waited_out_and_the_diagnosis_still_succeeds():
    iid = make_incident()
    calls = []

    async def flaky(prompt):
        calls.append(1)
        if len(calls) < 3:
            raise RateLimitError("429 Too Many Requests: tokens per minute")
        return "diagnosis after waiting"

    log, fake_sleep = slept()
    s, _ = svc(flaky, rate_limit_retries=2, rate_limit_wait_s=42, sleep=fake_sleep)
    assert run(s.process(iid, "p")) == "ok" and len(calls) == 3 and log == [42, 42]
    assert incidents.get(iid)["rca_text"] == "diagnosis after waiting"


def test_exhausted_rate_limit_retries_count_as_one_failure_for_the_breaker():
    iid = make_incident()
    calls = []

    async def always_limited(prompt):
        calls.append(1)
        raise RateLimitError("429")

    log, fake_sleep = slept()
    s, _ = svc(always_limited, rate_limit_retries=2, rate_limit_wait_s=1, breaker_failures=2, sleep=fake_sleep)
    assert run(s.process(iid, "p")) == "failed" and len(calls) == 3
    assert s._consecutive_failures == 1                                    # one failure, not three
    assert "RateLimitError" in incidents.get(iid)["rca_text"]


def test_other_errors_are_not_retried():
    iid = make_incident()
    calls = []

    async def broken(prompt):
        calls.append(1)
        raise ValueError("bad request")

    log, fake_sleep = slept()
    s, _ = svc(broken, rate_limit_retries=2, sleep=fake_sleep)
    assert run(s.process(iid, "p")) == "failed" and len(calls) == 1 and log == []


@pytest.mark.parametrize("exc,expected", [
    (RateLimitError("x"), True), (Exception("HTTP 429 Too Many Requests"), True),
    (Exception("Rate limit reached for model"), True), (ValueError("nope"), False), (TimeoutError(), False),
])
def test_rate_limit_classification(exc, expected):
    assert dg.is_rate_limit(exc) is expected


# ── cost of a diagnosis is recorded ───────────────────────────────────────────────────────

def test_result_objects_store_calls_and_tokens_and_plain_strings_still_work():
    iid = make_incident()

    async def with_stats(prompt):
        return dg.DiagnosisResult(text="diag", llm_calls=2, tokens=8100)

    s, _ = svc(with_stats)
    assert run(s.process(iid, "p")) == "ok"
    inc = incidents.get(iid)
    assert (inc["rca_text"], inc["llm_calls"], inc["llm_tokens"]) == ("diag", 2, 8100)

    async def plain(prompt):
        return "plain text"

    s2, _ = svc(plain)
    run(s2.process(iid, "p"))
    inc = incidents.get(iid)
    assert inc["rca_text"] == "plain text" and inc["llm_calls"] is None


def test_failed_diagnosis_records_no_cost():
    iid = make_incident()

    async def bad(prompt):
        raise ValueError("x")

    s, _ = svc(bad)
    run(s.process(iid, "p"))
    assert incidents.get(iid)["llm_calls"] is None


@pytest.mark.parametrize("message,expected", [
    ("Rate limit reached ... Please try again in 27.8175s. Need more tokens?", 27.8175),
    ("... try again in 2m1.5s ...", 121.5),
    ("... Try again in 14.477142857s.", 14.477142857),
    ("no hint here", None),
])
def test_provider_retry_delay_is_parsed(message, expected):
    got = dg.retry_after_seconds(RateLimitError(message))
    assert got == pytest.approx(expected) if expected is not None else got is None


def test_rate_limit_wait_follows_the_providers_delay_with_bounds():
    iid = make_incident()
    msgs = iter(["429 try again in 27.8s", "429 try again in 0.2s", "429 try again in 900s"])
    waits, fake_sleep = slept()
    calls = []

    async def limited(prompt):
        calls.append(1)
        if len(calls) <= 3:
            raise RateLimitError(next(msgs))
        return "ok"

    s, _ = svc(limited, rate_limit_retries=3, rate_limit_wait_s=45, sleep=fake_sleep)
    assert run(s.process(iid, "p")) == "ok"
    assert waits == [pytest.approx(30.8), 5.0, 120.0]          # provider delay + margin, floor 5 s, ceiling 120 s


# ── daily token budget ────────────────────────────────────────────────────────────────────

def extra_incident(n):
    return incidents.create("todo-app", [{"fp": f"fp-extra-{n}", "kind": "latency", "route": f"GET /r{n}", "sig": "latency"}],
                            "warning", ["slow"], T0 + n)["id"]


def test_the_model_is_not_called_once_the_daily_token_budget_is_spent():
    first, second, third = make_incident(), extra_incident(2), extra_incident(3)
    calls = []

    async def ok(prompt):
        calls.append(1)
        return dg.DiagnosisResult(text="diag", llm_calls=2, tokens=60_000)

    clock = Clock(T0 / 1000 + 60)                           # 'now' is within 24 h of the incidents
    s, _ = svc(ok, clock, daily_token_budget=100_000, max_runs_per_hour=99)
    assert run(s.process(first, "p")) == "ok"               # 0 spent so far
    assert run(s.process(second, "p")) == "ok"              # 60k spent, still under 100k
    assert run(s.process(third, "p")) == "budget"           # 120k recorded: refused, model not called
    assert len(calls) == 2 and "daily model budget" in incidents.get(third)["rca_text"]


def test_tokens_from_yesterday_do_not_count():
    iid = make_incident()
    incidents.set_rca(iid, "old", "ok", 2, 90_000)
    opened = incidents.get(iid)["opened_at"]
    assert incidents.tokens_used_since(opened - 1) == 90_000
    assert incidents.tokens_used_since(opened + 1) == 0


def test_budget_zero_means_unlimited():
    iid = make_incident()
    incidents.set_rca(iid, "old", "ok", 2, 10**9)

    async def ok(prompt):
        return "diag"

    clock = Clock(incidents.get(iid)["opened_at"] / 1000 + 60)
    s, _ = svc(ok, clock, daily_token_budget=0, max_runs_per_hour=99)
    assert run(s.process(iid, "p")) == "ok"
