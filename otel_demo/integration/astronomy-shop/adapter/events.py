"""Normalize observability evidence into the Copilot ingest event contract."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

_EVENT_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")

# The Copilot validates a batch atomically: one invalid event rejects all of
# them. These bounds mirror the ingest schema so a stray label cannot poison
# a whole batch.
_STATUS_MIN, _STATUS_MAX = 100, 599
_DURATION_MAX_MS = 3_600_000
_MAX_ROUTE_CHARS = 200
_MAX_EXCEPTION_CHARS = 8192


@dataclass(frozen=True)
class Evidence:
    service: str
    level: str
    message: str
    route: Optional[str] = None
    status: Optional[int] = None
    duration_ms: Optional[float] = None
    exception: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    source: str = "observability"
    timestamp: Optional[str | float | int] = None
    id: Optional[str] = None


def _timestamp(value: Any) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    return str(value)


def _status(value: Any) -> Optional[int]:
    """Return a status the Copilot accepts, or None when it is out of range."""
    if value is None or value == "":
        return None
    try:
        status = int(value)
    except (TypeError, ValueError):
        return None
    return status if _STATUS_MIN <= status <= _STATUS_MAX else None


def _duration(value: Any) -> Optional[float]:
    """Return a duration the Copilot accepts, or None when it is out of range."""
    if value is None or value == "":
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(duration) or duration < 0 or duration > _DURATION_MAX_MS:
        return None
    return duration


def _route(value: Optional[str]) -> Optional[str]:
    """Truncate a route to the length the Copilot accepts.

    The Copilot caps the route length, so an over-long route is rejected
    along with the whole batch it arrives in.
    """
    if value is None:
        return None
    return value[:_MAX_ROUTE_CHARS]


def _exception(value: Optional[str]) -> Optional[str]:
    """Truncate an exception to the length the Copilot accepts."""
    if value is None:
        return None
    return value[:_MAX_EXCEPTION_CHARS]


def _derived_id(evidence: Evidence, timestamp: str) -> str:
    """A deterministic id for evidence that does not carry one.

    The Copilot deduplicates on the event id, so the same evidence polled
    twice must produce the same id. Deriving it from the record's own
    fields keeps that promise for callers that do not set an id.
    """
    payload = "|".join(
        str(part)
        for part in (
            evidence.source,
            evidence.service,
            evidence.level,
            evidence.message,
            evidence.route,
            evidence.status,
            evidence.duration_ms,
            evidence.trace_id,
            evidence.span_id,
            timestamp,
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"ev-{digest[:32]}"


def to_event(evidence: Evidence) -> dict[str, Any]:
    """Convert one evidence record to the current Copilot event schema."""
    if not evidence.service.strip():
        raise ValueError("service is required")
    if not evidence.message.strip():
        raise ValueError("message is required")
    level = evidence.level.upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("invalid level")

    context = [f"source={evidence.source}"]
    if evidence.trace_id:
        context.append(f"trace_id={evidence.trace_id}")
    if evidence.span_id:
        context.append(f"span_id={evidence.span_id}")
    message = f"{evidence.message} | {' '.join(context)}"
    timestamp = _timestamp(evidence.timestamp)
    event_id = evidence.id or _derived_id(evidence, timestamp)
    if not _EVENT_ID.fullmatch(event_id):
        raise ValueError("invalid event id")
    return {
        "id": event_id,
        "ts": timestamp,
        "service": evidence.service,
        "level": level,
        "route": _route(evidence.route),
        "status": _status(evidence.status),
        "duration_ms": _duration(evidence.duration_ms),
        "message": message,
        "exception": _exception(evidence.exception),
    }


def from_mapping(data: Mapping[str, Any], *, source: str = "observability") -> Evidence:
    """Build evidence from a backend-neutral mapping used by upcoming clients."""
    return Evidence(
        service=str(data.get("service", "")),
        level=str(data.get("level", "INFO")),
        message=str(data.get("message", "")),
        route=data.get("route"),
        status=data.get("status"),
        duration_ms=data.get("duration_ms"),
        exception=data.get("exception"),
        trace_id=data.get("trace_id"),
        span_id=data.get("span_id"),
        source=source,
        timestamp=data.get("timestamp"),
        id=data.get("id"),
    )
