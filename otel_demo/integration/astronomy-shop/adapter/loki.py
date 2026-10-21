"""Read-only Loki HTTP API client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ._common import normalize_level, pick_service, stable_id
from ._http import get_json
from .events import Evidence

SOURCE = "loki"
MESSAGE_LIMIT = 2000


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


def to_evidence(
    entry: LogEntry,
    *,
    service: Optional[str] = None,
    level: Optional[str] = None,
) -> Evidence:
    """Map one log record to evidence, truncating oversized messages."""
    labels = entry.labels
    message = entry.line if len(entry.line) <= MESSAGE_LIMIT else entry.line[:MESSAGE_LIMIT]
    return Evidence(
        service=pick_service(labels, service),
        level=normalize_level(level if level is not None else _line_level(entry.line)),
        message=message,
        trace_id=labels.get("trace_id") or labels.get("traceID") or labels.get("trace.id"),
        span_id=labels.get("span_id") or labels.get("spanID") or labels.get("span.id"),
        timestamp=entry.timestamp_ns / 1_000_000_000,
        source=SOURCE,
        id=stable_id("log", entry.timestamp_ns, entry.line, dict(labels)),
    )
