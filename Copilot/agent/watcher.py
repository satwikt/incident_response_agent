"""Proactive background watcher for the SRE Copilot.

Polls observability sources (Prometheus, Loki, Tempo, Health) on a
configurable interval, evaluates breaches against thresholds, invokes the
ADK agent to generate a full RCA, and dispatches a Discord notification.

Usage
-----
Start from a FastAPI lifespan context:

    from agent.watcher import start_watcher, stop_watcher

    @asynccontextmanager
    async def lifespan(app):
        task = await start_watcher()
        yield
        await stop_watcher(task)
"""

import asyncio
import logging
from typing import List, Optional

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from .agent import root_agent
from .detect import build_summary, evaluate
from .notifier import send_discord_alert
from .thresholds import thresholds
from .tools import (
    get_error_rate,
    get_latency,
    get_recent_logs,
    get_service_health,
    get_slow_requests,
)

log = logging.getLogger("copilot.watcher")

# ── ADK runner (shared, stateless sessions per alert cycle) ──────────────────
_session_service = InMemorySessionService()
_runner = Runner(
    agent=root_agent,
    app_name="sre_copilot_watcher",
    session_service=_session_service,
)


# ── RCA generation via ADK agent ─────────────────────────────────────────────

async def _generate_rca(breaches: List[str], telemetry_summary: str) -> str:
    """Invoke the ADK agent asynchronously to produce a full RCA narrative."""
    prompt = (
        "PROACTIVE ALERT — the following threshold breaches were automatically "
        "detected by the SRE Copilot watcher:\n\n"
        + "\n".join(f"  - {b}" for b in breaches)
        + "\n\n"
        "Raw telemetry snapshot:\n"
        + telemetry_summary
        + "\n\n"
        "Please perform a complete RCA following your standard format:\n"
        "1. Symptom\n2. Evidence\n3. Code locus\n4. Root cause\n"
        "5. Implementation recommendation\n\n"
        "Call whatever tools you need to gather additional live data."
    )

    session_id = f"watcher-alert-{asyncio.get_event_loop().time():.0f}"
    await _session_service.create_session(
        app_name="sre_copilot_watcher",
        user_id="watcher",
        session_id=session_id,
    )

    rca_parts: List[str] = []
    try:
        async for event in _runner.run_async(
            user_id="watcher",
            session_id=session_id,
            new_message=Content(role="user", parts=[Part(text=prompt)]),
        ):
            # Collect final model text response
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        rca_parts.append(part.text)
    except Exception as exc:
        log.error("ADK agent RCA generation failed: %s", exc)
        return f"RCA generation failed: {exc}"

    return "\n".join(rca_parts) or "Agent returned no RCA text."


# ── Main poll cycle ───────────────────────────────────────────────────────────

async def _poll_once() -> None:
    """Execute one full monitoring cycle."""
    log.info("Watcher: starting poll cycle.")

    # All lookback windows = interval_minutes so each poll covers exactly
    # the time since the last one — no gaps, no stale historical incidents.
    window = thresholds.interval_minutes

    # Collect all telemetry in parallel (run blocking httpx calls in executor)
    loop = asyncio.get_event_loop()
    health, error_data, latency_data, slow_data, logs_data = await asyncio.gather(
        loop.run_in_executor(None, get_service_health),
        loop.run_in_executor(None, get_error_rate, window),
        loop.run_in_executor(None, get_latency, window),
        loop.run_in_executor(None, get_slow_requests, thresholds.slow_request_min_ms, window),
        loop.run_in_executor(
            None,
            get_recent_logs,
            ",".join(thresholds.log_keywords),
            thresholds.log_scan_limit,
            window,
        ),
    )

    breaches = evaluate(health, error_data, latency_data, slow_data, logs_data, thresholds)

    if not breaches:
        log.info("Watcher: all clear — no thresholds breached.")
        return

    log.warning("Watcher: %d breach(es) detected: %s", len(breaches), breaches)

    telemetry_summary = build_summary(health, error_data, latency_data, slow_data, logs_data)

    # Generate RCA via ADK agent (async)
    log.info("Watcher: invoking ADK agent for RCA generation...")
    rca_text = await _generate_rca(breaches, telemetry_summary)
    log.info("Watcher: RCA generated (%d chars).", len(rca_text))

    # Dispatch Discord notification (blocking I/O → executor)
    await loop.run_in_executor(
        None,
        send_discord_alert,
        breaches,
        rca_text,
        thresholds.interval_minutes,
    )


# ── Lifecycle management ──────────────────────────────────────────────────────

async def _watcher_loop() -> None:
    """Infinite loop: poll → sleep → repeat."""
    interval_seconds = thresholds.interval_minutes * 60
    log.info(
        "Watcher started — polling every %.1f minute(s).",
        thresholds.interval_minutes,
    )
    while True:
        try:
            await _poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never crash the watcher; log and continue on next cycle
            log.error("Watcher poll cycle failed unexpectedly: %s", exc, exc_info=True)

        await asyncio.sleep(interval_seconds)


async def start_watcher() -> asyncio.Task:
    """Create and return the background watcher asyncio Task.

    Call this inside a FastAPI lifespan startup block.
    """
    task = asyncio.create_task(_watcher_loop(), name="sre_copilot_watcher")
    log.info("Watcher task created: %s", task.get_name())
    return task


async def stop_watcher(task: Optional[asyncio.Task]) -> None:
    """Gracefully cancel the watcher Task on application shutdown."""
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            log.info("Watcher task cancelled cleanly.")
