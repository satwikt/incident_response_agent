import asyncio

import pytest

import faults
from faults import (
    Action,
    Cause,
    FIXES,
    FaultState,
    PoolExhausted,
    RouteKey,
    SimulatedDatabaseStateError,
    WorkerOutOfMemory,
)


@pytest.fixture
def st(monkeypatch):
    """A fresh state with deterministic, fast timings."""
    monkeypatch.setenv("FAULT_ERROR_RATE", "1.0")
    monkeypatch.setenv("POOL_SIZE", "3")
    monkeypatch.setenv("POOL_WAIT_S", "0")
    monkeypatch.setenv("MEM_LIMIT_MB", "5")
    monkeypatch.setenv("LATENCY_MIN_S", "0")
    monkeypatch.setenv("LATENCY_MAX_S", "0")
    return FaultState()


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(faults.asyncio, "sleep", fake_sleep)
    return slept


def run(coro):
    return asyncio.run(coro)


ROUTE = RouteKey.post_todos


# ── the cause/action matrix: only the right action fixes a cause ─────────────────────────

@pytest.mark.parametrize("cause", list(Cause))
@pytest.mark.parametrize("action", list(Action))
def test_only_the_matching_action_clears_a_cause(st, cause, action):
    st.inject(cause, ROUTE)
    st.apply_action(action)
    cleared = cause.value not in st.chaos_snapshot()
    assert cleared == (FIXES[cause] is action)


def test_every_cause_has_a_distinct_fix():
    assert set(FIXES) == set(Cause)
    assert len(set(FIXES.values())) == len(Cause)
    assert set(FIXES.values()) == set(Action)


# ── connection pool ─────────────────────────────────────────────────────────────────────

def test_pool_leak_exhausts_pool_for_every_route(st):
    st.inject(Cause.pool_exhausted, ROUTE)
    for _ in range(3):
        run(st.apply(ROUTE))  # each call leaks one slot
    assert st.pool_used == 3
    # Pool full: an unrelated route now fails with 503.
    with pytest.raises(PoolExhausted) as exc:
        run(st.apply(RouteKey.get_todos))
    assert exc.value.status_code == 503


def test_flush_pool_fixes_it_for_good(st):
    st.inject(Cause.pool_exhausted, ROUTE)
    for _ in range(3):
        run(st.apply(ROUTE))
    st.apply_action(Action.flush_pool)
    for _ in range(10):
        run(st.apply(ROUTE))  # no more leaking, no errors
    assert st.pool_used == 0


def test_restart_only_mitigates_pool_leak(st):
    st.inject(Cause.pool_exhausted, ROUTE)
    for _ in range(3):
        run(st.apply(ROUTE))
    st.apply_action(Action.restart_worker)
    assert st.pool_used == 0  # symptom gone...
    assert Cause.pool_exhausted.value in st.chaos_snapshot()  # ...cause still active
    for _ in range(3):
        run(st.apply(ROUTE))
    with pytest.raises(PoolExhausted):  # ...and it comes back
        run(st.apply(ROUTE))


# ── decoy pair: bad_config vs bad_deploy ─────────────────────────────────────────────────

def test_bad_config_and_bad_deploy_are_indistinguishable_by_error(st):
    errors = []
    for cause in (Cause.bad_config, Cause.bad_deploy):
        st.reset()
        st.inject(cause, ROUTE)
        with pytest.raises(SimulatedDatabaseStateError) as exc:
            run(st.apply(ROUTE))
        errors.append((type(exc.value), str(exc.value), exc.value.status_code))
    assert errors[0] == errors[1]


def test_decoy_pair_is_distinguishable_by_context(st):
    st.inject(Cause.bad_config, ROUTE)
    ctx = st.context()
    assert (ctx["release"], ctx["config_rev"]) == (faults.HEALTHY_RELEASE, faults.BAD_CONFIG_REV)
    st.reset()
    st.inject(Cause.bad_deploy, ROUTE)
    ctx = st.context()
    assert (ctx["release"], ctx["config_rev"]) == (faults.BAD_RELEASE, faults.HEALTHY_CONFIG_REV)


def test_rollbacks_restore_context_and_stop_errors(st):
    st.inject(Cause.bad_deploy, ROUTE)
    st.apply_action(Action.rollback_release)
    assert st.context()["release"] == faults.HEALTHY_RELEASE
    run(st.apply(ROUTE))  # no error
    st.inject(Cause.bad_config, ROUTE)
    st.apply_action(Action.rollback_config)
    assert st.context()["config_rev"] == faults.HEALTHY_CONFIG_REV
    run(st.apply(ROUTE))


def test_wrong_rollback_does_not_stop_errors(st):
    st.inject(Cause.bad_deploy, ROUTE)
    st.apply_action(Action.rollback_config)  # the decoy fix
    with pytest.raises(SimulatedDatabaseStateError):
        run(st.apply(ROUTE))
    assert st.context()["release"] == faults.BAD_RELEASE


