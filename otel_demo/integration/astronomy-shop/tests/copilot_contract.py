"""A local stand-in for the Copilot's ingest endpoint.

Every rule here is transcribed from the Copilot repository at commit
``0ce98443db0b0b3c09c40a53849164c1f7160fe9`` (``Copilot/api/ingest.py``,
``Copilot/agent/config.py``), so the adapter can be exercised against the real
contract without the Copilot's dependencies (FastAPI, pydantic) or a running
Copilot.

The order of checks matters and is copied from the endpoint: auth, then rate
limit, then body size, then batch size, then schema, then service binding.
A batch is validated atomically -- one invalid event rejects all of them.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Optional

_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_MAX_BODY_BYTES = 1_048_576
_MAX_BATCH = 500
_MAX_FUTURE_MS = 5 * 60 * 1000
_MAX_AGE_MS = 7 * 24 * 3600 * 1000
_MAX_MESSAGE_CHARS = 4096
_MAX_ROUTE_CHARS = 200


class CopilotIngest:
    """The subset of the Copilot's ingest behaviour the adapter depends on."""

    def __init__(
        self,
        keys: Mapping[str, str],
        *,
        rate_per_sec: float = 20.0,
        burst: int = 40,
    ) -> None:
        # key -> service, the same direction as the Copilot's INGEST_KEYS.
        self.keys = dict(keys)
        self.rate_per_sec = rate_per_sec
        self.burst = burst
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self.accepted_ids: set[str] = set()
        self.requests: list[dict[str, Any]] = []

    def _allow(self, service: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(service, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate_per_sec)
            if tokens < 1.0:
                self._buckets[service] = (tokens, now)
                return False
            self._buckets[service] = (tokens - 1.0, now)
            return True

    def handle(
        self, api_key: Optional[str], body: bytes
    ) -> tuple[int, dict[str, Any]]:
        """Return (status code, response body) for one ingest request."""
        service = self.keys.get(api_key or "")
        if service is None:
            return 401, {"detail": "unauthorized"}
        if not self._allow(service):
            return 429, {"detail": "rate limit exceeded"}
        if len(body) > _MAX_BODY_BYTES:
            return 413, {"detail": "payload too large"}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 422, {"detail": "invalid JSON"}
        if not isinstance(payload, list):
            return 422, {"detail": "expected a JSON array of events"}
        if len(payload) > _MAX_BATCH:
            return 413, {"detail": "too many events in one batch"}
        now_ms = int(time.time() * 1000)
        for event in payload:
            if not isinstance(event, Mapping):
                return 422, {"detail": "invalid event in batch"}
            if not self._valid(event, now_ms):
                return 422, {"detail": "invalid event in batch"}
            if event.get("service") is not None and event["service"] != service:
                return 403, {"detail": "service mismatch"}
        accepted = 0
        duplicates = 0
        for event in payload:
            key = f"{service}:{event['id']}"
            if key in self.accepted_ids:
                duplicates += 1
                continue
            self.accepted_ids.add(key)
            accepted += 1
        return 200, {"accepted": accepted, "duplicates": duplicates}

    def _valid(self, event: Mapping[str, Any], now_ms: int) -> bool:
        if not isinstance(event.get("id"), str) or not _ID_RE.match(event["id"]):
            return False
        level = event.get("level")
        if not isinstance(level, str) or level.upper() not in _LEVELS:
            return False
        # route carries max_length=200, so an over-long route is rejected
        # rather than truncated.
        route = event.get("route")
        if route is not None and (
            not isinstance(route, str) or len(route) > _MAX_ROUTE_CHARS
        ):
            return False
        # message and exception are truncated by _clean, so length is not a
        # rejection reason -- only a non-string is.
        message = event.get("message")
        if message is not None and not isinstance(message, str):
            return False
        exception = event.get("exception")
        if exception is not None and not isinstance(exception, str):
            return False
        status = event.get("status")
        if status is not None and (
            not isinstance(status, int) or isinstance(status, bool)
            or not 100 <= status <= 599
        ):
            return False
        duration = event.get("duration_ms")
        if duration is not None and (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not 0 <= duration <= 3_600_000
        ):
            return False
        return self._ts_ms(event.get("ts"), now_ms) is not None

    @staticmethod
    def _ts_ms(raw: Any, now_ms: int) -> Optional[int]:
        if raw is None:
            return None
        if isinstance(raw, str):
            from datetime import datetime, timezone

            try:
                dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ms = int(dt.timestamp() * 1000)
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            ms = int(raw * 1000) if raw < 1e11 else int(raw)
        else:
            return None
        if ms > now_ms + _MAX_FUTURE_MS or ms < now_ms - _MAX_AGE_MS:
            return None
        return ms


class ContractServer:
    """Serve the contract over HTTP so the adapter's real client is exercised."""

    def __init__(self, ingest: CopilotIngest) -> None:
        self.ingest = ingest
        handler = _make_handler(ingest)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/ingest/events"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _make_handler(ingest: CopilotIngest):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            status, payload = ingest.handle(self.headers.get("X-Api-Key"), body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return Handler
