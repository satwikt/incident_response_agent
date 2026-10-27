"""agent/memory.py: pure helpers and HindsightMemory against a fake client."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent import memory as mm
from agent.memory import (
    HindsightMemory,
    NullMemory,
    Recalled,
    build_narrative,
    context_of,
    parse_proposed_action,
    split_actions,
)
from tests.generated_opus.conftest import FakeHindsightClient, hit

INC = {"id": "INC-0042", "service": "checkout",
       "fingerprints": [{"fp": "fp-1", "kind": "error_rate", "route": "POST /checkout", "sig": "PaymentGatewayException"},
                        {"fp": "fp-2", "kind": "latency", "route": "GET /cart", "sig": "latency"}],
       "evidence": ["Error rate on POST /checkout is 30%"], "rca_text": "Root cause: gateway down.\nSecond line."}


# ── context_of ───────────────────────────────────────────────────────────────────────────────

def test_memory_context_json_message_and_exception_unpacked():
    """REQ: context suffix formatted as JSON is 'unpacked to clean text' (context_of docstring)."""
    msg = 'POST /checkout -> 500 | {"message": "pool exhausted", "exception": "TimeoutError", "level": "ERROR"}'
    assert context_of(msg) == "pool exhausted (TimeoutError)"


def test_memory_context_json_msg_key():
    """REQ: JSON context is unpacked to clean text (context_of docstring); 'msg' is the common alt key."""
    assert context_of('x | {"msg": "release=v2 rolled"}') == "release=v2 rolled"


def test_memory_context_truncated_json_still_unpacked():
    """REQ: JSON context is unpacked to clean text (docstring) even when the event message was cut off mid-JSON."""
    assert context_of('x | {"message": "pool exhausted", "exception": "Timeo').startswith("pool exhausted")


def test_memory_context_uses_last_separator():
    """REQ: context is the suffix after the LAST ' | ' (context_of docstring 'runtime-context suffix')."""
    assert context_of("a | b | release=v3") == "release=v3"


@pytest.mark.parametrize("msg", ["", None, "a|b", "no separator at all"])
def test_memory_context_absent_is_empty(msg):
    """REQ: no runtime-context suffix -> empty string (context_of)."""
    assert context_of(msg) == ""


def test_memory_context_is_bounded_and_unicode_safe():
    """REQ: context_of returns a short context sample (stored in context_sample) and keeps unicode."""
    out = context_of("x | release=v1 ü " + "y" * 1000)
    assert out.startswith("release=v1 ü") and len(out) <= 200


# ── build_narrative ──────────────────────────────────────────────────────────────────────────

def test_memory_narrative_header_and_flattened_diagnosis():
    """REQ: narrative is structured to read well as a memory: header with id/service/source, diagnosis on one line
    (build_narrative docstring; memory.py 'episodic: ... the diagnosis')."""
    n = build_narrative(INC, [], None, [], "", None, source="seed")
    assert n.splitlines()[0] == "[INC-0042 svc=checkout outcome=resolved source=seed]"
    assert "Diagnosis at the time: Root cause: gateway down. Second line." in n


def test_memory_narrative_routes_sorted_and_exception_only_from_error_rate():
    """REQ: narrative carries affected routes and the exception type (episodic memory, memory.py docstring)."""
    n = build_narrative(INC, [], None, [], "", None)
    assert "Affected routes: GET /cart, POST /checkout." in n
    assert "Exception type: PaymentGatewayException." in n and "latency." not in n.split("Exception type:")[1].split("\n")[0]


def test_memory_narrative_missing_evidence_says_not_recorded():
    """REQ: narrative is always well-formed even without evidence (build_narrative)."""
    n = build_narrative({"id": "I", "service": "s"}, [], None, [], "", None)
    assert "Symptoms: not recorded." in n


def test_memory_narrative_zero_minutes_is_reported():
    """REQ: 'how long it took' is part of episodic memory (memory.py docstring); 0 minutes is a valid duration."""
    assert "Time to resolve: 0.0 minutes." in build_narrative(INC, [], "x", [], "", 0)


def test_memory_narrative_unknown_duration_omitted():
    """REQ: unknown resolve time is omitted rather than invented (build_narrative)."""
    assert "Time to resolve" not in build_narrative(INC, [], "x", [], "", None)


def test_memory_narrative_long_diagnosis_truncated():
    """REQ: diagnosis excerpt is bounded (build_narrative keeps the first 500 chars)."""
    n = build_narrative(dict(INC, rca_text="z" * 5000), [], None, [], "", None)
    line = [l for l in n.splitlines() if l.startswith("Diagnosis at the time:")][0]
    assert len(line) <= len("Diagnosis at the time: ") + 500


# ── split_actions ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("actions,expected", [
    ([{"action": "restart_worker", "worked": False}, {"action": "flush_pool", "worked": True}], ("flush_pool", ["restart_worker"])),
    ([{"action": "flush_pool", "worked": True}, {"action": "restart_worker", "worked": False}], ("flush_pool", ["restart_worker"])),
    ([{"action": "flush_pool", "worked": False}, {"action": "flush_pool", "worked": True}], ("flush_pool", [])),
    ([{"action": "a", "worked": True}, {"action": "b", "worked": True}], ("b", [])),
])
def test_memory_split_actions_prioritizes_operator_feedback(actions, expected):
    """REQ: 'Prioritizes explicit worked: True from operator feedback' (split_actions docstring)."""
    assert split_actions(actions, None) == expected


def test_memory_split_actions_explicitly_failed_action_is_never_the_fix():
    """REQ: fallback to 'the last action' applies only 'for unannotated actions' (split_actions docstring). An action the
    operator explicitly marked worked=False must not be reported as the fix."""
    fix, failed = split_actions([{"action": "rollback_config"}, {"action": "restart_worker", "worked": False}], None)
    assert fix != "restart_worker"
    assert "restart_worker" in failed


def test_memory_split_actions_only_failures_yield_no_fix():
    """REQ: explicit operator feedback has priority (split_actions docstring); if every action was marked as failed there
    is no fix that worked."""
    assert split_actions([{"action": "flush_pool", "worked": False}], None) == (None, ["flush_pool"])


def test_memory_split_actions_allow_list_excludes_everything():
    """REQ: with an allow-list, unknown actions are ignored (split_actions 'allowed')."""
    assert split_actions([{"action": "rm_rf", "worked": True}], {"flush_pool"}) == (None, [])


def test_memory_split_actions_entries_without_name_ignored():
    """REQ: entries without an action name are not actions (split_actions)."""
    assert split_actions([{"worked": True}, {"action": ""}], None) == (None, [])


# ── parse_proposed_action ────────────────────────────────────────────────────────────────────

def test_memory_proposed_any_snake_case_without_allow_list():
    """REQ: with allowed=None any well-formed action name is accepted (parse_proposed_action; dashboard uses this)."""
    assert parse_proposed_action("Proposed action: escalate_to_sre", None) == "escalate_to_sre"


def test_memory_proposed_case_insensitive_and_normalized():
    """REQ: 'Proposed action: X' line is recognised regardless of case (parse_proposed_action regex IGNORECASE)."""
    assert parse_proposed_action("PROPOSED ACTION: Flush_Pool", {"flush_pool"}) == "flush_pool"


@pytest.mark.parametrize("word", ["none", "noop", "NULL", "undefined"])
def test_memory_proposed_none_like_values_mean_no_action(word):
    """REQ: returns None if the proposal is 'none' (parse_proposed_action docstring)."""
    assert parse_proposed_action(f"Proposed action: {word}", None) is None


def test_memory_proposed_trailing_punctuation_and_middle_of_text():
    """REQ: the action named on a 'Proposed action: X' line is returned (docstring) - punctuation is not part of it."""
    text = "Symptom...\nProposed action: rollback_release.\nThanks"
    assert parse_proposed_action(text, {"rollback_release"}) == "rollback_release"


def test_memory_proposed_none_text():
    """REQ: no text -> None (parse_proposed_action)."""
    assert parse_proposed_action(None, None) is None


@pytest.mark.parametrize("line", [
    "Proposed action: flush_pool",
    "**Proposed action:** flush_pool",
    "**Proposed action**: flush_pool",
    "Proposed action: **flush_pool**",
    "Proposed action: `flush_pool`",
    "  __Proposed action:__ flush_pool",
])
def test_memory_proposed_markdown_bold(line):
    """Decision (demo): markdown around the action line is tolerated - the nano model answered
    'Proposed action: **disable_fault**' and the incident was left without a proposal."""
    assert parse_proposed_action("Root cause: x\n" + line, {"flush_pool"}) == "flush_pool"


# ── NullMemory / make_memory ─────────────────────────────────────────────────────────────────

def test_memory_null_operation_status_unknown():
    """REQ: memory off = no calls, no results (NullMemory docstring)."""
    assert NullMemory().operation_status("op") == "unknown"
    assert NullMemory().retain({}, "", [], None, []).detail == "memory is off"


@pytest.mark.parametrize("mode", ["off", "", None, "maybe"])
def test_memory_make_memory_non_on_modes_are_null(mode):
    """REQ: Hindsight only when mode is 'on' and a URL is set, otherwise NullMemory (make_memory docstring)."""
    assert isinstance(mm.make_memory("http://hs", "b", mode), NullMemory)


def test_memory_make_memory_on_builds_hindsight(monkeypatch):
    """REQ: mode 'on' (case-insensitive) with a URL builds HindsightMemory with a dedicated executor
    (make_memory docstring; HindsightMemory docstring)."""
    import hindsight_client

    class FakeH:
        def __init__(self, base_url):
            self.base_url = base_url
    monkeypatch.setattr(hindsight_client, "Hindsight", FakeH)
    m = mm.make_memory("http://hs:8888", "bank-x", "ON", min_score=0.2)
    assert isinstance(m, HindsightMemory) and m.enabled
    assert m.bank_id == "bank-x" and m.min_score == 0.2 and m._c.base_url == "http://hs:8888"
    assert m._executor is not None
    m._executor.shutdown(wait=False)


# ── HindsightMemory ──────────────────────────────────────────────────────────────────────────

def _mem(client, **kw):
    return HindsightMemory(client, "bank", **kw)


def test_memory_ensure_bank_retried_until_success_then_cached():
    """REQ: 'Create the bank if needed. Retried on every call until it succeeds' (ensure_bank docstring)."""
    c = FakeHindsightClient(fail_create=1)
    m = _mem(c)
    m.recall({"fingerprints": []}, "q")
    m.recall({"fingerprints": []}, "q")
    m.recall({"fingerprints": []}, "q")
    assert [k for k, _ in c.calls].count("create_bank") == 2 and c.created == 1


def test_memory_recall_without_fingerprints_does_only_similarity_search():
    """REQ: exact recall by fingerprint tag PLUS similarity search (memory.py docstring); no fingerprints -> no tag query."""
    c = FakeHindsightClient()
    _mem(c).recall({"fingerprints": []}, "q")
    modes = [kw["tags_match"] for k, kw in c.calls if k == "recall"]
    assert modes == ["all_strict"]


def test_memory_recall_uses_every_fingerprint_tag():
    """REQ: exact recall over 'any' of the incident's fingerprints (memory.py docstring)."""
    c = FakeHindsightClient()
    _mem(c).recall(INC, "q")
    first = [kw for k, kw in c.calls if k == "recall"][0]
    assert first["tags"] == ["fp:fp-1", "fp:fp-2"] and first["tags_match"] == "any_strict"


