"""Read-only Jaeger HTTP API client (secondary trace view)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ._common import as_int, pick_attr, pick_service, stable_id
from ._http import get_json
from .events import Evidence

SOURCE = "jaeger"


@dataclass(frozen=True)
class JaegerTrace:
    trace_id: str
    spans: list[Mapping[str, Any]]
    processes: Mapping[str, Mapping[str, Any]]


class JaegerClient:
    """Minimal client for the Jaeger trace search endpoint.

    `api_prefix` carries the deployment's base path: Jaeger v2 registers its
    REST API under `base_path`, so the upstream stack serves it at
    `/jaeger/ui/api/traces` rather than `/api/traces`.
    """

    def __init__(
        self, base_url: str, *, api_prefix: str = "/api", timeout: float = 5.0
    ) -> None:
        self._base_url = base_url
        self._api_prefix = api_prefix
        self._timeout = timeout

    def find_traces(
        self,
        service: str,
        start_us: int,
        end_us: int,
        *,
        limit: int = 20,
        operation: Optional[str] = None,
    ) -> list[JaegerTrace]:
        payload = get_json(
            self._base_url,
            f"{self._api_prefix}/traces",
            {
                "service": service,
                "start": start_us,
                "end": end_us,
                "limit": limit,
                "operation": operation,
            },
            timeout=self._timeout,
        )
        if payload.get("errors"):
            raise ValueError(f"jaeger query failed: {payload['errors']}")
        return [
            JaegerTrace(
                trace_id=str(item.get("traceID", "")),
                spans=list(item.get("spans") or []),
                processes=item.get("processes") or {},
            )
            for item in payload.get("data") or []
        ]


def _tags(span: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(tag.get("key")): tag.get("value")
        for tag in span.get("tags") or []
        if isinstance(tag, Mapping)
    }


def _root_span(trace: JaegerTrace) -> Optional[Mapping[str, Any]]:
    if not trace.spans:
        return None
    parents = {
        span.get("spanID")
        for span in trace.spans
        for ref in span.get("references") or []
        if ref.get("refType") == "CHILD_OF"
    }
    roots = [span for span in trace.spans if span.get("spanID") not in parents]
    return min(roots or trace.spans, key=lambda span: span.get("startTime") or 0)


def _service_name(trace: JaegerTrace, span: Mapping[str, Any]) -> Optional[str]:
    process = trace.processes.get(span.get("processID") or "") or {}
    return process.get("serviceName")


def to_evidence(
    trace: JaegerTrace,
    *,
    service: Optional[str] = None,
    level: str = "WARNING",
    message: Optional[str] = None,
) -> Evidence:
    """Map one trace to evidence using its root span when available."""
    root = _root_span(trace)
    if root is None:
        raise ValueError("jaeger trace has no spans")
    tags = _tags(root)
    operation = root.get("operationName") or "unknown"
    duration_ms = float(root.get("duration") or 0) / 1000.0
    failed = tags.get("otel.status_code") == "ERROR" or tags.get("error") is True
    method = pick_attr(tags, "http.request.method", "http.method")
    route = pick_attr(tags, "http.route", "http.target", "url.path")
    if route and method and not str(route).startswith(f"{method} "):
        route = f"{method} {route}"
    return Evidence(
        service=pick_service({"service_name": _service_name(trace, root)}, service),
        level="ERROR" if failed else level,
        message=message
        or f"trace {trace.trace_id} {operation} duration_ms={duration_ms:.1f}",
        route=route,
        status=as_int(
            pick_attr(
                tags,
                "http.response.status_code",
                "http.status_code",
                "http_status_code",
            )
        ),
        duration_ms=duration_ms,
        trace_id=trace.trace_id,
        span_id=str(root.get("spanID") or "") or None,
        timestamp=float(root.get("startTime") or 0) / 1_000_000.0,
        source=SOURCE,
        id=stable_id(f"trace-{SOURCE}", trace.trace_id),
    )
