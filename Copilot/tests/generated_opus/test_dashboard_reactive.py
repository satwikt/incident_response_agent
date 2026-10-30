"""api/dashboard.py: incident list/detail, memory view, telemetry overview, allowed actions and fix feedback."""

from types import SimpleNamespace as NS

import pytest

import api.dashboard as dashboard
from agent import config, cycle, tools
from agent.memory import HindsightMemory, Recalled
from db import incidents
from tests.generated_opus.conftest import FakeHindsightClient, FakeMemory

# captured before the autouse fixture stubs it on the module
from api.dashboard import _llm_suggest_next_action as REAL_SUGGEST  # noqa: E402

T0 = 1_700_000_000_000
FP = [{"fp": "fp-a", "kind": "error_rate", "route": "POST /checkout", "sig": "PaymentGatewayException"}]


def mk(service="checkout", proposed=None, rca=None, context=None, evidence=None):
    inc = incidents.create(service, FP, "critical", evidence or ["5xx on POST /checkout"], T0)
    fields = {}
    if proposed:
        fields["proposed_action"] = proposed
    if context:
        fields["context_sample"] = context
    if fields:
        incidents.update_fields(inc["id"], **fields)
    if rca:
        incidents.set_rca(inc["id"], rca, "ok")
    return inc["id"]


def feedback(client, inc_id, action, worked, notes=None):
    body = {"action": action, "worked": worked}
    if notes is not None:
        body["notes"] = notes
    return client.post(f"/api/incidents/{inc_id}/feedback", json=body)


@pytest.fixture
def mem(monkeypatch):
    m = FakeMemory()
    monkeypatch.setattr(cycle, "_memory", m)
    return m


# ── /api/incidents ───────────────────────────────────────────────────────────────────────────

def test_dashboard_incidents_newest_first_and_limit(dash_client):
    """REQ: 'List recent and active incidents' with a max count (get_incidents docstring; limit query param)."""
    ids = [mk() for _ in range(3)]
    data = dash_client.get("/api/incidents").json()
    assert [d["id"] for d in data] == list(reversed(ids))
    assert [d["id"] for d in dash_client.get("/api/incidents?limit=1").json()] == [ids[-1]]


@pytest.mark.parametrize("limit", [0, 501, -1])
def test_dashboard_incidents_limit_out_of_range_is_422(dash_client, limit):
    """REQ: limit must be between 1 and 500 (Query(ge=1, le=500))."""
    assert dash_client.get(f"/api/incidents?limit={limit}").status_code == 422


def test_dashboard_incidents_combined_filters(dash_client):
    """REQ: optional status AND service filters (get_incidents docstring)."""
    a = mk("checkout")
    mk("catalog")
    incidents.transition(a, "RESOLVED", None, "t", T0 + 1)
    mk("checkout")
    data = dash_client.get("/api/incidents?service=checkout&status=RESOLVED").json()
    assert [d["id"] for d in data] == [a]


def test_dashboard_incidents_summary_carries_actions_and_proposal(dash_client):
    """REQ: summary shows diagnosis, proposed action and actions taken (IncidentSummary schema)."""
    i = mk(proposed="flush_pool", rca="Root cause: pool")
    feedback(dash_client, i, "restart_worker", False)
    row = dash_client.get("/api/incidents").json()[0]
    assert row["rca_text"] == "Root cause: pool" and row["rca_status"] == "ok"
    assert row["actions"][0]["action"] == "restart_worker" and row["actions"][0]["worked"] is False


def test_dashboard_incident_detail_has_dossier_fields(dash_client):
    """REQ: 'full incident dossier including timeline events and diagnostic context' (get_incident_detail docstring)."""
    i = mk(context="release=v2.4.0 config_rev=cfg-r41")
    incidents.transition(i, "ACKED", None, "oncall", T0 + 5)
    d = dash_client.get(f"/api/incidents/{i}").json()
    assert d["fingerprints"] == FP and d["context_sample"] == "release=v2.4.0 config_rev=cfg-r41"
    assert [e["kind"] for e in d["timeline"]] == ["opened", "OPEN->ACKED"]
    assert d["timeline"][1]["actor"] == "oncall"


