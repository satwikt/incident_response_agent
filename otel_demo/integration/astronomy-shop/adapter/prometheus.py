"""Read-only Prometheus HTTP API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ._common import as_int, normalize_level, pick_attr, pick_service, stable_id
from ._http import get_json
from .events import Evidence

SOURCE = "prometheus"


@dataclass(frozen=True)
class Sample:
    metric: str
    labels: Mapping[str, str]
    value: float
    timestamp: float


class PrometheusClient:
    """Minimal client for `/api/v1/query` and `/api/v1/query_range`."""

    def __init__(self, base_url: str, *, timeout: float = 5.0) -> None:
        self._base_url = base_url
        self._timeout = timeout

    def query(self, expression: str, *, at: Optional[float] = None) -> list[Sample]:
        payload = get_json(
            self._base_url,
            "api/v1/query",
            {"query": expression, "time": at},
            timeout=self._timeout,
        )
        return _parse(payload)

    def query_range(
        self, expression: str, start: float, end: float, step: str
    ) -> list[Sample]:
        payload = get_json(
            self._base_url,
            "api/v1/query_range",
            {"query": expression, "start": start, "end": end, "step": step},
            timeout=self._timeout,
        )
        return _parse(payload)


def _parse(payload: Mapping[str, Any]) -> list[Sample]:
    if payload.get("status") != "success":
        raise ValueError(f"prometheus query failed: {payload.get('error') or payload.get('status')}")
    data = payload.get("data") or {}
    result_type = data.get("resultType")
    rows: list[Sample] = []
    for series in data.get("result") or []:
        labels = series.get("metric") or {}
        metric = str(labels.get("__name__", ""))
        if result_type == "matrix":
            for timestamp, value in series.get("values") or []:
                rows.append(Sample(metric, labels, float(value), float(timestamp)))
        else:
            timestamp, value = series.get("value") or (0, 0)
            rows.append(Sample(metric, labels, float(value), float(timestamp)))
    return rows


def to_evidence(
    sample: Sample,
    *,
    service: Optional[str] = None,
    level: str = "WARNING",
    message: Optional[str] = None,
) -> Evidence:
    """Map one sample to backend-neutral evidence without inventing thresholds."""
    labels = sample.labels
    status = as_int(
        pick_attr(
            labels,
            "http_response_status_code",
            "http.status_code",
            "http_status_code",
            "status_code",
        )
    )
    route = pick_attr(labels, "http_route", "http.route", "http.target", "span_name")
    return Evidence(
        service=pick_service(labels, service),
        level=normalize_level(level),
        message=message or f"{sample.metric}={sample.value}",
        route=route,
        status=status,
        timestamp=sample.timestamp,
        source=SOURCE,
        id=stable_id("prom", sample.metric, sample.timestamp, dict(labels)),
    )
