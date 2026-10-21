"""Bounded HTTP client for the existing Copilot ingest endpoint."""

from __future__ import annotations

import json
from typing import Any, Iterable
from urllib.request import Request, urlopen

from .events import Evidence, to_event


def build_events(evidence: Iterable[Evidence]) -> list[dict[str, Any]]:
    """Convert evidence to ingest payloads once, preserving deterministic IDs."""
    return [to_event(item) for item in evidence]


def build_batch(
    evidence: Iterable[Evidence], *, limit: int = 500
) -> list[dict[str, Any]]:
    rows = build_events(evidence)
    if len(rows) > limit:
        raise ValueError(f"batch exceeds limit of {limit}")
    return rows


def send_events(
    url: str, api_key: str, events: list[dict[str, Any]], *, timeout: float = 5.0
) -> bytes:
    """Send already-normalized events; callers own retry policy."""
    body = json.dumps(events).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured endpoint
        return response.read()


def send_batch(
    url: str, api_key: str, evidence: Iterable[Evidence], *, timeout: float = 5.0
) -> bytes:
    """Send one evidence batch; callers own retry policy and must use bounded retries."""
    return send_events(url, api_key, build_batch(evidence), timeout=timeout)
