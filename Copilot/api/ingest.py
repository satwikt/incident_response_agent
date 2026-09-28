"""Authenticated batch ingest of telemetry events from monitored applications.

Order of checks (cheapest and safest first, and nothing is read from an unauthenticated caller):
  1. auth (constant-time key match; the key is bound to one service)  -> 401 / 503 if no keys configured
  2. per-key rate limit                                               -> 429
  3. body size (declared, then streamed)                              -> 413
  4. JSON + schema validation of the whole batch                      -> 422 (atomic: nothing stored)
  5. service binding (an event naming another service)                -> 403
  6. insert, duplicates ignored (ids are namespaced by service)       -> 200
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from agent import config
from db import events as store

log = logging.getLogger("copilot.ingest")
router = APIRouter()

_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# JSON escapes such as \ud800 can produce lone surrogates, which cannot be encoded as UTF-8 and used to crash
# the DB write. Valid surrogate pairs are already combined into one character by the JSON parser, so any
# surrogate still present in a parsed string is lone.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_MAX_FUTURE_MS = 5 * 60 * 1000
_MAX_AGE_MS = 7 * 24 * 3600 * 1000


def _clean(text: Optional[str], limit: int) -> Optional[str]:
    """Strip control characters (including NUL), replace lone surrogates, and truncate."""
    if text is None:
        return None
    return _SURROGATE_RE.sub("�", _CONTROL_RE.sub("", str(text)))[:limit]


class EventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    ts: float | int | str
    level: str
    service: Optional[str] = None
    route: Optional[str] = Field(default=None, max_length=config.MAX_ROUTE_CHARS)
    status: Optional[int] = Field(default=None, ge=100, le=599)
    duration_ms: Optional[float] = Field(default=None, ge=0, le=3_600_000)
    message: Optional[str] = None
    exception: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError("invalid id")
        return v

    @field_validator("level")
    @classmethod
    def _level(cls, v: str) -> str:
        v = v.upper()
        if v not in _LEVELS:
            raise ValueError("invalid level")
        return v

    @field_validator("route")
    @classmethod
    def _route(cls, v: Optional[str]) -> Optional[str]:
        return _clean(v, config.MAX_ROUTE_CHARS)

    @field_validator("message", mode="before")
    @classmethod
    def _message(cls, v):
        if v is not None and not isinstance(v, str):
            raise ValueError("message must be a string")
        return _clean(v, config.MAX_MESSAGE_CHARS)

    @field_validator("exception", mode="before")
    @classmethod
    def _exception(cls, v):
        if v is not None and not isinstance(v, str):
            raise ValueError("exception must be a string")
        return _clean(v, config.MAX_EXCEPTION_CHARS)

    @field_validator("duration_ms")
    @classmethod
    def _duration(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (math.isnan(v) or math.isinf(v)):
            raise ValueError("invalid duration")
        return v

    def ts_ms(self, now: int) -> int:
        """Client timestamp in epoch ms (numbers below 1e11 are seconds). Raises ValueError if implausible."""
        raw = self.ts
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid ts") from exc
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ms = int(dt.timestamp() * 1000)
        else:
            if math.isnan(raw) or math.isinf(raw):
                raise ValueError("invalid ts")
            ms = int(raw * 1000) if raw < 1e11 else int(raw)
        if ms > now + _MAX_FUTURE_MS or ms < now - _MAX_AGE_MS:
            raise ValueError("ts out of range")
        return ms


_BATCH = TypeAdapter(List[EventIn])


# ── rate limiting (token bucket per key, in process) ──────────────────────────

class _Buckets:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: Dict[str, Tuple[float, float]] = {}

    def allow(self, key_id: str) -> bool:
        cap = float(config.INGEST_RATE_BURST)
        rate = float(config.INGEST_RATE_PER_SEC)
        t = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key_id, (cap, t))
            tokens = min(cap, tokens + (t - last) * rate)
            if tokens < 1.0:
                self._state[key_id] = (tokens, t)
                return False
            self._state[key_id] = (tokens - 1.0, t)
            return True

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


buckets = _Buckets()


def authenticate(x_api_key: Optional[str]) -> str:
    """Return the service bound to the key, or raise. Compares against every key (no early exit)."""
    keys = config.INGEST_KEYS
    if not keys:
        raise HTTPException(status_code=503, detail="endpoint disabled")
    presented = (x_api_key or "").encode("utf-8", "replace")
    service = None
    for key, svc in keys.items():
        if hmac.compare_digest(presented, key.encode()):
            service = svc
    if service is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return service


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise HTTPException(status_code=413, detail="payload too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="bad request")
    chunks: List[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail="payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/ingest/events")
async def ingest_events(request: Request, x_api_key: Optional[str] = Header(default=None)) -> dict:
    service = authenticate(x_api_key)
    if not buckets.allow(service):
        raise HTTPException(status_code=429, detail="rate limit exceeded", headers={"Retry-After": "1"})

    body = await _read_body(request, config.INGEST_MAX_BODY_BYTES)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise HTTPException(status_code=422, detail="invalid JSON")
    if not isinstance(payload, list):
        raise HTTPException(status_code=422, detail="expected a JSON array of events")
    if len(payload) > config.INGEST_MAX_BATCH:
        raise HTTPException(status_code=413, detail="too many events in one batch")

    try:
        events = _BATCH.validate_python(payload)
    except ValidationError:
        # Do not echo input back: it is attacker-controlled.
        raise HTTPException(status_code=422, detail="invalid event in batch")

    now = store.now_ms()
    rows = []
    try:
        for ev in events:
            if ev.service is not None and ev.service != service:
                raise HTTPException(status_code=403, detail="service mismatch")
            rows.append({
                "id": ev.id, "ts": ev.ts_ms(now), "level": ev.level, "route": ev.route, "status": ev.status,
                "duration_ms": ev.duration_ms, "message": ev.message, "exception": ev.exception,
            })
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid event in batch")

    try:
        return await run_in_threadpool(store.insert_events, service, rows, now)
    except UnicodeError:
        # Defence in depth: anything that still cannot be encoded is a client error, not a server fault.
        raise HTTPException(status_code=422, detail="invalid event in batch")
