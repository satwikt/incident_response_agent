"""Read-only Loki HTTP API client."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ._common import normalize_level, pick_service, stable_id
from ._http import get_json
from .events import Evidence

SOURCE = "loki"
MESSAGE_LIMIT = 2000

# The Astronomy Shop services log every request as `METHOD /path -> STATUS (12.3ms)` inside a JSON line.
_REQUEST_LINE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)\s+->\s+(\d{3})\s+\(([\d.]+)ms\)")


@dataclass(frozen=True)
class LogEntry:
    timestamp_ns: int
    line: str
    labels: Mapping[str, str]


class LokiClient:
    """Minimal client for `/loki/api/v1/query_range`."""

    def __init__(self, base_url: str, *, timeout: float = 5.0) -> None:
        self._base_url = base_url
        self._timeout = timeout

    def query_range(
        self,
        query: str,
        start_ns: int,
        end_ns: int,
        *,
        limit: int = 100,
        direction: str = "backward",
    ) -> list[LogEntry]:
        payload = get_json(
            self._base_url,
            "loki/api/v1/query_range",
            {
                "query": query,
                "start": start_ns,
                "end": end_ns,
                "limit": limit,
                "direction": direction,
            },
            timeout=self._timeout,
        )
        return _parse(payload)


def _parse(payload: Mapping[str, Any]) -> list[LogEntry]:
    if payload.get("status") != "success":
        raise ValueError(f"loki query failed: {payload.get('error') or payload.get('status')}")
    data = payload.get("data") or {}
    entries: list[LogEntry] = []
    for stream in data.get("result") or []:
        labels = stream.get("stream") or {}
        for timestamp, line in stream.get("values") or []:
            entries.append(LogEntry(int(timestamp), str(line), labels))
    return entries


def _line_level(line: str) -> str:
    """Best-effort level extraction from structured log lines."""
    try:
        payload = json.loads(line)
    except (TypeError, ValueError):
        return "INFO"
    if not isinstance(payload, Mapping):
        return "INFO"
    for key in ("level", "severity_text", "severity"):
        value = payload.get(key)
        if value:
            return normalize_level(value, "INFO")
    return "INFO"


def _request_fields(line: str) -> tuple[Optional[str], Optional[int], Optional[float], Optional[str]]:
    """(route, status, duration_ms, exception) of a request log line; all None for any other line.

    Without these fields the Copilot cannot count the request, so its error-rate and latency tools see no traffic.
    """
    try:
        payload = json.loads(line)
    except (TypeError, ValueError):
        return None, None, None, None
    if not isinstance(payload, Mapping):
        return None, None, None, None
    match = _REQUEST_LINE.match(str(payload.get("message") or ""))
    if not match:
        return None, None, None, None
    exception = payload.get("exception")
    return (
        f"{match.group(1)} {match.group(2)}",
        int(match.group(3)),
        float(match.group(4)),
        str(exception) if exception else None,
    )


def to_evidence(
    entry: LogEntry,
    *,
    service: Optional[str] = None,
    level: Optional[str] = None,
) -> Evidence:
    """Map one log record to evidence, truncating oversized messages."""
    labels = entry.labels
    message = entry.line if len(entry.line) <= MESSAGE_LIMIT else entry.line[:MESSAGE_LIMIT]
    route, status, duration_ms, exception = _request_fields(entry.line)
    return Evidence(
        service=pick_service(labels, service),
        level=normalize_level(level if level is not None else _line_level(entry.line)),
        message=message,
        route=route,
        status=status,
        duration_ms=duration_ms,
        exception=exception,
        trace_id=labels.get("trace_id") or labels.get("traceID") or labels.get("trace.id"),
        span_id=labels.get("span_id") or labels.get("spanID") or labels.get("span.id"),
        timestamp=entry.timestamp_ns / 1_000_000_000,
        source=SOURCE,
        id=stable_id("log", entry.timestamp_ns, entry.line, dict(labels)),
    )
