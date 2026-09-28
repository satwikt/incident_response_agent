"""One watcher cycle and the outbox pump, free of any ADK/LLM import so they can be unit tested end to end.

``tick`` = collect telemetry -> detect breaches -> update incidents -> queue diagnoses and notifications.
It never waits for the LLM: new incidents are handed to the diagnosis queue and the alert goes out from the outbox
once the diagnosis (real or templated) is stored.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, List, Optional

from db import events as store
from db import incidents, outbox

from . import config, notifier, ops_client, tools
from . import memory as memmod
from .detect import build_summary, find_breaches, healthy_routes
from .incident_manager import Action, IncidentConfig, process_cycle
from .prompts import build_alert_prompt, build_verify_prompt
from .thresholds import thresholds

log = logging.getLogger("copilot.cycle")

Submit = Callable[..., object]          # submit(incident_id, prompt, kind="full")

_memory: Any = None


def get_memory() -> Any:
    """The configured memory (Hindsight when MEMORY_MODE=on and a URL is set, otherwise a no-op)."""
    global _memory
    if _memory is None:
        _memory = memmod.make_memory(config.HINDSIGHT_URL, config.HINDSIGHT_BANK, config.MEMORY_MODE, config.MEMORY_MIN_SCORE)
    return _memory


def set_memory(mem: Any) -> None:
    global _memory
    _memory = mem


def _enrich(match: memmod.Recalled) -> dict:
    """A recalled match plus what we know locally about that incident (context, evidence, actions)."""
    d = match.as_dict()
    local = incidents.get(match.incident_id)
    if local:
        d["context"] = local.get("context_sample") or ""
        d["evidence"] = local.get("evidence") or []
        if not d["fix"] and local.get("actions"):
            d["fix"], failed = memmod.split_actions(local["actions"], set(config.ALLOWED_ACTIONS))
            d["failed"] = d["failed"] or failed
    return d


def recall_for(inc: dict, window: float, now: int) -> tuple[list, str]:
    """Recall similar past incidents for a new incident. Returns (enriched matches, current runtime context)."""
    since = now - int(max(window, 1.0) * 3 * 60_000)
    routes = [f["route"] for f in inc["fingerprints"] if f.get("route")]
    msg = (store.sample_error_message(config.SERVICE_NAME, routes[0] if routes else None, since)
           or store.sample_error_message(config.SERVICE_NAME, None, since))
    context = memmod.context_of(msg)
    mem = get_memory()
    if not mem.enabled:
        return [], context
    query = "; ".join(inc["evidence"]) + (f". Runtime context: {context}" if context else "")
    result = mem.recall(inc, query, exclude_id=inc["id"])
    detail = f"recall degraded ({result.error})" if result.degraded else ""
    matches = [_enrich(m) for m in result.matches]
    incidents.set_recalled(inc["id"], matches, detail)
    return matches, context


def incident_config() -> IncidentConfig:
    return IncidentConfig(
        recovery_windows=config.RECOVERY_WINDOWS,
        recurrence_window_ms=int(config.RECURRENCE_WINDOW_HOURS * 3_600_000),
        renotify_ms=int(config.RENOTIFY_MINUTES * 60_000),
        max_open=config.MAX_OPEN_INCIDENTS,
    )


def collect(window: float) -> dict:
    """Run the read-only tools for one window."""
    return {
        "health": tools.get_service_health(),
        "error": tools.get_error_rate(window),
        "latency": tools.get_latency(window),
        "slow": tools.get_slow_requests(thresholds.slow_request_min_ms, window),
        "logs": tools.get_recent_logs(",".join(thresholds.log_keywords), thresholds.log_scan_limit, window),
    }


def signature_for(window: float, now: Optional[int] = None) -> Callable[[Optional[str], str], str]:
    def sig(route: Optional[str], kind: str) -> str:
        if kind == "error_rate":
            return store.top_exception(config.SERVICE_NAME, route, window, now) or "5xx"
        return kind
    return sig


def run_cycle(data: dict, window: float, now: int, cfg: Optional[IncidentConfig] = None) -> List[Action]:
    """Detect breaches in ``data`` and update incident state. Returns the actions to carry out."""
    breaches = find_breaches(data["health"], data["error"], data["latency"], data["slow"], data["logs"], thresholds)
    healthy = healthy_routes(data["error"], data["latency"], data["slow"], thresholds, config.CLOSE_RATIO)
    traffic_ok = (data["error"].get("total_request_count") or 0) >= config.MIN_REQUESTS
    return process_cycle(config.SERVICE_NAME, breaches, signature_for(window, now), healthy, traffic_ok, now,
                         cfg or incident_config())


def _notify(event: str, incident: dict, now: int, suffix: str = "") -> None:
    key = f"discord:{incident['id']}:{event}{suffix}"
    if outbox.enqueue("discord", key, incident["id"], {"event": event}, now):
        if event in ("opened", "reminder", "regressed"):
            incidents.mark_notified(incident["id"], now)


def handle_actions(actions: List[Action], data: dict, submit: Submit, now: int) -> None:
    for a in actions:
        inc = a.incident
        if a.kind == "opened":
            # Recall first (a fraction of a second, fails open) so the opening alert can show what memory knows. The
            # alert then goes out NOW; the LLM diagnosis follows as a second message. A slow or rate-limited model must
            # never delay the page (and this keeps 'opened' ahead of 'resolved').
            try:
                matches, context = recall_for(inc, thresholds.interval_minutes, now)
            except Exception:  # noqa: BLE001 - memory must never block an alert
                log.exception("recall failed")
                matches, context = [], ""
            exact_fix = [m for m in matches if m.get("exact") and m.get("fix")]
            if exact_fix:
                # Memory-first: a known problem with a confirmed fix needs a light verification, not a full diagnosis.
                kind, prompt = "verify", build_verify_prompt(inc["evidence"], context, exact_fix[:2])
            else:
                summary = build_summary(data["health"], data["error"], data["latency"], data["slow"], data["logs"])
                kind, prompt = "full", build_alert_prompt(inc["evidence"], summary, matches or None)
            incidents.update_fields(inc["id"], diagnosis_mode=kind, context_sample=context or None)
            _notify("opened", incidents.get(inc["id"]) or inc, now)
            submit(inc["id"], prompt, kind)
            log.warning("Incident %s opened%s: %s", inc["id"], f" ({a.detail})" if a.detail else "", inc["evidence"])
        elif a.kind == "reminder":
            _notify("reminder", inc, now, f":{inc['notify_count']}")
        elif a.kind == "regressed":
            _notify("regressed", inc, now, f":{inc['breach_windows']}")
        elif a.kind == "resolved":
            _notify("resolved", inc, now)
            _enqueue_memory(inc, now)
            log.warning("Incident %s resolved (%s)", inc["id"], a.detail)


def _enqueue_memory(inc: dict, now: int) -> None:
    """Queue the retain for a resolved incident (durable: survives a restart or a Hindsight outage)."""
    if not get_memory().enabled:
        incidents.update_fields(inc["id"], memory_status="off")
        return
    if outbox.enqueue("memory", f"memory:retain:{inc['id']}", inc["id"], {"op": "retain"}, now):
        incidents.update_fields(inc["id"], memory_status="pending")


def on_diagnosis_done(incident_id: str, now: int) -> None:
    """The stored diagnosis (real or templated) is ready: send it as a follow-up, once, if the incident is still active.

    A diagnosis that arrives after the incident resolved is stored (it is history) but not sent: it would be noise.
    """
    inc = incidents.get(incident_id)
    if inc is None:
        return
    proposed = memmod.parse_proposed_action(inc.get("rca_text") or "", set(config.ALLOWED_ACTIONS))
    if proposed:
        incidents.update_fields(incident_id, proposed_action=proposed)
        inc = incidents.get(incident_id) or inc
    if inc["status"] != "RESOLVED":
        _notify("diagnosis", inc, now)


def tick(window: float, submit: Submit, now: Optional[int] = None) -> List[Action]:
    now = now if now is not None else store.now_ms()
    data = collect(window)
    actions = run_cycle(data, window, now)
    handle_actions(actions, data, submit, now)
    if not actions:
        log.info("Watcher: no incident changes (%d active).", len(incidents.list_active(config.SERVICE_NAME)))
    return actions


def resume_pending(window: float, submit: Submit) -> int:
    """After a restart: re-queue diagnoses that never finished. Returns how many were queued."""
    n = 0
    data = collect(window)
    summary = build_summary(data["health"], data["error"], data["latency"], data["slow"], data["logs"])
    for inc in incidents.list_active(config.SERVICE_NAME):
        if inc["rca_status"] == "pending":
            submit(inc["id"], build_alert_prompt(inc["evidence"], summary, inc.get("recalled") or None),
                   inc.get("diagnosis_mode") or "full")
            n += 1
    return n


# ── outbox pump ───────────────────────────────────────────────────────────────────────────

def backoff_ms(attempts: int, retry_after_s: float = 0.0) -> int:
    return int(max(retry_after_s, min(300.0, 5.0 * (2 ** max(0, attempts - 1)))) * 1000)


def deliver_memory(item: dict, now: int, window: float, mem: Any = None,
                   journal_fn: Callable[[], List[dict]] = ops_client.fetch_journal,
                   sleep: Callable[[float], None] = time.sleep, poll_attempts: int = 6,
                   poll_interval_s: float = 3.0) -> notifier.Delivery:
    """Retain a resolved incident in memory. Learns what fixed it from the app's action journal."""
    inc = incidents.get(item.get("incident_id") or "")
    if inc is None:
        return notifier.Delivery("fail", detail="incident not found")
    mem = mem if mem is not None else get_memory()
    if not mem.enabled:
        return notifier.Delivery("skip", detail="memory off")
    resolved_at = inc["resolved_at"] or now
    actions = ops_client.actions_between(journal_fn(), inc["opened_at"] - 30_000, resolved_at)
    fix, failed = memmod.split_actions(actions, set(config.ALLOWED_ACTIONS))
    incidents.set_actions(inc["id"], actions)
    source = inc.get("source") or "live"
    narrative = memmod.build_narrative(inc, actions, fix, failed, inc.get("context_sample") or "",
                                       (resolved_at - inc["opened_at"]) / 60_000, source)
    outcome = mem.retain(inc, narrative, actions, fix, failed, source)
    if outcome.status == "failed":
        incidents.update_fields(inc["id"], memory_status="failed", memory_detail=f"retain failed ({outcome.detail})")
        return notifier.Delivery("retry", detail=outcome.detail)
    incidents.update_fields(inc["id"], memory_status="retained", memory_op=outcome.operation_id or "")
    for _ in range(max(0, poll_attempts)):
        status = (mem.operation_status(outcome.operation_id or "") or "").lower()
        if status in ("completed", "succeeded", "success", "done"):
            incidents.update_fields(inc["id"], memory_status="indexed")
            break
        if status in ("failed", "error"):
            incidents.update_fields(inc["id"], memory_status="failed", memory_detail="indexing failed")
            return notifier.Delivery("retry", detail="indexing failed")
        sleep(poll_interval_s)
    return notifier.Delivery("ok")


def process_outbox_once(now: int, window: float,
                        deliver: Callable[..., notifier.Delivery] = notifier.deliver,
                        memory_deliver: Optional[Callable[..., notifier.Delivery]] = None) -> Optional[str]:
    """Deliver at most one due item. Returns the resulting status, or None if nothing was due."""
    item = outbox.claim(now, int(config.OUTBOX_LEASE_S * 1000))
    if item is None:
        return None
    if item["kind"] == "memory":
        result = (memory_deliver or deliver_memory)(item, now, window)
    else:
        result = deliver(item, now, window)
    if result.status == "ok":
        outbox.complete(item["id"])
        return "done"
    if result.status == "skip":
        outbox.skip(item["id"], result.detail)
        return "skipped"
    if result.status == "fail":
        return outbox.retry(item["id"], 0, result.detail, now, max_attempts=item["attempts"])   # dead immediately
    return outbox.retry(item["id"], backoff_ms(item["attempts"], result.retry_after_s), result.detail, now,
                        config.OUTBOX_MAX_ATTEMPTS)
