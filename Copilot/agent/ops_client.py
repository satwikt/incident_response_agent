"""Read-only client for the monitored app's action journal (what operational actions were taken, and when).

It is how memory learns what actually fixed an incident without an approval workflow: whichever action was applied last
before recovery is recorded as the fix that worked, and earlier ones as fixes that did not. Fails open (returns []).
"""

from __future__ import annotations

import logging
from typing import List

import httpx

from . import config

log = logging.getLogger("copilot.ops")


def fetch_journal(timeout_s: float = 5.0) -> List[dict]:
    if not config.APP_OPS_URL or not config.OPS_KEY:
        return []
    try:
        with httpx.Client(timeout=timeout_s) as c:
            resp = c.get(f"{config.APP_OPS_URL}/ops/journal", headers={"X-Api-Key": config.OPS_KEY})
        if resp.status_code != 200:
            log.warning("action journal returned HTTP %s", resp.status_code)
            return []
        rows = resp.json()
        return [r for r in rows if isinstance(r, dict) and isinstance(r.get("ts_ms"), int) and isinstance(r.get("action"), str)]
    except Exception as exc:  # noqa: BLE001 - never break incident handling over a missing journal
        log.warning("action journal unavailable (%s)", type(exc).__name__)
        return []


def actions_between(journal: List[dict], start_ms: int, end_ms: int) -> List[dict]:
    """Journal entries within [start_ms, end_ms], oldest first."""
    return sorted((r for r in journal if start_ms <= r["ts_ms"] <= end_ms), key=lambda r: r["ts_ms"])
