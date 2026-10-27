"""Regression tests for the fixes found during the hands-on demo run:

* evidence of an incident is not overwritten by later cycles (the memory narrative kept a post-fix '50%');
* a breach seen right after a resolve is the tail of the old incident, not a new one (INC-0004 false alarm);
* a repeat with a different failing HTTP status is not a "verified match" (INC-0003 said match against a 500 incident).
"""

from agent import cycle
from agent.detect import Breach
from agent.incident_manager import IncidentConfig, process_cycle
from db import incidents

S = "checkout"
T0 = 1_800_000_000_000
MIN = 60_000


def cfg(**kw):
    base = dict(recovery_windows=3, recurrence_window_ms=24 * 60 * MIN, renotify_ms=30 * MIN, max_open=5)
    base.update(kw)
    return IncidentConfig(**base)


def sig(route, kind):
    return "5xx" if kind == "error_rate" else kind


def err(pct, route="POST /checkout"):
    return Breach("error_rate", route, f"Error rate on '{route}' is {pct}.0% ({pct // 10}/10 requests, threshold 10.0%) [window 1m]")


def run(breaches, now, c=None, healthy=(), traffic_ok=True):
    return process_cycle(S, breaches, sig, set(healthy), traffic_ok, now, c or cfg())


# ── evidence is kept ────────────────────────────────────────────────────────────────────

def test_evidence_keeps_the_opening_line_when_the_same_breach_is_seen_again():
    opened = run([err(100)], T0)[0].incident
    run([err(50)], T0 + MIN)
    run([err(20)], T0 + 2 * MIN)
    assert incidents.get(opened["id"])["evidence"] == [err(100).text]


def test_evidence_appends_a_breach_of_a_new_route():
    opened = run([err(100)], T0)[0].incident
    run([err(100), err(60, "GET /cart")], T0 + MIN)
    ev = incidents.get(opened["id"])["evidence"]
    assert ev[0] == err(100).text and len(ev) == 2 and "GET /cart" in ev[1]


# ── the tail of a resolved incident ────────────────────────────────────────────────────

def _resolve_after_breach():
    opened = run([err(100)], T0)[0].incident
    incidents.transition(opened["id"], "RESOLVED", None, "operator", T0 + MIN, "fix worked")
    return opened["id"]


def test_breach_right_after_resolve_does_not_open_a_new_incident():
    _resolve_after_breach()
    actions = run([err(50)], T0 + MIN + 23_000, cfg(resolve_grace_ms=90_000))
    assert not any(a.kind == "opened" for a in actions)
    assert [i["id"] for i in incidents.list_active(S)] == []


def test_breach_still_there_after_the_grace_period_opens_a_recurrence():
    old = _resolve_after_breach()
    actions = run([err(100)], T0 + MIN + 120_000, cfg(resolve_grace_ms=90_000))
    opened = [a for a in actions if a.kind == "opened"]
    assert len(opened) == 1
    assert incidents.get(opened[0].incident["id"])["recurrence_of"] == old


def test_grace_is_off_by_default_so_existing_behaviour_is_unchanged():
    _resolve_after_breach()
    assert any(a.kind == "opened" for a in run([err(100)], T0 + MIN + 1_000))


def test_grace_does_not_hide_a_different_problem():
    _resolve_after_breach()
    other = Breach("latency", "GET /cart", "p95 latency on 'GET /cart' is 900ms")
    assert any(a.kind == "opened" for a in run([other], T0 + MIN + 1_000, cfg(resolve_grace_ms=90_000)))


# ── a different failing status is not a verified match ────────────────────────────────────

def _past_incident_with_status(status_detail):
    inc = run([err(100)], T0)[0].incident
    if status_detail:
        incidents.record_event(inc["id"], "context:status", "watcher", status_detail, T0)
    return {"incident_id": inc["id"], "exact": True, "fix": "disable_fault"}


def test_same_status_stays_a_match():
    assert cycle._same_failure_mode(_past_incident_with_status("error status 500"), 500)


def test_different_status_is_not_a_match():
    assert not cycle._same_failure_mode(_past_incident_with_status("error status 500"), 503)


def test_old_incident_without_a_recorded_status_keeps_the_match():
    assert cycle._same_failure_mode(_past_incident_with_status(""), 503)


def test_no_current_status_keeps_the_match():
    assert cycle._same_failure_mode(_past_incident_with_status("error status 500"), None)


# ── query agent answers ───────────────────────────────────────────────────────────────────

def test_query_prompt_caps_error_lines_and_dedupes_companion_lines():
    from agent.query_agent import QUERY_SYSTEM_INSTRUCTION as p
    assert "at most 10 lines" in p and "companion cause line" in p and "(+N more in this window)" in p


def test_query_prompt_does_not_infer_another_services_health():
    from agent.query_agent import QUERY_SYSTEM_INSTRUCTION as p
    assert "No direct health data is available" in p and "reports only the monitored service" in p
