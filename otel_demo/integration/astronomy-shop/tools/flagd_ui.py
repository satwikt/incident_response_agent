"""Read and write feature flags through the vendored flagd-ui control API."""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

FLAGD_SCHEMA = "https://flagd.dev/schema/v0/flags.json"


class FlagdUiClient:
    """Client for `/api/read` and `/api/write` on the flagd-ui service."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080/feature",
        *,
        timeout: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def read(self) -> dict[str, dict]:
        payload = self._request("GET", "/api/read")
        flags = payload.get("flags")
        if not isinstance(flags, Mapping):
            raise ValueError("flagd-ui read response has no flags map")
        return {str(name): dict(spec) for name, spec in flags.items()}

    def write(self, flags: Mapping[str, Any]) -> None:
        body = json.dumps(
            {"data": {"$schema": FLAGD_SCHEMA, "flags": flags}}
        ).encode("utf-8")
        self._request("POST", "/api/write", body)

    def _request(self, method: str, path: str, body: bytes | None = None) -> dict:
        request = Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(
                f"flagd-ui {method} {path} failed: HTTP {exc.code} {detail}"
            ) from exc
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("flagd-ui response is not a JSON object")
        return payload
