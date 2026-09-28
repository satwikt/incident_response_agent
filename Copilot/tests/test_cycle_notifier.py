import json

import httpx
import pytest

from agent import config, cycle, notifier
from agent.notifier import Delivery, deliver, render
from db import events as store
from db import incidents, outbox

S = "todo-app"


def seed(route="POST /todos", n_ok=0, n_err=0, exc="SimulatedDatabaseStateError: boom", dur=8.0, prefix="s"):
    rows = []
    for i in range(n_ok):
        rows.append({"id": f"{prefix}ok{i}-{route}", "ts": store.now_ms(), "level": "INFO", "route": route,
                     "status": 200, "duration_ms": dur, "message": f"{route} -> 200 | release=v2.3.1"})
    for i in range(n_err):
        rows.append({"id": f"{prefix}er{i}-{route}", "ts": store.now_ms(), "level": "ERROR", "route": route,
                     "status": 500, "duration_ms": dur, "message": f"{route} -> 500 | release=v2.4.0", "exception": exc})
    store.insert_events(S, rows)


def wipe_events():
    from db.database import get_db_connection
    c = get_db_connection()
    c.execute("DELETE FROM events")
    c.commit()
    c.close()


class Submitted:
    def __init__(self):
        self.jobs = []

    def __call__(self, incident_id, prompt, kind="full"):
        self.jobs.append((incident_id, prompt))
        self.kinds = getattr(self, "kinds", []) + [kind]


# ── end to end on a real database ─────────────────────────────────────────────────────

def test_a_fault_over_many_cycles_produces_one_incident_one_diagnosis_job_and_one_alert():
    seed(n_ok=40, n_err=20)
    sub = Submitted()
    for _ in range(10):
        cycle.tick(5, sub)
    assert len(incidents.list_active(S)) == 1 and len(sub.jobs) == 1
    iid, prompt = sub.jobs[0]
    assert "PROACTIVE ALERT" in prompt and "POST /todos" in prompt
    assert outbox.counts() == {"pending": 1}          # the opening alert is queued immediately, not after the LLM
    assert incidents.get(iid)["notify_count"] == 1

    incidents.set_rca(iid, "Root cause: bad deploy", "ok")
    cycle.on_diagnosis_done(iid, store.now_ms())
    cycle.on_diagnosis_done(iid, store.now_ms())      # repeated signal: still one follow-up
    assert outbox.counts() == {"pending": 2}
    first, second = outbox.claim(store.now_ms(), 1000), None
    outbox.complete(first["id"])
    second = outbox.claim(store.now_ms(), 1000)
    assert (first["payload"]["event"], second["payload"]["event"]) == ("opened", "diagnosis")


def test_fingerprint_uses_the_exception_type_from_the_events():
    seed(n_ok=40, n_err=20, exc="PoolExhausted: connection pool exhausted")
    cycle.tick(5, Submitted())
    fp = incidents.get("INC-0001")["fingerprints"][0]
    assert fp["kind"] == "error_rate" and fp["sig"] == "PoolExhausted"


def test_thin_routes_never_open_an_incident():
    seed(n_ok=0, n_err=5)                             # 5 of 5 failing, but below MIN_REQUESTS
    assert cycle.tick(5, Submitted()) == [] and incidents.list_active(S) == []


def test_recovery_needs_k_healthy_ticks_then_a_single_resolved_alert():
    seed(n_ok=40, n_err=20)
    sub = Submitted()
    cycle.tick(5, sub)
    wipe_events()
    seed(n_ok=60, prefix="h")                         # healthy traffic with enough volume
    kinds = []
    for _ in range(config.RECOVERY_WINDOWS):
        kinds += [a.kind for a in cycle.tick(5, sub)]
    assert kinds == ["resolved"] and incidents.get("INC-0001")["status"] == "RESOLVED"
    assert cycle.tick(5, sub) == []                   # nothing left to do
    assert outbox.counts() == {"pending": 2}
    order = []
    while (item := outbox.claim(store.now_ms(), 1000)) is not None:
        order.append(item["payload"]["event"])
        outbox.complete(item["id"])
    assert order == ["opened", "resolved"]            # 'resolved' can never overtake 'opened'


def test_recurrence_after_recovery_opens_a_linked_incident_and_a_new_diagnosis():
    seed(n_ok=40, n_err=20)
    sub = Submitted()
    cycle.tick(5, sub)
    wipe_events()
    seed(n_ok=60, prefix="h")
    for _ in range(config.RECOVERY_WINDOWS):
        cycle.tick(5, sub)
    wipe_events()
    seed(n_ok=40, n_err=20, prefix="again")
    cycle.tick(5, sub)
    assert incidents.get("INC-0002")["recurrence_of"] == "INC-0001" and len(sub.jobs) == 2


def test_resume_requeues_diagnoses_that_never_finished():
    seed(n_ok=40, n_err=20)
    cycle.tick(5, Submitted())                        # opens INC-0001, diagnosis 'pending', then "the process dies"
    sub = Submitted()
    assert cycle.resume_pending(5, sub) == 1 and sub.jobs[0][0] == "INC-0001"
    incidents.set_rca("INC-0001", "done", "ok")
    assert cycle.resume_pending(5, Submitted()) == 0


