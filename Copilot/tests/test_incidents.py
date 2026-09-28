from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.detect import Breach
from agent.incident_manager import IncidentConfig, make_fingerprints, process_cycle
from db import incidents

S = "todo-app"
T0 = 1_800_000_000_000
MIN = 60_000


def cfg(**kw):
    base = dict(recovery_windows=3, recurrence_window_ms=24 * 60 * MIN, renotify_ms=30 * MIN, max_open=5)
    base.update(kw)
    return IncidentConfig(**base)


def sig(route, kind):
    return "SimulatedError" if kind == "error_rate" else kind


def err(route="POST /todos", text=None):
    return Breach("error_rate", route, text or f"Error rate on '{route}' is 30%")


def lat(route="GET /todos"):
    return Breach("latency", route, f"p95 latency on '{route}' is 900ms")


def cycle(breaches, now, healthy=(), traffic_ok=True, c=None):
    return process_cycle(S, breaches, sig, set(healthy), traffic_ok, now, c or cfg())


# ── fingerprints ────────────────────────────────────────────────────────────────────────

def test_fingerprint_ignores_volatile_details_but_not_route_kind_or_signature():
    a = make_fingerprints(S, [err(text="Error rate 30% (9/30)")], sig)[0]["fp"]
    b = make_fingerprints(S, [err(text="Error rate 55% (40/72)")], sig)[0]["fp"]
    assert a == b
    assert make_fingerprints(S, [err("GET /todos")], sig)[0]["fp"] != a                # route
    assert make_fingerprints(S, [lat("POST /todos")], sig)[0]["fp"] != a               # kind
    assert make_fingerprints(S, [err()], lambda r, k: "PoolExhausted")[0]["fp"] != a   # exception signature
    assert make_fingerprints("other", [err()], sig)[0]["fp"] != a                      # service


def test_primary_fingerprint_is_the_highest_priority_breach():
    fps = make_fingerprints(S, [lat("A"), err("B"), Breach("keyword", None, "k")], sig)
    assert [f["kind"] for f in fps] == ["error_rate", "latency", "keyword"]


# ── dedupe (AC3) ────────────────────────────────────────────────────────────────────────

def test_ten_cycles_of_the_same_fault_make_exactly_one_incident():
    opened = 0
    for i in range(10):
        actions = cycle([err()], T0 + i * 15_000)
        opened += sum(a.kind == "opened" for a in actions)
    assert opened == 1
    active = incidents.list_active(S)
    assert len(active) == 1 and active[0]["breach_windows"] == 10 and active[0]["id"] == "INC-0001"


def test_same_problem_with_changing_numbers_stays_one_incident():
    cycle([err(text="30%")], T0)
    cycle([err(text="12%")], T0 + 15_000)
    assert len(incidents.list_active(S)) == 1


def test_distinct_fingerprints_in_one_cycle_share_one_incident():
    actions = cycle([err("A"), err("B"), lat("C")], T0)
    assert [a.kind for a in actions] == ["opened"]
    inc = incidents.get("INC-0001")
    assert {f["route"] for f in inc["fingerprints"]} == {"A", "B", "C"} and inc["severity"] == "critical"


def test_a_new_route_breaching_later_joins_only_if_it_overlaps():
    cycle([err("A")], T0)
    cycle([err("A"), err("B")], T0 + 1)          # overlaps on A: B is attached
    assert len(incidents.list_active(S)) == 1
    assert {f["route"] for f in incidents.get("INC-0001")["fingerprints"]} == {"A", "B"}
    actions = cycle([lat("Z")], T0 + 2)          # unrelated fingerprint: separate incident
    assert [a.kind for a in actions] == ["opened"] and len(incidents.list_active(S)) == 2


def test_no_breaches_and_no_incidents_does_nothing():
    assert cycle([], T0, healthy={"A"}) == [] and incidents.list_active(S) == []


# ── recovery and hysteresis ─────────────────────────────────────────────────────────────

def test_resolves_only_after_exactly_k_consecutive_healthy_windows():
    cycle([err()], T0)
    kinds = []
    for i in range(1, 4):
        kinds.append([a.kind for a in cycle([], T0 + i * 15_000, healthy={"POST /todos"})])
    assert kinds == [[], [], ["resolved"]]
    inc = incidents.get("INC-0001")
    assert inc["status"] == "RESOLVED" and inc["resolved_by"] == "auto" and inc["healthy_windows"] == 2


