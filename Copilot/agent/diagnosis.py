"""Diagnosis queue: runs the LLM agent for new incidents, off the detection path.

Detection never waits on the LLM. New incidents are queued here and processed one at a time under a budget:

* **Hourly cap** (``max_runs_per_hour``): bounds cost and provider quota even if fingerprints churn.
* **Daily token budget** (`daily_token_budget`): free tiers have daily quotas; once the tokens recorded on the last
  24 hours of incidents reach the budget, the model is not called and the alert says why.
* **Circuit breaker**: after ``breaker_failures`` consecutive failures the queue stops calling the model for
  ``breaker_pause_s`` and answers with a templated message instead, so a dead provider is not hammered.
* **Timeout** per run.
* **Rate limits are waited out**: a provider quota error (429) waits `rate_limit_wait_s` and retries up to
  `rate_limit_retries` times. Only if that is exhausted does it count as one failure toward the breaker.

Whatever happens, the incident gets an ``rca_text`` (real or templated) and ``on_done`` fires, so the alert is always
sent, with or without an AI diagnosis.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Deque, Optional, Set

from db import incidents

log = logging.getLogger("copilot.diagnosis")

@dataclass
class DiagnosisResult:
    """What a diagnosis returns: the text plus what it cost."""
    text: str
    llm_calls: Optional[int] = None
    tokens: Optional[int] = None


Diagnose = Callable[[str], Awaitable["str | DiagnosisResult"]]


_RETRY_IN = re.compile(r"try again in\s+(?:(\d+)m)?\s*([\d.]+)s", re.IGNORECASE)


def retry_after_seconds(exc: BaseException) -> Optional[float]:
    """The delay a provider reports ('Please try again in 27.8s' / '1m2.5s'), if the error message has one."""
    m = _RETRY_IN.search(str(exc))
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + float(m.group(2))


def is_rate_limit(exc: BaseException) -> bool:
    """True for provider quota errors (HTTP 429). These mean 'wait', not 'the model is broken'."""
    text = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in text or "rate limit" in text or "429" in text


def templated(reason: str) -> str:
    return f"Automatic diagnosis unavailable ({reason}). The breach details above are the current evidence."


class DiagnosisService:
    def __init__(self, diagnose: Diagnose, *, verify: Optional[Diagnose] = None, timeout_s: float = 120.0,
                 max_runs_per_hour: int = 12,
                 breaker_failures: int = 3, breaker_pause_s: float = 300.0,
                 daily_token_budget: int = 0, rate_limit_retries: int = 2, rate_limit_wait_s: float = 45.0,
                 on_done: Optional[Callable[[str], None]] = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._diagnose = diagnose
        self._verify = verify or diagnose
        self.daily_token_budget = max(0, daily_token_budget)
        self.rate_limit_retries = max(0, rate_limit_retries)
        self.rate_limit_wait_s = rate_limit_wait_s
        self._sleep = sleep
        self.timeout_s = timeout_s
        self.max_runs_per_hour = max_runs_per_hour
        self.breaker_failures = max(1, breaker_failures)
        self.breaker_pause_s = breaker_pause_s
        self._on_done = on_done
        self._clock = clock
        self._queue: "asyncio.Queue[tuple[str, str, str]]" = asyncio.Queue()
        self._queued: Set[str] = set()
        self._runs: Deque[float] = deque()
        self._consecutive_failures = 0
        self._breaker_until = 0.0

    # -- producer ------------------------------------------------------------------------------

    def submit(self, incident_id: str, prompt: str, kind: str = "full") -> bool:
        """Queue a diagnosis ('full' agent run, or 'verify': the light memory-first check). False if already queued."""
        if incident_id in self._queued:
            return False
        self._queued.add(incident_id)
        self._queue.put_nowait((incident_id, prompt, kind))
        return True

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # -- consumer ------------------------------------------------------------------------------

    async def run_forever(self) -> None:
        while True:
            incident_id, prompt, kind = await self._queue.get()
            try:
                await self.process(incident_id, prompt, kind)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad job must not stop the worker
                log.exception("diagnosis worker error for %s", incident_id)
            finally:
                self._queued.discard(incident_id)

    async def _run_with_rate_limit_waits(self, prompt: str, kind: str = "full") -> DiagnosisResult:
        fn = self._verify if kind == "verify" else self._diagnose
        for attempt in range(self.rate_limit_retries + 1):
            try:
                raw = await asyncio.wait_for(fn(prompt), timeout=self.timeout_s)
                result = raw if isinstance(raw, DiagnosisResult) else DiagnosisResult(text=raw or "")
                result.text = (result.text or "").strip()
                if not result.text:
                    raise ValueError("empty diagnosis")
                return result
            except Exception as exc:  # noqa: BLE001
                if is_rate_limit(exc) and attempt < self.rate_limit_retries:
                    reported = retry_after_seconds(exc)
                    # Use the provider's own delay (plus a margin) when it gives one, never less than 5 s or more than 120 s.
                    wait = min(120.0, max(5.0, reported + 3.0)) if reported is not None else self.rate_limit_wait_s
                    log.warning("provider rate limit; waiting %.0fs before retry %d/%d", wait,
                                attempt + 1, self.rate_limit_retries)
                    await self._sleep(wait)
                    continue
                raise
        raise RuntimeError("unreachable")

    async def process(self, incident_id: str, prompt: str, kind: str = "full") -> str:
        """Diagnose one incident and store the result. Returns ok | failed | breaker | cap."""
        now = self._clock()
        while self._runs and now - self._runs[0] > 3600:
            self._runs.popleft()

        status, text, calls, tokens = "ok", "", None, None
        if now < self._breaker_until:
            status, text = "breaker", templated("the model was failing repeatedly; paused briefly")
        elif self.daily_token_budget and incidents.tokens_used_since(int((now - 86400) * 1000)) >= self.daily_token_budget:
            status, text = "budget", templated(f"daily model budget of {self.daily_token_budget} tokens reached")
        elif len(self._runs) >= self.max_runs_per_hour:
            status, text = "cap", templated(f"hourly limit of {self.max_runs_per_hour} diagnoses reached")
        else:
            self._runs.append(now)
            try:
                result = await self._run_with_rate_limit_waits(prompt, kind)
                text, calls, tokens = result.text, result.llm_calls, result.tokens
                self._consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001 - classify, never raise
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.breaker_failures:
                    self._breaker_until = self._clock() + self.breaker_pause_s
                    self._consecutive_failures = 0
                    log.warning("diagnosis circuit breaker open for %.0fs", self.breaker_pause_s)
                # Only the exception type is stored/shown: messages can contain provider details.
                status, text = "failed", templated(type(exc).__name__)
                log.warning("diagnosis failed for %s: %s", incident_id, type(exc).__name__)

        incidents.set_rca(incident_id, text, "ok" if status == "ok" else status, calls, tokens)
        incidents.record_event(incident_id, f"diagnosis:{status}", "agent", "", int(self._clock() * 1000))
        if self._on_done:
            self._on_done(incident_id)
        return status