# ── /api/memory/past-incidents ───────────────────────────────────────────────────────────────

def _resolved(service="checkout", actions=None, proposed=None):
    inc = incidents.insert_resolved(service, FP, "critical", ["5xx"], T0, T0 + 60_000, "Root cause: gw",
                                    actions or [], "release=v1", source="seed")
    if proposed:
        incidents.update_fields(inc["id"], proposed_action=proposed)
    return inc["id"]


def test_dashboard_memory_off_lists_only_resolved_local_incidents(dash_client):
    """REQ: memories are historical resolved incidents (get_past_incident_memories docstring 'local database fallback')."""
    r = _resolved(actions=[{"action": "flush_pool"}])
    mk()  # open incident: not a memory
    data = dash_client.get("/api/memory/past-incidents").json()
    assert data["enabled"] is False and data["bank_id"] == config.HINDSIGHT_BANK
    assert [m["incident_id"] for m in data["memories"]] == [r] and data["total"] == 1
    assert data["memories"][0]["fix"] == "flush_pool" and "fix:flush_pool" in data["memories"][0]["tags"]


def test_dashboard_memory_proposed_action_used_when_no_actions(dash_client):
    """REQ: memory view shows the verified fix; without logged actions the incident's proposed action is shown."""
    r = _resolved(proposed="rollback_release")
    m = dash_client.get("/api/memory/past-incidents").json()["memories"][0]
    assert m["incident_id"] == r and m["fix"] == "rollback_release"


def test_dashboard_memory_hindsight_matches_merged_without_duplicates(dash_client, mem):
    """REQ: 'Return historical memories and verified fixes stored in Hindsight (or local database fallback)'
    (docstring); the same incident is not listed twice."""
    r = _resolved(actions=[{"action": "flush_pool"}])
    mem.matches = [Recalled(r, "narr from hindsight", True, 0.8, fix="flush_pool", failed=[], source="live",
                            tags=["fix:flush_pool"]),
                   Recalled("INC-0999", "remote only", False, 0.4, fix="enable_fallback", failed=["restart_worker"],
                            source="seed", tags=["source:seed"])]
    data = dash_client.get("/api/memory/past-incidents").json()
    ids = [m["incident_id"] for m in data["memories"]]
    assert data["enabled"] is True and sorted(ids) == sorted([r, "INC-0999"]) and data["total"] == 2
    hs = next(m for m in data["memories"] if m["incident_id"] == r)
    assert hs["narrative"] == "narr from hindsight" and hs["score"] == 0.8
    remote = next(m for m in data["memories"] if m["incident_id"] == "INC-0999")
    assert remote["failed"] == ["restart_worker"] and remote["source"] == "seed"


def test_dashboard_memory_hindsight_match_without_fix_uses_local_actions(dash_client, mem):
    """REQ: verified fixes are shown; a Hindsight memory without a fix tag falls back to the local record."""
    r = _resolved(actions=[{"action": "restart_worker"}, {"action": "flush_pool"}])
    mem.matches = [Recalled(r, "n", False, 0.5)]
    m = dash_client.get("/api/memory/past-incidents").json()["memories"][0]
    assert m["fix"] == "flush_pool" and m["failed"] == ["restart_worker"]


def test_dashboard_memory_hindsight_error_falls_back_to_db(dash_client, mem):
    """REQ: Hindsight failures fall back to the database (get_past_incident_memories; memory.py 'fails open')."""
    mem.recall_exc = ConnectionError("down")
    r = _resolved(actions=[{"action": "flush_pool"}])
    res = dash_client.get("/api/memory/past-incidents")
    assert res.status_code == 200 and [m["incident_id"] for m in res.json()["memories"]] == [r]