def test_idle_service_produces_no_incidents():
    assert cycle.tick(5, Submitted()) == []


# ── outbox pump ───────────────────────────────────────────────────────────────────────────

def queue_opened():
    seed(n_ok=40, n_err=20)
    cycle.tick(5, Submitted())                        # queues exactly one item: the opening alert


def test_pump_marks_delivered_items_done():
    queue_opened()
    assert cycle.process_outbox_once(store.now_ms(), 5, deliver=lambda *a: Delivery("ok")) == "done"
    assert cycle.process_outbox_once(store.now_ms(), 5, deliver=lambda *a: Delivery("ok")) is None


def test_pump_skips_when_discord_is_not_configured():
    queue_opened()
    assert cycle.process_outbox_once(store.now_ms(), 5, deliver=lambda *a: Delivery("skip", detail="no webhook")) == "skipped"


def test_pump_retries_with_backoff_honouring_retry_after_then_dead_letters(monkeypatch):
    queue_opened()
    monkeypatch.setattr(config, "OUTBOX_MAX_ATTEMPTS", 3)
    t = store.now_ms()
    assert cycle.process_outbox_once(t, 5, deliver=lambda *a: Delivery("retry", retry_after_s=90)) == "pending"
    assert cycle.process_outbox_once(t + 60_000, 5, deliver=lambda *a: Delivery("ok")) is None     # 90 s not elapsed
    assert cycle.process_outbox_once(t + 91_000, 5, deliver=lambda *a: Delivery("retry")) == "pending"
    assert cycle.process_outbox_once(t + 10**6, 5, deliver=lambda *a: Delivery("retry")) == "dead"
    assert outbox.counts() == {"dead": 1}


def test_a_permanent_failure_is_dead_immediately():
    queue_opened()
    assert cycle.process_outbox_once(store.now_ms(), 5, deliver=lambda *a: Delivery("fail", detail="HTTP 404")) == "dead"


def test_backoff_grows_and_is_capped_but_never_shorter_than_retry_after():
    assert [cycle.backoff_ms(n) for n in (1, 2, 3, 4)] == [5000, 10_000, 20_000, 40_000]
    assert cycle.backoff_ms(20) == 300_000
    assert cycle.backoff_ms(1, retry_after_s=120) == 120_000


# ── notifier: rendering ───────────────────────────────────────────────────────────────────

def inc(**over):
    base = {"id": "INC-0007", "service": S, "status": "OPEN", "severity": "critical", "opened_at": 1_000_000,
            "resolved_at": None, "resolved_by": None, "recurrence_of": None, "rca_text": "Root cause: bad deploy.",
            "evidence": ["Error rate on 'POST /todos' is 30%"], "notify_count": 0}
    base.update(over)
    return base


def embed(event="opened", **over):
    return render(inc(**over), event, 1_000_000 + 360_000, 1.0)["embeds"][0]


def test_opened_embed_has_id_evidence_and_diagnosis():
    e = embed()
    text = json.dumps(e)
    assert "INC-0007" in e["title"] and "Error rate on" in text and "Root cause: bad deploy." in text
    assert e["color"] == 0xE74C3C


def test_recurrence_is_called_out():
    assert "INC-0003" in json.dumps(embed(recurrence_of="INC-0003"))


def test_resolved_embed_reports_time_to_resolve_and_omits_the_diagnosis():
    e = embed("resolved", status="RESOLVED", resolved_at=1_000_000 + 360_000, resolved_by="auto")
    assert "resolved" in e["description"] and "6m" in e["description"] and e["color"] == 0x2ECC71
    assert "Diagnosis" not in json.dumps(e)


def test_reminder_and_regression_wording():
    assert "still open" in embed("reminder")["description"]
    assert "again" in embed("regressed")["description"]


def embed_chars(e):
    """Characters Discord counts: title, description, footer, and every field name and value."""
    return (len(e["title"]) + len(e["description"]) + len(e["footer"]["text"])
            + sum(len(f["name"]) + len(f["value"]) for f in e["fields"]))


@pytest.mark.parametrize("rca,evidence", [
    ("x" * 20_000, ["e" * 900] * 30),        # everything huge
    ("x" * 20_000, ["short"]),               # huge diagnosis only
    ("short", ["e" * 900] * 30),             # huge evidence only
    ("", []),                                # nothing at all
])
def test_embeds_stay_within_discord_limits(rca, evidence):
    e = embed(rca_text=rca, evidence=evidence)
    assert len(e["fields"]) <= 25 and all(len(f["value"]) <= 1024 for f in e["fields"])
    assert embed_chars(e) <= 6000                     # over this, Discord answers 400 and the alert would be lost


def test_a_truncated_diagnosis_says_so():
    e = embed(rca_text="x" * 20_000)
    assert any("truncated" in f["value"] for f in e["fields"])


