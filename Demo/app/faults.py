"""Incident scenarios for the demo app.

Two deliberately separate concerns live here:

* **Chaos** (test harness): injects a *cause* into a route. Never callable by the agent.
* **Operations** (what an on-call engineer, or the agent after approval, can do): five
  operational *actions*. Each action clears exactly one cause and may have side effects
  on runtime state. Applying the wrong action does not fix the incident, which is what
  makes "which fix worked" a real thing to remember.

Causes and their real fixes::

    pool_exhausted  -> flush_pool        (restart_worker only helps until the leak refills the pool)
    bad_config      -> rollback_config   (restarting or flushing does nothing)
    bad_deploy      -> rollback_release  (same symptom as bad_config; a decoy)
    memory_leak     -> restart_worker
    slow_downstream -> enable_fallback

``bad_config`` and ``bad_deploy`` raise the *same* exception from the *same* place. They can
only be told apart by evidence in ``context()`` (release vs config revision changed).

This module has no web or telemetry dependencies so it can be unit tested in isolation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from enum import Enum
from typing import Optional

log = logging.getLogger("demo.faults")


class RouteKey(str, Enum):
    get_todos = "get_todos"
    post_todos = "post_todos"
    put_todo = "put_todo"
    delete_todo = "delete_todo"


class Cause(str, Enum):
    pool_exhausted = "pool_exhausted"
    bad_config = "bad_config"
    bad_deploy = "bad_deploy"
    memory_leak = "memory_leak"
    slow_downstream = "slow_downstream"


class Action(str, Enum):
    flush_pool = "flush_pool"
    restart_worker = "restart_worker"
    rollback_config = "rollback_config"
    rollback_release = "rollback_release"
    enable_fallback = "enable_fallback"


# The single action that actually clears each cause.
FIXES: dict[Cause, Action] = {
    Cause.pool_exhausted: Action.flush_pool,
    Cause.bad_config: Action.rollback_config,
    Cause.bad_deploy: Action.rollback_release,
    Cause.memory_leak: Action.restart_worker,
    Cause.slow_downstream: Action.enable_fallback,
}

HEALTHY_RELEASE = "v2.3.1"
BAD_RELEASE = "v2.4.0"
HEALTHY_CONFIG_REV = "cfg-r41"
BAD_CONFIG_REV = "cfg-r42"


class FaultError(Exception):
    """Base class for errors raised by an injected cause. ``status_code`` drives the HTTP reply."""

    status_code = 500


class SimulatedDatabaseStateError(FaultError, ValueError):
    """Raised for bad_config and bad_deploy (identical on purpose)."""


class PoolExhausted(FaultError):
    status_code = 503


class WorkerOutOfMemory(FaultError):
    status_code = 500


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class FaultState:
    """All mutable scenario state. Thread-safe: HTTP handlers and admin endpoints run concurrently."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.error_rate = min(max(_env_float("FAULT_ERROR_RATE", 0.3), 0.0), 1.0)
        self.pool_size = max(_env_int("POOL_SIZE", 10), 1)
        self.pool_wait_s = max(_env_float("POOL_WAIT_S", 2.0), 0.0)
        self.mem_limit_mb = max(_env_int("MEM_LIMIT_MB", 150), 1)
        self.latency_min_s = max(_env_float("LATENCY_MIN_S", 2.0), 0.0)
        self.latency_max_s = max(_env_float("LATENCY_MAX_S", 5.0), self.latency_min_s)
        self.reset()

    # -- lifecycle ------------------------------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self.active: dict[Cause, set[RouteKey]] = {}
            self.pool_used = 0
            self.leaked_mb = 0
            self.release = HEALTHY_RELEASE
            self.config_rev = HEALTHY_CONFIG_REV
            self.fallback_enabled = False
            self.worker_started_at = time.time()
            self.journal: list[dict] = []

    # -- chaos (test harness only) --------------------------------------------------------

    def inject(self, cause: Cause, route: RouteKey) -> None:
        with self._lock:
            self.active.setdefault(cause, set()).add(route)
            if cause is Cause.bad_deploy:
                self.release = BAD_RELEASE
            elif cause is Cause.bad_config:
                self.config_rev = BAD_CONFIG_REV
            elif cause is Cause.slow_downstream:
                self.fallback_enabled = False
            log.warning("chaos: injected %s on %s", cause.value, route.value)

    def clear(self, cause: Cause, route: Optional[RouteKey] = None) -> None:
        with self._lock:
            if cause not in self.active:
                return
            if route is None:
                self.active.pop(cause)
            else:
                self.active[cause].discard(route)
                if not self.active[cause]:
                    self.active.pop(cause)
            if cause not in self.active:
                self._restore_context(cause)
            log.warning("chaos: cleared %s", cause.value)

    def _restore_context(self, cause: Cause) -> None:
        if cause is Cause.bad_deploy:
            self.release = HEALTHY_RELEASE
        elif cause is Cause.bad_config:
            self.config_rev = HEALTHY_CONFIG_REV

    def chaos_snapshot(self) -> dict[str, list[str]]:
        with self._lock:
            return {c.value: sorted(r.value for r in routes) for c, routes in self.active.items()}

    # -- operations (the only thing the agent may ever trigger) ---------------------------

    def apply_action(self, action: Action, actor: str = "unknown") -> dict:
        """Apply an operational action. Does not report whether it helped; observe telemetry for that."""
        with self._lock:
            if action is Action.flush_pool:
                self.pool_used = 0
            elif action is Action.restart_worker:
                # A restart empties everything held in-process, but cannot remove a leak's cause
                # unless the cause is the in-process memory leak itself.
                self.pool_used = 0
                self.leaked_mb = 0
                self.worker_started_at = time.time()
            elif action is Action.enable_fallback:
                self.fallback_enabled = True

            for cause, fix in FIXES.items():
                if fix is action and cause in self.active:
                    self.active.pop(cause)
                    self._restore_context(cause)

            entry = {"ts": time.time(), "action": action.value, "actor": actor}
            self.journal.append(entry)
            del self.journal[:-200]  # bounded
            log.warning("ops: %s applied by %s", action.value, actor)
            return {"action": action.value, "applied": True}

    def journal_snapshot(self, limit: int = 50) -> list:
        """Recent operational actions, oldest first, with epoch-millisecond timestamps."""
        with self._lock:
            return [{"ts_ms": int(e["ts"] * 1000), "action": e["action"], "actor": e["actor"]}
                    for e in self.journal[-max(1, min(limit, 200)):]]

    def context(self) -> dict:
        """Runtime facts an engineer would check first. Attached to logs and exposed read-only."""
        with self._lock:
            return {
                "release": self.release,
                "config_rev": self.config_rev,
                "pool_used": self.pool_used,
                "pool_size": self.pool_size,
                "leaked_mb": self.leaked_mb,
                "fallback_enabled": self.fallback_enabled,
                "worker_uptime_s": int(time.time() - self.worker_started_at),
            }

    # -- request path ---------------------------------------------------------------------

    async def apply(self, route: RouteKey) -> None:
        """Called at the start of each route handler. Raises FaultError subclasses or sleeps."""
        # 1. Shared connection pool: when full, every route suffers.
        with self._lock:
            exhausted = self.pool_used >= self.pool_size
        if exhausted:
            await asyncio.sleep(self.pool_wait_s)
            log.error("pool exhausted: %s", self._ctx_text())
            raise PoolExhausted("connection pool exhausted")

        with self._lock:
            leak_here = route in self.active.get(Cause.pool_exhausted, ())
            if leak_here:
                self.pool_used += 1  # connection acquired and never released

            failing_config = route in self.active.get(Cause.bad_config, ()) or route in self.active.get(
                Cause.bad_deploy, ()
            )
            leaking_mem = route in self.active.get(Cause.memory_leak, ())
            slow_dep = route in self.active.get(Cause.slow_downstream, ())
            fallback = self.fallback_enabled
            if leaking_mem:
                self.leaked_mb += 1
            leaked = self.leaked_mb

        # 2. Bad config / bad deploy: identical failure, identical text.
        if failing_config and random.random() < self.error_rate:
            log.error("[%s] database state error: %s", route.value, self._ctx_text())
            raise SimulatedDatabaseStateError("Simulated database state error")

        # 3. Memory leak: latency grows with the leak, then the worker dies.
        if leaking_mem:
            if leaked >= self.mem_limit_mb:
                log.error("[%s] worker memory limit exceeded: %s", route.value, self._ctx_text())
                raise WorkerOutOfMemory("worker memory limit exceeded")
            log.warning("[%s] memory growing: %s", route.value, self._ctx_text())
            await asyncio.sleep(min(leaked * 0.005, 0.75))

        # 4. Slow downstream dependency: latency, unless the fallback is serving degraded data.
        if slow_dep and not fallback:
            delay = random.uniform(self.latency_min_s, self.latency_max_s)
            log.warning("[%s] downstream slow, waiting %.2fs: %s", route.value, delay, self._ctx_text())
            await asyncio.sleep(delay)

    def context_text(self) -> str:
        return self._ctx_text()

    def _ctx_text(self) -> str:
        c = self.context()
        return (
            f"release={c['release']} config_rev={c['config_rev']} "
            f"pool={c['pool_used']}/{c['pool_size']} leaked_mb={c['leaked_mb']} "
            f"fallback={c['fallback_enabled']}"
        )


# Module-level singleton used by the app.
state = FaultState()


async def apply_faults(route: RouteKey) -> None:
    await state.apply(route)