def test_dashboard_memory_explicitly_failed_action_not_shown_as_fix(dash_client):
    """REQ: memory view shows 'verified fixes' (docstring); an action the operator marked worked=False is never the fix
    (split_actions docstring)."""
    _resolved(actions=[{"action": "restart_worker", "worked": False, "actor": "operator"}])
    m = dash_client.get("/api/memory/past-incidents").json()["memories"][0]
    assert m["fix"] != "restart_worker"


# ── /api/telemetry/overview ──────────────────────────────────────────────────────────────────

@pytest.fixture
def stub_tools(monkeypatch):
    seen = {}

    def error_rate(minutes):
        seen["er"] = minutes
        return {"routes": []}

    def latency(minutes):
        seen["lat"] = minutes
        return {"routes": []}

    monkeypatch.setattr(tools, "get_service_health", lambda: {"status": "up"})
    monkeypatch.setattr(tools, "get_error_rate", error_rate)
    monkeypatch.setattr(tools, "get_latency", latency)
    return seen


def test_dashboard_overview_defaults_to_monitored_service(dash_client, stub_tools):
    """REQ: service 'defaults to config.SERVICE_NAME' (Query description)."""
    d = dash_client.get("/api/telemetry/overview").json()
    assert d["service"] == config.SERVICE_NAME and d["health"] == {"status": "up"}
    assert stub_tools == {"er": 5.0, "lat": 5.0}


def test_dashboard_overview_counts_active_and_total_for_service(dash_client, stub_tools, monkeypatch):
    """REQ: snapshot includes active incidents for the service (get_telemetry_overview docstring). A service other than the
    monitored one is read from the event store for THAT service (B20), not from the monitored service's tools."""
    import api.dashboard as dashboard
    seen = {}
    monkeypatch.setattr(dashboard.store, "health", lambda svc: {"service": svc, "status": "up"})
    monkeypatch.setattr(dashboard.store, "error_rate", lambda svc, m: seen.update(er=m, svc=svc) or {"by_route": []})
    monkeypatch.setattr(dashboard.store, "latency", lambda svc, m: {"by_route": []})
    a = mk("checkout")
    mk("checkout")
    mk("catalog")
    incidents.transition(a, "RESOLVED", None, "t", T0 + 1)
    d = dash_client.get("/api/telemetry/overview?service=checkout&window_minutes=15").json()
    assert d["active_incidents"] == 1 and d["total_incidents"] == 2
    assert seen == {"er": 15.0, "svc": "checkout"} and d["health"]["service"] == "checkout"
    assert "er" not in stub_tools


@pytest.mark.parametrize("w", [0.5, 61, 0])
def test_dashboard_overview_window_bounds_422(dash_client, stub_tools, w):
    """REQ: window_minutes must be within 1..60 (Query ge=1.0, le=60.0)."""
    assert dash_client.get(f"/api/telemetry/overview?window_minutes={w}").status_code == 422


# ── /api/meta/allowed-actions ────────────────────────────────────────────────────────────────

def test_dashboard_allowed_actions_sorted_unique_superset(dash_client):
    """REQ: 'Return the list of remediation actions the server accepts for incident feedback' (docstring)."""
    acts = dash_client.get("/api/meta/allowed-actions").json()["allowed_actions"]
    assert acts == sorted(set(acts)) and set(config.ALLOWED_ACTIONS) <= set(acts)


def test_dashboard_every_listed_action_is_accepted_by_feedback(dash_client):
    """REQ: allowed-actions lists exactly what the feedback endpoint accepts (get_allowed_actions docstring)."""
    for action in dash_client.get("/api/meta/allowed-actions").json()["allowed_actions"]:
        i = mk()
        r = feedback(dash_client, i, action, True)
        assert r.status_code == 200, (action, r.text)


# ── POST /api/incidents/{id}/feedback : validation ───────────────────────────────────────────

