"""Build pollable evidence sources from the read-only backend clients."""

from __future__ import annotations

import time
from typing import Callable, Optional

from .events import Evidence
from .jaeger import JaegerClient
from .jaeger import to_evidence as jaeger_evidence
from .loki import LokiClient
from .loki import to_evidence as loki_evidence
from .prometheus import PrometheusClient
from .prometheus import to_evidence as prometheus_evidence
from .tempo import TempoClient
from .tempo import to_evidence as tempo_evidence

Source = Callable[[], list[Evidence]]
Clock = Callable[[], float]


def prometheus_source(
    client: PrometheusClient,
    expression: str,
    *,
    service: Optional[str] = None,
    level: str = "WARNING",
    at: Optional[float] = None,
) -> Source:
    """Poll one PromQL expression and convert every sample to evidence."""

    def fetch() -> list[Evidence]:
        return [
            prometheus_evidence(sample, service=service, level=level)
            for sample in client.query(expression, at=at)
        ]

    return fetch


def loki_source(
    client: LokiClient,
    query: str,
    *,
    window_seconds: float = 300.0,
    limit: int = 100,
    service: Optional[str] = None,
    level: Optional[str] = None,
    now: Clock = time.time,
) -> Source:
    """Poll a trailing LogQL window and convert log records to evidence."""

    def fetch() -> list[Evidence]:
        end_ns = int(now() * 1_000_000_000)
        start_ns = end_ns - int(window_seconds * 1_000_000_000)
        return [
            loki_evidence(entry, service=service, level=level)
            for entry in client.query_range(query, start_ns, end_ns, limit=limit)
        ]

    return fetch


def tempo_source(
    client: TempoClient,
    service: str,
    *,
    window_seconds: float = 300.0,
    limit: int = 20,
    level: str = "WARNING",
    now: Clock = time.time,
) -> Source:
    """Poll a trailing Tempo search window for one service."""

    def fetch() -> list[Evidence]:
        end_ns = int(now() * 1_000_000_000)
        start_ns = end_ns - int(window_seconds * 1_000_000_000)
        return [
            tempo_evidence(summary, service=service, level=level)
            for summary in client.search(
                service=service, start_ns=start_ns, end_ns=end_ns, limit=limit
            )
        ]

    return fetch


def jaeger_source(
    client: JaegerClient,
    service: str,
    *,
    window_seconds: float = 300.0,
    limit: int = 20,
    level: str = "WARNING",
    now: Clock = time.time,
) -> Source:
    """Poll a trailing Jaeger search window for one service."""

    def fetch() -> list[Evidence]:
        end_us = int(now() * 1_000_000)
        start_us = end_us - int(window_seconds * 1_000_000)
        return [
            jaeger_evidence(trace, level=level)
            for trace in client.find_traces(
                service, start_us, end_us, limit=limit
            )
        ]

    return fetch