def test_error_rate_zero_never_fails(monkeypatch):
    monkeypatch.setenv("FAULT_ERROR_RATE", "0")
    s = FaultState()
    s.inject(Cause.bad_config, ROUTE)
    for _ in range(50):
        run(s.apply(ROUTE))


# ── memory leak ─────────────────────────────────────────────────────────────────────────

def test_memory_leak_slows_then_kills_worker(st, no_real_sleep):
    st.inject(Cause.memory_leak, ROUTE)
    for _ in range(4):
        run(st.apply(ROUTE))
    assert st.leaked_mb == 4
    assert no_real_sleep and no_real_sleep == sorted(no_real_sleep)  # latency grows with the leak
    with pytest.raises(WorkerOutOfMemory):
        run(st.apply(ROUTE))  # limit is 5


def test_restart_worker_fixes_memory_leak(st):
    st.inject(Cause.memory_leak, ROUTE)
    for _ in range(4):
        run(st.apply(ROUTE))
    st.apply_action(Action.restart_worker)
    assert st.leaked_mb == 0
    run(st.apply(ROUTE))
    assert st.leaked_mb == 0  # cause cleared, no further leak


def test_flush_pool_does_not_help_memory_leak(st):
    st.inject(Cause.memory_leak, ROUTE)
    for _ in range(4):
        run(st.apply(ROUTE))
    st.apply_action(Action.flush_pool)
    assert st.leaked_mb == 4


# ── slow downstream ─────────────────────────────────────────────────────────────────────

def test_slow_downstream_sleeps_until_fallback_enabled(monkeypatch, no_real_sleep):
    monkeypatch.setenv("LATENCY_MIN_S", "2")
    monkeypatch.setenv("LATENCY_MAX_S", "2")
    s = FaultState()
    s.inject(Cause.slow_downstream, ROUTE)
    run(s.apply(ROUTE))
    assert no_real_sleep == [2.0]
    s.apply_action(Action.enable_fallback)
    run(s.apply(ROUTE))
    assert no_real_sleep == [2.0]  # no second sleep


# ── scoping, reset, bounds ──────────────────────────────────────────────────────────────

def test_cause_only_affects_its_route(st):
    st.inject(Cause.bad_config, ROUTE)
    run(st.apply(RouteKey.get_todos))  # other route is fine
    with pytest.raises(SimulatedDatabaseStateError):
        run(st.apply(ROUTE))


def test_clear_single_route_keeps_others(st):
    st.inject(Cause.bad_config, RouteKey.get_todos)
    st.inject(Cause.bad_config, RouteKey.post_todos)
    st.clear(Cause.bad_config, RouteKey.get_todos)
    assert st.chaos_snapshot() == {"bad_config": ["post_todos"]}
    st.clear(Cause.bad_config, RouteKey.post_todos)
    assert st.chaos_snapshot() == {}
    assert st.context()["config_rev"] == faults.HEALTHY_CONFIG_REV


def test_reset_restores_everything(st):
    st.inject(Cause.bad_deploy, ROUTE)
    st.inject(Cause.memory_leak, ROUTE)
    run(st.apply(RouteKey.get_todos))
    st.apply_action(Action.enable_fallback)
    st.reset()
    assert st.chaos_snapshot() == {}
    ctx = st.context()
    assert ctx["release"] == faults.HEALTHY_RELEASE and ctx["leaked_mb"] == 0 and not ctx["fallback_enabled"]
    assert st.journal == []


def test_journal_is_bounded_and_records_actor(st):
    for _ in range(300):
        st.apply_action(Action.flush_pool, actor="test")
    assert len(st.journal) == 200
    assert st.journal[-1]["actor"] == "test" and st.journal[-1]["action"] == "flush_pool"


def test_action_result_does_not_reveal_effect(st):
    st.inject(Cause.bad_config, ROUTE)
    helped = st.apply_action(Action.rollback_config)
    st.inject(Cause.bad_config, ROUTE)
    useless = st.apply_action(Action.flush_pool)
    assert helped == {"action": "rollback_config", "applied": True}
    assert useless == {"action": "flush_pool", "applied": True}


def test_invalid_env_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("POOL_SIZE", "not-a-number")
    monkeypatch.setenv("FAULT_ERROR_RATE", "7")  # clamped
    s = FaultState()
    assert s.pool_size == 10
    assert s.error_rate == 1.0


def test_only_restart_and_reset_touch_worker_uptime(st):
    before = st.worker_started_at
    st.worker_started_at = before - 100
    for action in (Action.flush_pool, Action.rollback_config, Action.rollback_release, Action.enable_fallback):
        st.apply_action(action)
    assert st.worker_started_at == before - 100
    st.apply_action(Action.restart_worker)
    assert st.worker_started_at > before - 100