@pytest.mark.parametrize("action", ["", "   ", "flush pool", "flush_pool;rm", "../etc"])
def test_feedback_malformed_action_is_400(dash_client, action):
    """REQ: action must be a valid action identifier (submit_incident_feedback)."""
    i = mk()
    assert feedback(dash_client, i, action, True).status_code == 400
    assert incidents.get(i)["actions"] == []


def test_feedback_padded_action_is_trimmed(dash_client):
    """REQ: operator input is normalised (action stripped) before validation (submit_incident_feedback)."""
    i = mk()
    r = feedback(dash_client, i, "  flush_pool  ", True)
    assert r.status_code == 200 and r.json()["action"] == "flush_pool"


@pytest.mark.parametrize("alias", ["restart_service", "reboot_worker"])
def test_feedback_aliases_normalised_to_restart_worker(dash_client, alias):
    """REQ: common operator action aliases are normalised (submit_incident_feedback alias table)."""
    assert feedback(dash_client, mk(), alias, False).json()["action"] == "restart_worker"


def test_feedback_accepts_incident_specific_proposed_action(dash_client):
    """REQ: the action the diagnosis proposed for this incident is accepted even if dynamic (SYSTEM_INSTRUCTION allows
    'any dynamic snake_case action name'; feedback adds proposed_action to the allowed set)."""
    i = mk(proposed="drain_queue")
    assert feedback(dash_client, i, "drain_queue", True).status_code == 200


def test_feedback_missing_worked_is_422(dash_client):
    """REQ: IncidentFeedbackRequest requires 'worked' (api/schema.py)."""
    i = mk()
    assert dash_client.post(f"/api/incidents/{i}/feedback", json={"action": "flush_pool"}).status_code == 422


def test_feedback_notes_none_and_whitespace_recorded_as_empty(dash_client):
    """REQ: action record stores trimmed notes (submit_incident_feedback action_record)."""
    i = mk()
    feedback(dash_client, i, "flush_pool", False)
    feedback(dash_client, i, "restart_worker", False, notes="   ")
    assert [a["notes"] for a in incidents.get(i)["actions"]] == ["", ""]
    assert all(a["actor"] == "operator" for a in incidents.get(i)["actions"])


# ── feedback: worked=True ────────────────────────────────────────────────────────────────────

def test_feedback_worked_resolves_and_retains_verified_fix(dash_client, mem):
    """REQ: 'Fix Worked: Marks incident as RESOLVED and writes a verified procedure tag fix:<action> into Hindsight memory'
    (OPERATIONS_RUNBOOK §4)."""
    i = mk(rca="Root cause: pool", context="release=v1")
    r = feedback(dash_client, i, "flush_pool", True, notes="cleared").json()
    assert r["status"] == "RESOLVED" and r["memory_retained"] is True and r["new_recommendation"] is None
    call = mem.retained[-1]
    assert call["fix"] == "flush_pool" and call["failed"] == []
    assert "Fix that worked: flush_pool." in call["narrative"] and "release=v1" in call["narrative"]
    inc = incidents.get(i)
    assert inc["status"] == "RESOLVED" and inc["resolved_by"] == "operator"
    assert inc["memory_status"] == "retained" and inc["memory_op"] == "op-1" and inc["proposed_action"] == "flush_pool"
    last = incidents.list_events(i)[-1]
    assert last["kind"] == "OPEN->RESOLVED" and "Verified fix: flush_pool" in last["detail"] and "cleared" in last["detail"]


def test_feedback_worked_after_failures_retains_negative_memory(dash_client, mem):
    """REQ: procedural memory - which action worked and which did not (negative memory) (memory.py docstring)."""
    i = mk()
    feedback(dash_client, i, "restart_worker", False)
    feedback(dash_client, i, "flush_pool", True)
    call = mem.retained[-1]
    assert call["fix"] == "flush_pool" and call["failed"] == ["restart_worker"]
    assert "These did NOT resolve it: restart_worker." in call["narrative"]


