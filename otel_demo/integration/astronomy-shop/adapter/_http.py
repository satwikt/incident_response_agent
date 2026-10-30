"""Minimal read-only JSON HTTP helper for observability backend clients."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def get_json(
    base_url: str,
    path: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    timeout: float = 5.0,
) -> Any:
    """GET one JSON document from an approved, operator-configured endpoint."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if params:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        if query:
            url = f"{url}?{query}"
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured endpoint
        return json.loads(response.read().decode("utf-8"))
