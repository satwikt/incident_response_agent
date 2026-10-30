"""Read-only Grafana Tempo HTTP API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ._common import pick_service, stable_id
from ._http import get_json
from .events import Evidence

SOURCE = "tempo"


@dataclass(frozen=True)
class TraceSummary:
    trace_id: str
    root_service: str
    root_name: str
    start_unix_nano: int
    duration_ms: float


class TempoClient:
    """Minimal client for `/api/search` and `/api/traces/{id}`."""

    def __init__(self, base_url: str, *, timeout: float = 5.0) -> None:
        self._base_url = base_url
        self._timeout = timeout

    def search(
        self,
        *,
        service: str,
        start_ns: int,
        end_ns: int,
        limit: int = 20,
        tags: Optional[str] = None,
    ) -> list[TraceSummary]:
        start_s = int(start_ns / 1_000_000_000) if start_ns > 100_000_000_000 else int(start_ns)
        end_s = int(end_ns / 1_000_000_000) if end_ns > 100_000_000_000 else int(end_ns)
        payload = get_json(
            self._base_url,
            "api/search",
            {
                "tags": tags or f"service.name={service}",
                "start": start_s,
                "end": end_s,
                "limit": limit,
            },
            timeout=self._timeout,
        )
        return _parse_search(payload)

    def get_trace(self, trace_id: str) -> Mapping[str, Any]:
        return get_json(
            self._base_url, f"api/traces/{trace_id}", timeout=self._timeout
        )


def _parse_search(payload: Mapping[str, Any]) -> list[TraceSummary]:
    traces = payload.get("traces") or []
    rows: list[TraceSummary] = []
    for trace in traces:
        rows.append(
            TraceSummary(
                trace_id=str(trace.get("traceID", "")),
                root_service=str(trace.get("rootServiceName", "")),
                root_name=str(trace.get("rootTraceName", "")),
                start_unix_nano=int(trace.get("startTimeUnixNano") or 0),
                duration_ms=float(trace.get("durationMs") or 0.0),
            )
        )
    return rows


def to_evidence(
    summary: TraceSummary,
    *,
    service: Optional[str] = None,
    level: str = "WARNING",
    message: Optional[str] = None,
) -> Evidence:
    """Map one trace summary to evidence, preserving the trace identifier."""
    resolved = service or summary.root_service
    if not resolved:
        raise ValueError("trace summary has no root service")
    return Evidence(
        service=resolved,
        level=level,
        message=message
        or f"trace {summary.trace_id} {summary.root_name} duration_ms={summary.duration_ms:.1f}",
        duration_ms=summary.duration_ms,
        trace_id=summary.trace_id,
        timestamp=summary.start_unix_nano / 1_000_000_000,
        source=SOURCE,
        id=stable_id(f"trace-{SOURCE}", summary.trace_id),
    )
