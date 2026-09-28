"""Authenticated control endpoints for the demo app.

* ``/chaos/*`` injects incidents. Guarded by ``CHAOS_KEY``. Used by the demo harness only.
* ``/ops/*``  operational actions and read-only runtime state. Guarded by ``OPS_KEY``.
  This is the only surface the Copilot's remediation executor calls.

Both fail closed: if the corresponding key is not configured, the endpoints answer 503 instead
of running unauthenticated. Keys are compared in constant time.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Callable

from fastapi import APIRouter, Depends, Header, HTTPException

from faults import Action, Cause, RouteKey, state

log = logging.getLogger("demo.admin")


def _require_key(env_name: str) -> Callable:
    def dependency(x_api_key: str | None = Header(default=None)) -> str:
        expected = os.getenv(env_name, "")
        if not expected:
            # Fail closed, without saying which variable is missing.
            raise HTTPException(status_code=503, detail="endpoint disabled")
        if x_api_key is None or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")
        return env_name

    return dependency


require_chaos_key = _require_key("CHAOS_KEY")
require_ops_key = _require_key("OPS_KEY")

router = APIRouter()


# ── chaos (test harness) ─────────────────────────────────────────────────────────────────

@router.get("/chaos", dependencies=[Depends(require_chaos_key)])
def chaos_state() -> dict:
    """Currently injected causes and the routes they affect."""
    return state.chaos_snapshot()


@router.post("/chaos/reset", dependencies=[Depends(require_chaos_key)])
def chaos_reset() -> dict:
    """Clear every cause and restore runtime state (used between demo scenarios)."""
    state.reset()
    return {"reset": True}


@router.post("/chaos/{cause}/{route}", dependencies=[Depends(require_chaos_key)])
def chaos_set(cause: Cause, route: RouteKey, enabled: bool) -> dict:
    """Inject (``enabled=true``) or clear (``enabled=false``) a cause on one route."""
    if enabled:
        state.inject(cause, route)
    else:
        state.clear(cause, route)
    return {"cause": cause.value, "route": route.value, "enabled": enabled}


# ── operations (what remediation may do) ─────────────────────────────────────────────────

@router.get("/ops/state", dependencies=[Depends(require_ops_key)])
def ops_state() -> dict:
    """Read-only runtime facts: release, config revision, pool usage, worker uptime."""
    return state.context()


@router.post("/ops/{action}", dependencies=[Depends(require_ops_key)])
def ops_apply(action: Action) -> dict:
    """Apply one operational action. The response does not say whether it helped."""
    return state.apply_action(action, actor="ops-api")
