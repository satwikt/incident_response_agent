"""Poll observability sources and deliver normalized evidence to Copilot.

The adapter is a separate process: it never sits in an application request path.
Each source failure is isolated so one unavailable backend cannot stop the rest.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError

from .client import build_events, send_events
from .events import Evidence

Source = Callable[[], Iterable[Evidence]]
Sender = Callable[..., bytes]
Sleep = Callable[[float], None]

MAX_BATCH_SIZE = 500
MAX_BODY_BYTES = 1_000_000  # ingest rejects bodies over 1 MiB


def parse_ingest_keys(raw: str) -> dict[str, str]:
    """Parse ``service:key,service2:key2`` into ``{service: key}``.

    The Copilot binds every ingest key to exactly one service and rejects a
    batch naming any other service, so the adapter sends one request per
    service with that service's key.
    """
    out: dict[str, str] = {}
    for pair in (raw or "").split(","):
        service, sep, key = pair.strip().partition(":")
        if sep and service.strip() and key.strip():
            out[service.strip()] = key.strip()
    return out


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry configuration owned by the adapter (never the client)."""

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 4.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be >= 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must be >= 0")

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff for the given 1-based attempt number."""
        return min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))


@dataclass
class IngestTotals:
    fetched: int = 0
    accepted: int = 0
    duplicates: int = 0
    dropped: int = 0
    retries: int = 0
    failed_batches: int = 0


@dataclass(frozen=True)
class PollReport:
    fetched: int
    accepted: int
    duplicates: int
    dropped: int
    retries: int
    failed_batches: int
    source_errors: Mapping[str, str]


def chunk_events(
    events: Iterable[dict[str, Any]],
    *,
    limit: int = MAX_BATCH_SIZE,
    max_bytes: int = MAX_BODY_BYTES,
) -> list[list[dict[str, Any]]]:
    """Split normalized events by both count and serialized body size."""
    if not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_BATCH_SIZE}")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 2  # enclosing JSON array
    for event in events:
        encoded = len(json.dumps(event, separators=(",", ":"))) + 1
        if current and (len(current) >= limit or size + encoded > max_bytes):
            batches.append(current)
            current = []
            size = 2
        current.append(event)
        size += encoded
    if current:
        batches.append(current)
    return batches


def is_retryable(exc: BaseException) -> bool:
    """Retry only transient transport and server-side failures."""
    if isinstance(exc, HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    return isinstance(exc, (URLError, TimeoutError, ConnectionError, OSError))


def _receipt(payload: bytes, fallback: int) -> Tuple[int, int]:
    """Read accepted/duplicate counts from the ingest response when present."""
    try:
        data: Any = json.loads(payload)
    except (TypeError, ValueError):
        return fallback, 0
    if not isinstance(data, Mapping):
        return fallback, 0
    accepted = data.get("accepted")
    duplicates = data.get("duplicates")
    if not isinstance(accepted, int) or accepted < 0 or accepted > fallback:
        return fallback, 0
    if not isinstance(duplicates, int) or duplicates < 0:
        duplicates = fallback - accepted
    return accepted, duplicates


class Poller:
    """Fetch evidence from named sources and ingest it with bounded retries."""

    def __init__(
        self,
        ingest_url: str,
        api_key: str,
        *,
        batch_size: int = MAX_BATCH_SIZE,
        timeout: float = 5.0,
        policy: Optional[RetryPolicy] = None,
        sender: Sender = send_events,
        sleep: Sleep = time.sleep,
        service_keys: Optional[Mapping[str, str]] = None,
        watch_service: Optional[str] = None,
    ) -> None:
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        self._ingest_url = ingest_url
        self._api_key = api_key
        self._batch_size = batch_size
        self._timeout = timeout
        self._policy = policy or RetryPolicy()
        self._sender = sender
        self._sleep = sleep
        self._service_keys = dict(service_keys or {})
        self._watch_service = watch_service.strip() if watch_service else None
        self.totals = IngestTotals()

    def _by_service(
        self, events: Sequence[dict[str, Any]]
    ) -> Tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
        """Group events by service so that one request never mixes services.

        The Copilot rejects a whole batch when an event names a service other
        than the one its key is bound to, so each service is chunked and sent
        on its own.

        A service with no key cannot be sent at all; its events are dropped and
        counted instead of being sent with a key bound to another service,
        which would burn that service's rate limit on a 403.

        When a watched service is set, evidence for any other service is
        dropped: the Copilot watches one service, so the rest can never raise
        a breach and only pollute its event store.
        """
        grouped: dict[str, list[dict[str, Any]]] = {}
        missing: dict[str, int] = {}
        for event in events:
            service = str(event.get("service") or "")
            if self._watch_service and service != self._watch_service:
                missing[service] = missing.get(service, 0) + 1
                continue
            if self._service_keys and service not in self._service_keys:
                missing[service] = missing.get(service, 0) + 1
                continue
            grouped.setdefault(service, []).append(event)
        return grouped, missing

    def _drop_reason(self, service: str) -> str:
        """Why events for ``service`` were not sent."""
        if self._watch_service and service != self._watch_service:
            return f"service {service!r} is not the watched service {self._watch_service!r}"
        return f"no ingest key for service {service!r}"

    def _key_for(self, event: Mapping[str, Any]) -> str:
        """The ingest key bound to the event's service, falling back to the default key."""
        if not self._service_keys:
            return self._api_key
        service = str(event.get("service") or "")
        return self._service_keys.get(service, self._api_key)

    def run_once(self, sources: Mapping[str, Source]) -> PollReport:
        """Poll every source once, isolate failures, and ingest all evidence."""
        fetched = accepted = duplicates = dropped = retries = failed_batches = 0
        errors: dict[str, str] = {}

        for name, source in sources.items():
            try:
                evidence = list(source())
            except Exception as exc:  # noqa: BLE001 - one backend must not stop the rest
                errors[name] = f"{type(exc).__name__}: {exc}"
                continue
            fetched += len(evidence)
            try:
                events = build_events(evidence)
            except Exception as exc:  # noqa: BLE001 - isolate normalization failures
                errors[name] = f"normalize: {type(exc).__name__}: {exc}"
                continue
            grouped, missing = self._by_service(events)
            for service, count in sorted(missing.items()):
                dropped += count
                errors[f"{name}:ingest-keys"] = (
                    f"{self._drop_reason(service)}: {count} event(s) dropped"
                )
            for service in sorted(grouped):
                for batch in chunk_events(grouped[service], limit=self._batch_size):
                    batch_accepted, batch_duplicates, batch_retries, error = self._send(batch)
                    retries += batch_retries
                    if error is not None:
                        failed_batches += 1
                        dropped += len(batch)
                        errors[f"{name}:ingest"] = error
                    else:
                        accepted += batch_accepted
                        duplicates += batch_duplicates

        report = PollReport(
            fetched=fetched,
            accepted=accepted,
            duplicates=duplicates,
            dropped=dropped,
            retries=retries,
            failed_batches=failed_batches,
            source_errors=errors,
        )
        self._accumulate(report)
        return report

    def run_forever(
        self,
        sources: Mapping[str, Source],
        *,
        interval: float = 30.0,
        max_cycles: Optional[int] = None,
    ) -> Iterable[PollReport]:
        """Yield one report per cycle; the caller owns process lifecycle."""
        if interval < 0:
            raise ValueError("interval must be >= 0")
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            yield self.run_once(sources)
            cycles += 1
            if max_cycles is None or cycles < max_cycles:
                self._sleep(interval)

    def _send(
        self, batch: Sequence[dict[str, Any]]
    ) -> Tuple[int, int, int, Optional[str]]:
        """Send one batch, returning (accepted, duplicates, retries, error)."""
        retries = 0
        api_key = self._key_for(batch[0]) if batch else self._api_key
        for attempt in range(1, self._policy.attempts + 1):
            try:
                payload = self._sender(
                    self._ingest_url,
                    api_key,
                    batch,
                    timeout=self._timeout,
                )
                accepted, duplicates = _receipt(payload, len(batch))
                return accepted, duplicates, retries, None
            except Exception as exc:  # noqa: BLE001 - classified below
                if attempt >= self._policy.attempts or not is_retryable(exc):
                    return 0, 0, retries, f"{type(exc).__name__}: {exc}"
                retries += 1
                self._sleep(self._policy.delay_for(attempt))
        return 0, 0, retries, "retry policy exhausted"

    def _accumulate(self, report: PollReport) -> None:
        self.totals.fetched += report.fetched
        self.totals.accepted += report.accepted
        self.totals.duplicates += report.duplicates
        self.totals.dropped += report.dropped
        self.totals.retries += report.retries
        self.totals.failed_batches += report.failed_batches
