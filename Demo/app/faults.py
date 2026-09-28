"""Per-route fault injection framework for the SRE Copilot Demo app.

Faults are applied *inside* each route handler — after OTel has opened the
route's span — so that tracing and metrics correctly attribute induced latency
or errors to the specific endpoint rather than to a generic middleware layer.
"""

import asyncio
import logging
import random
from contextvars import ContextVar
from enum import Enum

log = logging.getLogger("demo.faults")


class RouteKey(str, Enum):
    """Identifies which route a fault applies to.

    Used as a path parameter in the admin API so Swagger renders a dropdown.
    """
    get_todos   = "get_todos"
    post_todos  = "post_todos"
    put_todo    = "put_todo"
    delete_todo = "delete_todo"


class FaultName(str, Enum):
    """The type of fault to inject.

    Used as a path parameter in the admin API so Swagger renders a dropdown.

    - **latency_spike** – sleeps 2–5 s inside the handler before the DB call.
    - **random_500_storm** – raises an unhandled ValueError on ~30 % of calls.
    - **memory_leak** – appends 1 MB to an in-process list on every call.
    - **db_connection_leak** – skips ``db.close()`` so the session is never returned.
    """
    latency_spike      = "latency_spike"
    random_500_storm   = "random_500_storm"
    memory_leak        = "memory_leak"
    db_connection_leak = "db_connection_leak"


# ── State ─────────────────────────────────────────────────────────────────────

# Per-route fault state: { route_key -> { fault_name -> bool } }
_route_faults: dict[str, dict[str, bool]] = {
    route.value: {fault.value: False for fault in FaultName}
    for route in RouteKey
}

# Set by apply_faults() so that get_db()'s finally block can check the
# correct route's db_connection_leak flag without needing a parameter.
_current_route: ContextVar[str] = ContextVar("current_route", default="")

# Accumulates leaked memory while the memory_leak fault is active.
leaked_memory: list = []


# ── Public API ────────────────────────────────────────────────────────────────

def get_faults() -> dict:
    """Return the current fault state for every route.

    Example response::

        {
            "get_todos":   {"latency_spike": false, "random_500_storm": false, ...},
            "post_todos":  {"latency_spike": true,  "random_500_storm": false, ...},
            ...
        }
    """
    return {route: dict(faults) for route, faults in _route_faults.items()}


def toggle_fault(route_key: RouteKey, fault_name: FaultName, enabled: bool) -> None:
    """Enable or disable a single fault on a specific route.

    Example — inject latency on GET /todos::

        toggle_fault(RouteKey.get_todos, FaultName.latency_spike, True)

    Disabling ``memory_leak`` also clears the accumulated leaked memory.
    """
    _route_faults[route_key.value][fault_name.value] = enabled
    log.warning("Fault '%s' on route '%s' set to %s", fault_name.value, route_key.value, enabled)
    if not enabled and fault_name == FaultName.memory_leak:
        leaked_memory.clear()
        log.info("Cleared %d MB of leaked memory", len(leaked_memory))


async def apply_faults(route_key: RouteKey) -> None:
    """Apply all active faults for *route_key*.

    **Must be called at the very start of each route handler**, before any DB
    work, so that the OTel span for that handler fully captures any induced
    latency or error.

    Fault execution order:

    1. ``random_500_storm`` — fail fast before touching the DB.
    2. ``memory_leak`` — allocate before the DB call.
    3. ``latency_spike`` — sleep before the DB call.

    ``db_connection_leak`` is handled automatically by ``get_db()`` via the
    ``_current_route`` context variable set here.

    Example::

        @app.get("/todos")
        async def get_todos(db: Session = Depends(get_db)):
            await apply_faults(RouteKey.get_todos)
            return db.query(TodoDB).all()
    """
    _current_route.set(route_key.value)
    faults = _route_faults[route_key.value]

    if faults[FaultName.random_500_storm.value] and random.random() < 0.3:
        log.error("[%s] random_500_storm triggered", route_key.value)
        raise ValueError("Simulated database state error")

    if faults[FaultName.memory_leak.value]:
        leaked_memory.append("A" * 1024 * 1024)
        log.warning("[%s] memory_leak: %d MB accumulated", route_key.value, len(leaked_memory))

    if faults[FaultName.latency_spike.value]:
        delay = random.uniform(2.0, 5.0)
        log.warning("[%s] latency_spike: sleeping %.2fs", route_key.value, delay)
        await asyncio.sleep(delay)