def test_feedback_worked_fix_not_also_listed_as_failed(dash_client, mem):
    """REQ: the fix that worked and the actions that did NOT work are disjoint (memory.py docstring 'which one worked, and
    which did not'; split_actions removes the fix from failed). Same action failed once, then worked on retry."""
    i = mk()
    feedback(dash_client, i, "flush_pool", False)
    feedback(dash_client, i, "flush_pool", True)
    call = mem.retained[-1]
    assert call["fix"] == "flush_pool" and "flush_pool" not in call["failed"]


def test_feedback_worked_hindsight_tags(dash_client, monkeypatch):
    """REQ: Fix Worked writes fix:<action> into Hindsight (OPERATIONS_RUNBOOK §4) - verified through HindsightMemory tags."""
    c = FakeHindsightClient()
    monkeypatch.setattr(cycle, "_memory", HindsightMemory(c, "bank"))
    i = mk()
    feedback(dash_client, i, "rollback_release", True)
    tags = [kw for k, kw in c.calls if k == "retain"][-1]["tags"]
    assert "fix:rollback_release" in tags and "outcome:resolved" in tags


def test_feedback_worked_memory_off_still_resolves(dash_client):
    """REQ: memory fails open / can be off; resolution never depends on it (memory.py module docstring)."""
    i = mk()
    r = feedback(dash_client, i, "flush_pool", True).json()
    assert r["status"] == "RESOLVED" and r["memory_retained"] is False
    assert incidents.get(i)["memory_status"] == "skipped"


def test_feedback_worked_memory_failure_still_resolves(dash_client, mem):
    """REQ: retain failures are reported, never block resolution (memory.py 'fails open')."""
    mem.retain_status = "failed"
    i = mk()
    r = feedback(dash_client, i, "flush_pool", True).json()
    assert r["status"] == "RESOLVED" and r["memory_retained"] is False
    assert incidents.get(i)["memory_status"] == "failed"


def test_feedback_worked_on_resolved_incident_is_idempotent(dash_client, mem):
    """REQ: RESOLVED is terminal (db/incidents.py docstring) - repeat 'Fix Worked' does not add a state transition."""
    i = mk()
    feedback(dash_client, i, "flush_pool", True)
    n_events = len(incidents.list_events(i))
    r = feedback(dash_client, i, "flush_pool", True).json()
    assert r["status"] == "RESOLVED" and len(incidents.list_events(i)) == n_events


@pytest.mark.parametrize("prior", ["ACKED", "REMEDIATING", "MONITORING"])
def test_feedback_worked_resolves_from_any_active_state(dash_client, mem, prior):
    """REQ: Fix Worked marks the incident RESOLVED (OPERATIONS_RUNBOOK §4); all active states may move to RESOLVED
    (db/incidents.TRANSITIONS)."""
    i = mk()
    path = {"ACKED": ["ACKED"], "REMEDIATING": ["REMEDIATING"], "MONITORING": ["REMEDIATING", "MONITORING"]}[prior]
    for s in path:
        incidents.transition(i, s, None, "t", T0 + 1)
    assert feedback(dash_client, i, "flush_pool", True).json()["status"] == "RESOLVED"
    assert incidents.get(i)["status"] == "RESOLVED"


# ── feedback: worked=False ───────────────────────────────────────────────────────────────────

def test_feedback_failed_open_moves_to_remediating_and_retains_failure(dash_client, mem):
    """REQ: 'Fix Failed: Marks incident as REMEDIATING and writes failed:<action> into Hindsight memory to prompt an
    alternate fix' (OPERATIONS_RUNBOOK §4)."""
    i = mk()
    r = feedback(dash_client, i, "restart_worker", False, notes="still 503").json()
    assert r["status"] == "REMEDIATING" and r["worked"] is False
    assert incidents.get(i)["status"] == "REMEDIATING"
    call = mem.retained[-1]
    assert call["fix"] is None and call["failed"] == ["restart_worker"]
    last = incidents.list_events(i)[-1]
    assert last["kind"] == "OPEN->REMEDIATING" and last["actor"] == "operator" and "still 503" in last["detail"]