def test_memory_recall_caps_results_and_ranks_by_score():
    """REQ: results above the cut-off, best first, at most max_results (memory.py docstring; HindsightMemory max_results)."""
    c = FakeHindsightClient(results=lambda tags, m: [hit(f"INC-{i}", 0.1 * i) for i in range(1, 7)])
    res = _mem(c, max_results=3).recall({"fingerprints": []}, "q")
    assert [r.incident_id for r in res.matches] == ["INC-6", "INC-5", "INC-4"]


def test_memory_recall_drops_items_below_min_score_or_without_scores():
    """REQ: results are kept only above a relevance cut-off on the FINAL score (memory.py docstring)."""
    c = FakeHindsightClient(results=lambda tags, m: [hit("A", 0.01), hit("B", 0.5),
                                                     hit("C", None), hit(None, 0.9)])
    res = _mem(c, min_score=0.05).recall({"fingerprints": []}, "q")
    assert [r.incident_id for r in res.matches] == ["B"]


def test_memory_recall_exact_beats_similar_for_same_document():
    """REQ: a document found by fingerprint is an exact match and ranks first (memory.py docstring; recall ranking)."""
    def results(tags, mode):
        if mode == "any_strict":
            return [hit("EXACT", 0.3, tags=["fix:flush_pool"])]
        return [hit("EXACT", 0.8), hit("OTHER", 0.9)]
    res = _mem(FakeHindsightClient(results=results)).recall(INC, "q")
    assert res.matches[0].incident_id == "EXACT" and res.matches[0].exact is True
    assert [r.incident_id for r in res.matches] == ["EXACT", "OTHER"]


