"""Background tasks: the watcher loop, the diagnosis worker and the outbox pump.

Detection (``cycle.tick``) never waits for the LLM. New incidents go to the ``DiagnosisService`` queue; alerts leave
through the durable outbox once the diagnosis is stored. Run with a single process (``--workers 1``).

Usage: start from a FastAPI lifespan context:

    task = await start_watcher()
    yield
    await stop_watcher(task)
"""

import asyncio
import logging
from typing import Optional

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from db import events as store

from . import config, cycle
from .agent import root_agent, verifier_agent
from .diagnosis import DiagnosisResult, DiagnosisService
from .thresholds import thresholds

log = logging.getLogger("copilot.watcher")

_session_service = InMemorySessionService()
_runner = Runner(agent=root_agent, app_name="incident_copilot_watcher", session_service=_session_service)
_verify_runner = Runner(agent=verifier_agent, app_name="incident_copilot_verifier", session_service=_session_service)


async def _generate_rca(prompt: str, runner=None, app_name: str = "incident_copilot_watcher") -> DiagnosisResult:
    """Run an ADK agent on ``prompt`` and return its final text and cost. Raises on any failure (the queue handles it)."""
    runner = runner or _runner
    session_id = f"watcher-alert-{asyncio.get_running_loop().time():.3f}"
    await _session_service.create_session(app_name=app_name, user_id="watcher", session_id=session_id)
    parts, calls, tokens = [], 0, 0
    async for event in runner.run_async(user_id="watcher", session_id=session_id,
                                         new_message=Content(role="user", parts=[Part(text=prompt)]),
                                         run_config=RunConfig(max_llm_calls=config.AGENT_MAX_LLM_CALLS)):
        content = getattr(event, "content", None)
        if content and getattr(content, "role", None) == "model" and not getattr(event, "partial", False):
            calls += 1
        usage = getattr(event, "usage_metadata", None)
        if usage and getattr(usage, "total_token_count", None):
            tokens += usage.total_token_count
        if event.is_final_response() and content and content.parts:
            parts.extend(p.text for p in content.parts if getattr(p, "text", None))
    return DiagnosisResult(text="\n".join(parts), llm_calls=calls or None, tokens=tokens or None)


async def _verify_rca(prompt: str) -> DiagnosisResult:
    """The light, tool-less verification used by the memory-first path."""
    return await _generate_rca(prompt, runner=_verify_runner, app_name="incident_copilot_verifier")


async def _watcher_loop(diagnosis: DiagnosisService) -> None:
    loop = asyncio.get_running_loop()
    interval_s = thresholds.interval_minutes * 60
    window = thresholds.interval_minutes

    def submit(incident_id: str, prompt: str, kind: str = "full") -> None:
        # tick() runs in a worker thread; asyncio.Queue is not thread safe.
        loop.call_soon_threadsafe(diagnosis.submit, incident_id, prompt, kind)

    try:
        resumed = await loop.run_in_executor(None, cycle.resume_pending, window, submit)
        if resumed:
            log.warning("Resumed %d unfinished diagnosis job(s).", resumed)
    except Exception:  # noqa: BLE001
        log.exception("resume_pending failed")

    log.info("Watcher started: every %.2f minute(s).", thresholds.interval_minutes)
    while True:
        try:
            await loop.run_in_executor(None, cycle.tick, window, submit)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never crash the loop
            log.exception("Watcher cycle failed")
        await asyncio.sleep(interval_s)


async def _outbox_loop(window: float) -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            status = await loop.run_in_executor(None, cycle.process_outbox_once, store.now_ms(), window)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Outbox pump failed")
            status = None
        await asyncio.sleep(0.2 if status else 2.0)      # keep draining while there is work


async def _supervisor() -> None:
    window = thresholds.interval_minutes
    diagnosis = DiagnosisService(
        _generate_rca,
        verify=_verify_rca,
        timeout_s=config.AGENT_TIMEOUT_S,
        max_runs_per_hour=config.AGENT_MAX_RUNS_PER_HOUR,
        daily_token_budget=config.AGENT_DAILY_TOKEN_BUDGET,
        rate_limit_retries=config.AGENT_RATE_LIMIT_RETRIES,
        rate_limit_wait_s=config.AGENT_RATE_LIMIT_WAIT_S,
        breaker_failures=config.BREAKER_FAILURES,
        breaker_pause_s=config.BREAKER_PAUSE_S,
        on_done=lambda incident_id: cycle.on_diagnosis_done(incident_id, store.now_ms()),
    )
    tasks = [
        asyncio.create_task(_watcher_loop(diagnosis), name="watcher"),
        asyncio.create_task(diagnosis.run_forever(), name="diagnosis"),
        asyncio.create_task(_outbox_loop(window), name="outbox"),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def start_watcher() -> asyncio.Task:
    """Start the watcher, diagnosis worker and outbox pump under one supervisor task."""
    task = asyncio.create_task(_supervisor(), name="incident_copilot_supervisor")
    log.info("Watcher supervisor created.")
    return task


async def stop_watcher(task: Optional[asyncio.Task]) -> None:
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            log.info("Watcher stopped cleanly.")