def test_feedback_failed_from_acked_moves_to_remediating(dash_client, mem):
    """REQ: 'Fix Failed: Marks incident as REMEDIATING' (OPERATIONS_RUNBOOK §4); ACKED -> REMEDIATING is a legal
    transition (db/incidents.TRANSITIONS). An acknowledged incident whose fix failed must be REMEDIATING."""
    i = mk()
    incidents.transition(i, "ACKED", None, "oncall", T0 + 1)
    r = feedback(dash_client, i, "restart_worker", False).json()
    assert r["status"] == "REMEDIATING" and incidents.get(i)["status"] == "REMEDIATING"


def test_feedback_failed_while_remediating_stays_remediating(dash_client, mem):
    """REQ: Fix Failed keeps the incident active in REMEDIATING (OPERATIONS_RUNBOOK §4)."""
    i = mk()
    feedback(dash_client, i, "restart_worker", False)
    r = feedback(dash_client, i, "flush_pool", False).json()
    assert r["status"] == "REMEDIATING" and len(incidents.get(i)["actions"]) == 2


def test_feedback_failed_hindsight_tags_failed_action(dash_client, monkeypatch):
    """REQ: Fix Failed writes failed:<action> into Hindsight (OPERATIONS_RUNBOOK §4)."""
    c = FakeHindsightClient()
    monkeypatch.setattr(cycle, "_memory", HindsightMemory(c, "bank"))
    i = mk()
    feedback(dash_client, i, "restart_worker", False)
    tags = [kw for k, kw in c.calls if k == "retain"][-1]["tags"]
    assert "failed:restart_worker" in tags and not any(t.startswith("fix:") for t in tags)


def test_feedback_failed_memory_not_marked_resolved(dash_client, monkeypatch):
    """REQ: Hindsight stores 'one document per RESOLVED incident' (memory.py module docstring); a Fix Failed on a still
    active incident must not be written with the 'outcome:resolved' tag (it would be recalled as resolved precedent)."""
    c = FakeHindsightClient()
    monkeypatch.setattr(cycle, "_memory", HindsightMemory(c, "bank"))
    i = mk()
    feedback(dash_client, i, "restart_worker", False)
    tags = [kw for k, kw in c.calls if k == "retain"][-1]["tags"]
    assert "outcome:resolved" not in tags


def test_feedback_failed_uses_model_suggestion(dash_client, mem, monkeypatch):
    """REQ: 'Ask the model to suggest the next action based on the original diagnosis and what has failed'
    (submit_incident_feedback comment); the suggestion becomes the incident's proposed action."""
    calls = []
    monkeypatch.setattr(dashboard, "_llm_suggest_next_action",
                        lambda rca_text, failed_actions, service: calls.append((rca_text, failed_actions, service))
                        or "enable_fallback")
    i = mk(rca="Root cause: payment gateway down")
    r = feedback(dash_client, i, "restart_worker", False).json()
    assert r["new_recommendation"] == "enable_fallback" and "enable_fallback" in r["message"]
    assert calls == [("Root cause: payment gateway down", ["restart_worker"], "checkout")]
    inc = incidents.get(i)
    assert inc["proposed_action"] == "enable_fallback" and "restart_worker failed" in inc["memory_detail"]


def test_feedback_failed_never_recommends_an_action_that_already_failed(dash_client, mem, monkeypatch):
    """REQ: next recommendation must be an ALTERNATE fix (OPERATIONS_RUNBOOK §4 'prompt an alternate fix');
    build_next_action_prompt: 'Do NOT repeat a failed action'; the offline fallback already excludes failed actions."""
    monkeypatch.setattr(dashboard, "_llm_suggest_next_action", lambda **kw: "restart_worker")
    i = mk()
    r = feedback(dash_client, i, "restart_worker", False).json()
    assert r["new_recommendation"] != "restart_worker"