def test_memory_recall_parses_fix_failed_and_source_tags():
    """REQ: procedural memory - which action worked and which did not (memory.py docstring) - comes back from tags."""
    tags = ["fix:flush_pool", "failed:restart_worker", "failed:rollback_config", "source:seed", "svc:checkout"]
    res = _mem(FakeHindsightClient(results=lambda t, m: [hit("INC-1", 0.9, tags=tags)])).recall({"fingerprints": []}, "q")
    r = res.matches[0]
    assert r.fix == "flush_pool" and r.failed == ["restart_worker", "rollback_config"] and r.source == "seed"


def test_memory_recall_excludes_self():
    """REQ: recall never returns the incident being diagnosed (exclude_id)."""
    res = _mem(FakeHindsightClient(results=lambda t, m: [hit("INC-0042", 0.9)])).recall(INC, "q", exclude_id="INC-0042")
    assert res.matches == []


def test_memory_recall_text_bounded():
    """REQ: recalled text is a bounded excerpt (Recalled.text capped at 400 chars)."""
    res = _mem(FakeHindsightClient(results=lambda t, m: [hit("I", 0.9, text="x" * 5000)])).recall({"fingerprints": []}, "q")
    assert len(res.matches[0].text) == 400


def test_memory_recall_fails_open_on_client_error():
    """REQ: 'Everything here fails open: if Hindsight is unreachable ... simply has no memory' (memory.py docstring)."""
    res = _mem(FakeHindsightClient(fail=True)).recall(INC, "q")
    assert res.degraded and res.matches == [] and res.error == "ConnectionError"