def test_a_breach_in_between_resets_the_healthy_streak():
    cycle([err()], T0)
    cycle([], T0 + 1, healthy={"POST /todos"})
    cycle([], T0 + 2, healthy={"POST /todos"})
    cycle([err()], T0 + 3)                                   # relapse
    assert incidents.get("INC-0001")["healthy_windows"] == 0
    for i in range(2):
        assert cycle([], T0 + 10 + i, healthy={"POST /todos"}) == []
    assert incidents.get("INC-0001")["status"] == "OPEN"


def test_unknown_traffic_is_not_healthy_and_does_not_reset_the_streak():
    cycle([err()], T0)
    cycle([], T0 + 1, healthy={"POST /todos"})               # streak 1
    cycle([], T0 + 2, healthy=set())                         # route has too little traffic: unknown
    cycle([], T0 + 3, healthy=set())
    assert incidents.get("INC-0001")["healthy_windows"] == 1 and incidents.get("INC-0001")["status"] == "OPEN"


def test_service_wide_incident_recovers_on_global_traffic():
    cycle([Breach("keyword", None, "3 events matched CRITICAL")], T0)
    assert cycle([], T0 + 1, traffic_ok=False) == []         # no traffic: unknown
    assert incidents.get("INC-0001")["healthy_windows"] == 0
    for i in range(3):
        acts = cycle([], T0 + 2 + i, traffic_ok=True)
    assert [a.kind for a in acts] == ["resolved"]


def test_multi_route_incident_needs_every_route_healthy():
    cycle([err("A"), err("B")], T0)
    for i in range(1, 6):
        cycle([], T0 + i, healthy={"A"})                     # B never proves healthy
    assert incidents.get("INC-0001")["status"] == "OPEN"
    for i in range(6, 9):
        cycle([], T0 + i, healthy={"A", "B"})
    assert incidents.get("INC-0001")["status"] == "RESOLVED"


# ── recurrence ──────────────────────────────────────────────────────────────────────────

def _open_and_resolve(now):
    cycle([err()], now)
    for i in range(1, 4):
        cycle([], now + i, healthy={"POST /todos"})


def test_recurrence_opens_a_new_linked_incident():
    _open_and_resolve(T0)
    actions = cycle([err()], T0 + 10 * MIN)
    assert [a.kind for a in actions] == ["opened"] and "INC-0001" in actions[0].detail
    assert incidents.get("INC-0002")["recurrence_of"] == "INC-0001"
    assert incidents.get("INC-0001")["status"] == "RESOLVED"        # history untouched, MTTR stays clean


def test_no_recurrence_link_outside_the_window():
    _open_and_resolve(T0)
    cycle([err()], T0 + 25 * 60 * MIN)
    assert incidents.get("INC-0002")["recurrence_of"] is None


def test_recurrence_requires_the_same_fingerprint():
    _open_and_resolve(T0)
    cycle([err("GET /other")], T0 + MIN)
    assert incidents.get("INC-0002")["recurrence_of"] is None


# ── reminders, regression, suppression, cap ─────────────────────────────────────────────

def test_reminder_only_after_the_interval_and_only_while_open():
    cycle([err()], T0)
    assert cycle([err()], T0 + 29 * MIN) == []
    assert [a.kind for a in cycle([err()], T0 + 31 * MIN)] == ["reminder"]
    incidents.mark_notified("INC-0001", T0 + 31 * MIN)
    assert cycle([err()], T0 + 40 * MIN) == []
    incidents.transition("INC-0001", "ACKED", None, "op", T0 + 41 * MIN)
    assert cycle([err()], T0 + 200 * MIN) == []              # acknowledged: no nagging


def test_breach_during_monitoring_regresses_the_incident():
    cycle([err()], T0)
    incidents.transition("INC-0001", "REMEDIATING", None, "op", T0 + 1)
    incidents.transition("INC-0001", "MONITORING", None, "op", T0 + 2)
    actions = cycle([err()], T0 + 3)
    assert [a.kind for a in actions] == ["regressed"] and incidents.get("INC-0001")["status"] == "OPEN"