def test_feedback_failed_offline_fallback_skips_all_failed_actions(dash_client, mem):
    """REQ: without the model, the recommendation is an allow-listed action not yet failed (submit_incident_feedback
    fallback)."""
    i = mk()
    feedback(dash_client, i, config.ALLOWED_ACTIONS[0], False)
    r = feedback(dash_client, i, config.ALLOWED_ACTIONS[1], False).json()
    rec = r["new_recommendation"]
    assert rec in config.ALLOWED_ACTIONS and rec not in config.ALLOWED_ACTIONS[:2]
    assert incidents.get(i)["proposed_action"] == rec


def test_feedback_failed_all_actions_exhausted_escalates(dash_client, mem, monkeypatch):
    """REQ: when every allowed action has failed the copilot escalates to a human (submit_incident_feedback fallback)."""
    monkeypatch.setattr(config, "ALLOWED_ACTIONS", ("flush_pool", "noop"))
    i = mk()
    r = feedback(dash_client, i, "flush_pool", False).json()
    assert r["new_recommendation"] == "escalate_to_human_sre"


def test_feedback_failed_on_resolved_incident_is_rejected(dash_client, mem):
    """REQ: RESOLVED is terminal (db/incidents.py docstring) - a late 'Fix Failed' cannot reopen it or overwrite its verified
    memory (B18): 409, nothing recorded, nothing retained."""
    i = mk()
    feedback(dash_client, i, "flush_pool", True)
    retained_before = len(mem.retained)
    r = feedback(dash_client, i, "restart_worker", False)
    assert r.status_code == 409
    assert incidents.get(i)["status"] == "RESOLVED" and len(mem.retained) == retained_before
    assert [a["action"] for a in incidents.get(i)["actions"]] == ["flush_pool"]


# ── _llm_suggest_next_action (litellm faked) ─────────────────────────────────────────────────

def _completion_returning(content, seen):
    def completion(**kw):
        seen.append(kw)
        return NS(choices=[NS(message=NS(content=content))])
    return completion


def test_suggest_next_action_parses_model_reply(monkeypatch):
    """REQ: returns 'the model-suggested action name' using the Groq model synchronously (docstring)."""
    import litellm
    seen = []
    monkeypatch.setattr(litellm, "completion", _completion_returning("Proposed action: enable_fallback", seen))
    assert REAL_SUGGEST("Root cause: gateway", ["restart_worker"], "checkout") == "enable_fallback"
    kw = seen[0]
    from agent.llm import get_groq_fallback_model
    assert kw["model"] == get_groq_fallback_model()
    prompt = kw["messages"][0]["content"]
    assert "restart_worker" in prompt and "checkout" in prompt and "Root cause: gateway" in prompt
    assert "num_retries" not in kw and "fallbacks" not in kw


@pytest.mark.parametrize("content", ["Proposed action: none", "I am not sure.", None, ""])
def test_suggest_next_action_no_usable_answer_is_none(monkeypatch, content):
    """REQ: returns None when the model gives no action (docstring 'or None')."""
    import litellm
    monkeypatch.setattr(litellm, "completion", _completion_returning(content, []))
    assert REAL_SUGGEST("r", [], "s") is None


def test_suggest_next_action_error_is_none(monkeypatch):
    """REQ: 'Returns ... None on any error' (_llm_suggest_next_action docstring)."""
    import litellm

    def boom(**kw):
        raise TimeoutError("groq")
    monkeypatch.setattr(litellm, "completion", boom)
    assert REAL_SUGGEST("r", ["x"], "s") is None


@pytest.mark.skip(reason="AMBIGUOUS: _llm_suggest_next_action accepts ANY snake_case name (comment: 'not just ALLOWED_ACTIONS'), "
                         "while config.py says 'Only these actions can ever be proposed' and ARCHITECTURE §2.2 says fixes are "
                         "proposed 'strictly from ALLOWED_ACTIONS'; prompts/SYSTEM_INSTRUCTION allow dynamic names.")
def test_suggest_next_action_restricted_to_allow_list():
    """REQ: conflicting sources."""
