"""Shared guard for the fault-injection endpoints.

When CHAOS_KEY is set, every state-changing fault call must carry it in the ``X-Chaos-Key`` header; without it the endpoints
stay open, so a local ``docker compose up`` needs no setup. Reading the fault state (GET) is not guarded.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException

HEADER = "X-Chaos-Key"


def chaos_key() -> str:
    return os.getenv("CHAOS_KEY", "").strip()


def require_chaos_key(x_chaos_key: Optional[str] = Header(default=None)) -> None:
    expected = chaos_key()
    if not expected:
        return
    if not x_chaos_key or not hmac.compare_digest(x_chaos_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Chaos key required (X-Chaos-Key header)")