def test_suppressed_incident_absorbs_breaches_silently_and_can_still_resolve():
    cycle([err()], T0)
    incidents.transition("INC-0001", "SUPPRESSED", None, "op", T0 + 1)
    assert cycle([err()], T0 + 40 * MIN) == []
    assert len(incidents.list_active(S)) == 1
    for i in range(3):
        cycle([], T0 + 50 * MIN + i, healthy={"POST /todos"})
    assert incidents.get("INC-0001")["status"] == "RESOLVED"


def test_incident_cap_attaches_instead_of_opening_more():
    c = cfg(max_open=2)
    cycle([err("A")], T0, c=c)
    cycle([lat("B")], T0 + 1, c=c)
    actions = cycle([err("C")], T0 + 2, c=c)
    assert [a.kind for a in actions] == ["capped"] and len(incidents.list_active(S)) == 2
    assert any(f["route"] == "C" for i in incidents.list_active(S) for f in i["fingerprints"])


def test_state_is_read_from_the_database_so_a_restart_changes_nothing():
    cycle([err()], T0)
    cycle([], T0 + 1, healthy={"POST /todos"})
    # "restart": nothing is held in memory between calls; the next cycle continues the streak
    cycle([], T0 + 2, healthy={"POST /todos"})
    assert incidents.get("INC-0001")["healthy_windows"] == 2


# ── state machine ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("frm,to", [
    ("RESOLVED", "OPEN"), ("RESOLVED", "ACKED"), ("OPEN", "MONITORING"), ("ACKED", "MONITORING"),
    ("MONITORING", "ACKED"), ("SUPPRESSED", "ACKED"), ("OPEN", "OPEN"),
])
def test_illegal_transitions_are_rejected(frm, to):
    cycle([err()], T0)
    _force(frm)
    with pytest.raises(incidents.InvalidTransition):
        incidents.transition("INC-0001", to, None, "op", T0 + 5)


def _force(status):
    path = {"OPEN": [], "ACKED": ["ACKED"], "REMEDIATING": ["REMEDIATING"], "MONITORING": ["REMEDIATING", "MONITORING"],
            "SUPPRESSED": ["SUPPRESSED"], "RESOLVED": ["RESOLVED"]}[status]
    for s in path:
        incidents.transition("INC-0001", s, None, "setup", T0 + 1)


def test_stale_version_is_a_conflict_and_changes_nothing():
    cycle([err()], T0)
    v = incidents.get("INC-0001")["version"]
    incidents.transition("INC-0001", "ACKED", v, "alice", T0 + 1)
    with pytest.raises(incidents.ConflictError):
        incidents.transition("INC-0001", "SUPPRESSED", v, "bob", T0 + 2)
    assert incidents.get("INC-0001")["status"] == "ACKED"


def test_two_operators_acting_at_once_exactly_one_wins():
    cycle([err()], T0)
    v = incidents.get("INC-0001")["version"]
    results = []

    def act(who):
        try:
            incidents.transition("INC-0001", "ACKED" if who == "a" else "SUPPRESSED", v, who, T0 + 1)
            return "won"
        except incidents.ConflictError:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(act, ["a", "b"]))
    assert sorted(results) == ["conflict", "won"]


def test_unknown_incident_is_not_found():
    with pytest.raises(incidents.NotFound):
        incidents.transition("INC-9999", "ACKED", None, "op", T0)


def test_resolved_is_terminal_and_records_who_resolved_it():
    cycle([err()], T0)
    incidents.transition("INC-0001", "RESOLVED", None, "alice", T0 + 5, "fixed by hand")
    inc = incidents.get("INC-0001")
    assert inc["status"] == "RESOLVED" and inc["resolved_by"] == "alice" and inc["resolved_at"] == T0 + 5
    with pytest.raises(incidents.InvalidTransition):
        incidents.transition("INC-0001", "OPEN", None, "alice", T0 + 6)


def test_timeline_records_every_change_with_the_actor():
    cycle([err()], T0)
    incidents.transition("INC-0001", "ACKED", None, "alice", T0 + 1)
    kinds = [(e["kind"], e["actor"]) for e in incidents.events("INC-0001")]
    assert kinds == [("opened", "watcher"), ("OPEN->ACKED", "alice")]


def test_version_increases_on_every_change():
    cycle([err()], T0)
    v1 = incidents.get("INC-0001")["version"]
    cycle([err()], T0 + 1)
    incidents.set_rca("INC-0001", "text", "ok")
    assert incidents.get("INC-0001")["version"] > v1 + 1