def test_memory_recalled_as_dict_rounds_score():
    """REQ: Recalled.as_dict is the persisted shape (score rounded, tags omitted)."""
    d = Recalled("I", "t", True, 0.123456, fix="f", failed=["x"], source="seed", tags=["a"]).as_dict()
    assert d == {"incident_id": "I", "text": "t", "exact": True, "score": 0.1235, "fix": "f", "failed": ["x"], "source": "seed"}


def test_memory_retain_without_fix_has_no_fix_tag_and_one_tag_per_failure():
    """REQ: procedural memory tags: fix:<action> only when one worked; failed:<action> for each that did not
    (memory.py docstring; OPERATIONS_RUNBOOK §4)."""
    c = FakeHindsightClient()
    out = _mem(c).retain(INC, "n", [], None, ["restart_worker", "flush_pool"], "live")
    tags = [kw for k, kw in c.calls if k == "retain"][0]["tags"]
    assert out.status == "retained"
    assert not any(t.startswith("fix:") for t in tags)
    assert "failed:restart_worker" in tags and "failed:flush_pool" in tags
    assert {"fp:fp-1", "fp:fp-2", "svc:checkout", "kind:incident"} <= set(tags)


def test_memory_retain_keyed_by_incident_with_metadata():
    """REQ: one Hindsight document per incident keyed by incident id, retained again replaces it (memory.py docstring)."""
    c = FakeHindsightClient()
    _mem(c).retain(INC, "narr", [], "flush_pool", [], "seed")
    kw = [kw for k, kw in c.calls if k == "retain"][0]
    assert kw["document_id"] == "INC-0042" and kw["update_mode"] == "replace"
    assert kw["metadata"] == {"incident_id": "INC-0042", "source": "seed"} and "source:seed" in kw["tags"]


def test_memory_retain_incident_without_fingerprints():
    """REQ: retain works for incidents with no fingerprints (chat-registered or seed) - fails open, never raises."""
    c = FakeHindsightClient()
    out = _mem(c).retain({"id": "I", "service": "s"}, "n", [], "x", [], "live")
    assert out.status == "retained"


def test_memory_retain_failure_reported_as_failed():
    """REQ: retain fails open and reports failure (memory.py docstring; RetainOutcome status 'failed')."""
    assert _mem(FakeHindsightClient(fail=True)).retain(INC, "n", [], None, []).status == "failed"


@pytest.mark.parametrize("fn,op", [(None, "op-1"), (lambda op: "completed", ""), (lambda op: 1 / 0, "op-1")])
def test_memory_operation_status_unknown_cases(fn, op):
    """REQ: operation_status returns 'unknown' when it cannot tell (no status fn, empty id, or error)."""
    assert _mem(FakeHindsightClient(), status_fn=fn).operation_status(op) == "unknown"


def test_memory_operation_status_reports_value():
    """REQ: operation_status returns the Hindsight status for the operation (status_fn injected)."""
    assert _mem(FakeHindsightClient(), status_fn=lambda op: "completed").operation_status("op") == "completed"


def test_memory_client_calls_run_on_dedicated_executor_thread():
    """REQ: 'Routing every client call through one dedicated, single-worker executor keeps the client on one thread'
    (HindsightMemory docstring)."""
    seen = []

    class ThreadClient(FakeHindsightClient):
        def create_bank(self, **kw):
            seen.append(threading.current_thread().name)

        def recall(self, *a, **kw):
            seen.append(threading.current_thread().name)
            return super().recall(*a, **kw)

        def retain(self, *a, **kw):
            seen.append(threading.current_thread().name)
            return super().retain(*a, **kw)

    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hs-test")
    try:
        m = HindsightMemory(ThreadClient(), "bank", executor=ex)
        m.recall(INC, "q")
        m.retain(INC, "n", [], None, [])
    finally:
        ex.shutdown(wait=True)
    assert seen and len(set(seen)) == 1 and seen[0].startswith("hs-test")