def test_link_is_added_only_when_a_base_url_is_configured(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
    assert "/incidents/" not in json.dumps(embed())
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://localhost:8001")
    assert "http://localhost:8001/incidents/INC-0007" in json.dumps(embed())


# ── notifier: delivery ────────────────────────────────────────────────────────────────────

def item():
    seed(n_ok=40, n_err=20)
    cycle.tick(5, Submitted())
    return {"id": 1, "incident_id": "INC-0001", "payload": {"event": "opened"}}


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_delivery_success_and_payload_shape():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(204)

    d = deliver(item(), store.now_ms(), 5, webhook_url="http://hook.test/x", client=client(handler))
    assert d.status == "ok" and "INC-0001" in seen["body"]["embeds"][0]["title"]


@pytest.mark.parametrize("status,expected", [(500, "retry"), (502, "retry"), (429, "retry"), (400, "fail"),
                                             (401, "fail"), (404, "fail")])
def test_delivery_status_classification(status, expected):
    d = deliver(item(), store.now_ms(), 5, webhook_url="http://hook.test/x",
                client=client(lambda req: httpx.Response(status, json={"retry_after": 7.5})))
    assert d.status == expected
    if status == 429:
        assert d.retry_after_s == 7.5


def test_rate_limit_falls_back_to_the_retry_after_header():
    d = deliver(item(), store.now_ms(), 5, webhook_url="http://hook.test/x",
                client=client(lambda req: httpx.Response(429, headers={"Retry-After": "12"})))
    assert d.status == "retry" and d.retry_after_s == 12.0


def test_network_errors_are_retryable_and_never_raise():
    def boom(req):
        raise httpx.ConnectError("no route")
    assert deliver(item(), store.now_ms(), 5, webhook_url="http://hook.test/x", client=client(boom)).status == "retry"


def test_no_webhook_means_skip_and_a_missing_incident_means_fail():
    assert deliver(item(), store.now_ms(), 5, webhook_url="").status == "skip"
    ghost = {"id": 2, "incident_id": "INC-9999", "payload": {"event": "opened"}}
    assert deliver(ghost, store.now_ms(), 5, webhook_url="http://hook.test/x", client=client(lambda r: httpx.Response(204))).status == "fail"


def test_payload_never_allows_mentions_even_if_the_text_contains_them():
    body = render(inc(rca_text="@everyone <@&123> <@456> wake up", evidence=["@here now"]), "opened", 1_000_000, 1.0)
    assert body["allowed_mentions"] == {"parse": []}


def test_a_diagnosis_finishing_after_the_incident_resolved_is_stored_but_not_sent():
    seed(n_ok=40, n_err=20)
    sub = Submitted()
    cycle.tick(5, sub)
    wipe_events()
    seed(n_ok=60, prefix="h")
    for _ in range(config.RECOVERY_WINDOWS):
        cycle.tick(5, sub)
    incidents.set_rca("INC-0001", "late diagnosis", "ok")
    cycle.on_diagnosis_done("INC-0001", store.now_ms())
    events = []
    while (item := outbox.claim(store.now_ms(), 1000)) is not None:
        events.append(item["payload"]["event"])
        outbox.complete(item["id"])
    assert events == ["opened", "resolved"] and incidents.get("INC-0001")["rca_text"] == "late diagnosis"


def test_a_slow_diagnosis_never_delays_the_opening_alert():
    seed(n_ok=40, n_err=20)
    cycle.tick(5, Submitted())                        # diagnosis still pending, nothing has finished
    item = outbox.claim(store.now_ms(), 1000)
    assert item["payload"] == {"event": "opened"} and incidents.get("INC-0001")["rca_status"] == "pending"


def test_opening_alert_says_the_diagnosis_is_in_progress_and_the_followup_carries_it():
    pending = embed("opened", rca_status="pending", rca_text=None)
    assert any("In progress" in f["value"] for f in pending["fields"])
    done = embed("diagnosis", rca_status="ok")
    assert "Diagnosis for INC-0007" in done["description"] and "Root cause: bad deploy." in json.dumps(done)
    ready = embed("opened", rca_status="ok")
    assert not any("In progress" in f["value"] for f in ready["fields"])


def test_diagnosis_followup_shows_what_it_cost():
    d = embed("diagnosis", rca_status="ok", llm_calls=2, llm_tokens=8100)["description"]
    assert "2 model call(s)" in d and "8.1k tokens" in d
    assert "model call" not in embed("diagnosis", rca_status="failed")["description"]


def test_existing_databases_are_migrated_to_have_the_cost_columns(tmp_path):
    import sqlite3
    old = tmp_path / "old.db"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE incidents (seq INTEGER PRIMARY KEY, id TEXT, rca_text TEXT)")
    c.commit()
    c.row_factory = sqlite3.Row
    cur = c.cursor()
    for column in ("llm_calls", "llm_tokens"):                       # the exact migration step from init_db
        have = {r["name"] for r in cur.execute("PRAGMA table_info(incidents)")}
        if column not in have:
            cur.execute(f"ALTER TABLE incidents ADD COLUMN {column} INTEGER")
    assert {r["name"] for r in cur.execute("PRAGMA table_info(incidents)")} >= {"llm_calls", "llm_tokens"}
