"""Push-based telemetry: one structured event per request, batched to the Copilot's /ingest/events.

Design rules:
* Emitting never blocks or fails a request. Events go into a bounded in-memory queue; a background
  thread batches and sends them. If the queue is full the newest event is dropped and counted.
* Failures to reach the Copilot are swallowed (with capped exponential backoff and a limited number
  of retries per batch); the demo app keeps serving traffic.
* Disabled (no-op) unless both the ingest URL and key are configured.

No third-party dependencies: the sender uses the standard library.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.request
import uuid
from typing import Callable, Dict, List, Optional

log = logging.getLogger("demo.telemetry")

Sender = Callable[[List[dict]], None]


class EventEmitter:
    def __init__(
        self,
        url: str = "",
        api_key: str = "",
        service: str = "todo-app",
        max_queue: int = 10_000,
        batch_size: int = 100,
        flush_interval_s: float = 1.0,
        timeout_s: float = 2.0,
        max_attempts: int = 3,
        sender: Optional[Sender] = None,
    ) -> None:
        self.url, self.api_key, self.service = url, api_key, service
        self.batch_size = max(1, min(batch_size, 500))
        self.flush_interval_s = max(0.05, flush_interval_s)
        self.timeout_s = timeout_s
        self.max_attempts = max(1, max_attempts)
        self._custom_sender = sender is not None
        self._sender = sender or self._http_send
        self._q: "queue.Queue[dict]" = queue.Queue(maxsize=max(1, max_queue))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.dropped = 0
        self.sent = 0
        self.failed_batches = 0

    @property
    def enabled(self) -> bool:
        return self._custom_sender or bool(self.url and self.api_key)

    # -- producer side (called on the request path) ---------------------------------------

    def emit(self, event: dict) -> None:
        if not self.enabled:
            return
        try:
            self._q.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    # -- lifecycle --------------------------------------------------------------------------

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="event-emitter", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout_s)

    # -- consumer side -------------------------------------------------------------------------

    def _drain(self) -> List[dict]:
        batch: List[dict] = []
        deadline = time.monotonic() + self.flush_interval_s
        while len(batch) < self.batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(self._q.get(timeout=min(remaining, 0.2)))
            except queue.Empty:
                if self._stop.is_set():
                    break
        return batch

    def _run(self) -> None:
        while not self._stop.is_set() or not self._q.empty():
            batch = self._drain()
            if batch:
                self._deliver(batch)
            elif self._stop.is_set():
                break

    def _deliver(self, batch: List[dict]) -> None:
        delay = 0.5
        for attempt in range(1, self.max_attempts + 1):
            try:
                self._sender(batch)
                self.sent += len(batch)
                return
            except Exception as exc:  # noqa: BLE001 - never let telemetry take the app down
                log.warning("event delivery failed (attempt %d/%d): %s", attempt, self.max_attempts, type(exc).__name__)
                if attempt < self.max_attempts and not self._stop.wait(delay):
                    delay = min(delay * 2, 10.0)
                else:
                    break
        self.failed_batches += 1
        self.dropped += len(batch)

    def _http_send(self, batch: List[dict]) -> None:
        body = json.dumps(batch).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Content-Type": "application/json", "X-Api-Key": self.api_key},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 - fixed operator-set URL
            if resp.status >= 300:
                raise RuntimeError(f"ingest returned {resp.status}")


# ── request -> event ──────────────────────────────────────────────────────────────────────

def build_event(
    service: str, method: str, route: Optional[str], status: int, duration_ms: float,
    context_text: str = "", exc: Optional[BaseException] = None,
) -> Dict[str, object]:
    label = f"{method} {route}" if route else f"{method} (unmatched)"
    level = "ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO"
    message = f"{label} -> {status}"
    exception = None
    if exc is not None:
        exception = f"{type(exc).__name__}: {exc}"
        message += f" {exception}"
    if context_text:
        message += f" | {context_text}"
    return {
        "id": uuid.uuid4().hex,
        "ts": time.time(),
        "service": service,
        "level": level,
        "route": label,
        "status": status,
        "duration_ms": round(duration_ms, 2),
        "message": message,
        "exception": exception,
    }


def install(
    app,
    emitter: EventEmitter,
    context_fn: Callable[[], str] = lambda: "",
    status_for_exc: Callable[[BaseException], int] = lambda exc: 500,
    path_prefix: str = "/todos",
) -> None:
    """Add middleware that emits one event per matching request. Control-plane and health paths are skipped."""

    @app.middleware("http")
    async def _emit_events(request, call_next):
        start = time.perf_counter()
        status, error = 500, None
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception as exc:  # noqa: BLE001 - re-raised; only observed here
            error, status = exc, status_for_exc(exc)
            raise
        finally:
            path = request.url.path
            if path == path_prefix or path.startswith(path_prefix + "/"):
                route_obj = request.scope.get("route")
                template = getattr(route_obj, "path", None)
                try:
                    ctx = context_fn()
                except Exception:  # noqa: BLE001
                    ctx = ""
                emitter.emit(build_event(
                    emitter.service, request.method, template, status,
                    (time.perf_counter() - start) * 1000.0, ctx, error,
                ))
