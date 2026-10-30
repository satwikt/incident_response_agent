"""FastAPI router for Incident Dashboard backend APIs."""

import logging
import re
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from agent import config, cycle, tools
from agent.memory import OP_DONE, OP_FAILED, RetainOutcome, build_narrative, parse_proposed_action, split_actions
from api.schema import (
    IncidentDetail,
    IncidentEvent,
    IncidentFeedbackRequest,
    IncidentFeedbackResponse,
    IncidentSummary,
    MemoryItem,
    PastIncidentsMemoryResponse,
    TelemetryOverviewResponse,
)
from db import events as store
from db import incidents

log = logging.getLogger("copilot.dashboard")
router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/meta/allowed-actions")
def get_allowed_actions() -> dict:
    """Return the list of remediation actions the server accepts for incident feedback."""
    system_extended_actions = {
        "create_missing_table", "run_db_migration", "apply_schema_migration",
        "disable_fault", "hotfix_query", "patch_code", "flush_pool",
        "restart_worker", "rollback_config", "rollback_release", "enable_fallback",
    }
    return {"allowed_actions": sorted(list(set(config.ALLOWED_ACTIONS) | system_extended_actions))}


def _llm_suggest_next_action(rca_text: str, failed_actions: List[str], service: str) -> Optional[str]:
    """Call the LLM synchronously to suggest the next remediation action after failures.

    Uses LiteLLM directly (already a dependency via the agent model stack) so this can be
    called from a synchronous FastAPI endpoint without spinning up an asyncio event loop.
    Returns the model-suggested action name, or None on any error.
    """
    from agent.llm import get_groq_fallback_model, litellm_kwargs
    from agent.memory import parse_proposed_action
    from agent.prompts import build_next_action_prompt

    try:
        import litellm  # noqa: PLC0415

        prompt = build_next_action_prompt(rca_text, failed_actions, service)
        kwargs = litellm_kwargs()
        # Use Groq as the synchronous call target — fast, cheap, and already configured.
        model = get_groq_fallback_model()
        api_key = getattr(config, "GROQ_API_KEY", "") or None

        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_key=api_key,
            **{k: v for k, v in kwargs.items() if k not in ("num_retries", "fallbacks")},
        )
        reply = (response.choices[0].message.content or "").strip()
        suggested = parse_proposed_action(reply, None)  # allow any snake_case, not just ALLOWED_ACTIONS
        if suggested and suggested not in ("none", "noop"):
            log.info("LLM suggested next action '%s' for service '%s'", suggested, service)
            return suggested
    except Exception as exc:
        log.warning("LLM next-action suggestion failed (%s); no recommendation returned", exc)
    return None


def _reconcile_memory(inc: dict) -> dict:
    """A queued retain ('retained') becomes 'indexed' or 'failed' once Hindsight reports the operation's outcome.

    The retain call only queues the work; Hindsight extracts facts asynchronously and can fail afterwards, so the status
    shown to the operator is checked against the operation instead of being assumed.
    """
    op = inc.get("memory_op")
    if inc.get("memory_status") != "retained" or not op:
        return inc
    mem = cycle.get_memory()
    if not getattr(mem, "enabled", False):
        return inc
    try:
        status = (mem.operation_status(op) or "").lower()
    except Exception as exc:  # noqa: BLE001 - a status check must never break the dossier
        log.warning("memory operation status check failed (%s)", exc)
        return inc
    if status in OP_DONE:
        incidents.update_fields(inc["id"], memory_status="indexed")
        return {**inc, "memory_status": "indexed"}
    if status in OP_FAILED:
        detail = "Hindsight failed to index this memory (see the hindsight container log)"
        incidents.update_fields(inc["id"], memory_status="failed", memory_detail=detail)
        return {**inc, "memory_status": "failed", "memory_detail": detail}
    return inc


def _retain_message(status: str, detail: str = "") -> str:
    if status == "retained":
        return "Incident resolved. The verified fix was queued for Hindsight memory; its indexing status is on the incident."
    if status == "skipped":
        return "Incident resolved. Memory is off, so the verified fix is recorded in the incident only."
    return f"Incident resolved, but the fix could not be queued for Hindsight memory ({detail or status})."


@router.get("/incidents", response_model=List[IncidentSummary])
def get_incidents(
    service: Optional[str] = Query(None, description="Filter by service name"),
    status: Optional[str] = Query(None, description="Filter by status (e.g. OPEN, RESOLVED)"),
    limit: int = Query(50, ge=1, le=500, description="Max incidents to return"),
) -> List[IncidentSummary]:
    """List recent and active incidents with optional status and service filters."""
    rows = incidents.list_all(service=service, status=status, limit=limit)
    return [
        IncidentSummary(
            id=r["id"],
            service=r["service"],
            status=r["status"],
            severity=r["severity"],
            opened_at=r["opened_at"],
            last_seen_at=r["last_seen_at"],
            resolved_at=r.get("resolved_at"),
            resolved_by=r.get("resolved_by"),
            fingerprint=r.get("fingerprint", ""),
            evidence=r.get("evidence") or [],
            rca_text=r.get("rca_text"),
            rca_status=r.get("rca_status", "pending"),
            proposed_action=r.get("proposed_action"),
            actions=r.get("actions") or [],
            recalled=r.get("recalled") or [],
            version=r.get("version", 1),
            diagnosis_mode=r.get("diagnosis_mode"),
            llm_calls=r.get("llm_calls"),
            llm_tokens=r.get("llm_tokens"),
        )
        for r in rows
    ]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident_detail(incident_id: str) -> IncidentDetail:
    """Get full incident dossier including timeline events and diagnostic context."""
    inc = incidents.get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    inc = _reconcile_memory(inc)

    events_raw = incidents.list_events(incident_id)
    timeline = [
        IncidentEvent(
            id=e["id"],
            incident_id=e["incident_id"],
            ts=e["ts"],
            kind=e["kind"],
            actor=e["actor"],
            detail=e.get("detail") or "",
        )
        for e in events_raw
    ]

    return IncidentDetail(
        id=inc["id"],
        service=inc["service"],
        status=inc["status"],
        severity=inc["severity"],
        opened_at=inc["opened_at"],
        last_seen_at=inc["last_seen_at"],
        resolved_at=inc.get("resolved_at"),
        resolved_by=inc.get("resolved_by"),
        fingerprint=inc.get("fingerprint", ""),
        evidence=inc.get("evidence") or [],
        rca_text=inc.get("rca_text"),
        rca_status=inc.get("rca_status", "pending"),
        proposed_action=inc.get("proposed_action"),
        actions=inc.get("actions") or [],
        recalled=inc.get("recalled") or [],
        version=inc.get("version", 1),
        diagnosis_mode=inc.get("diagnosis_mode"),
        llm_calls=inc.get("llm_calls"),
        llm_tokens=inc.get("llm_tokens"),
        fingerprints=inc.get("fingerprints") or [],
        context_sample=inc.get("context_sample"),
        memory_status=inc.get("memory_status"),
        timeline=timeline,
    )


@router.get("/memory/past-incidents", response_model=PastIncidentsMemoryResponse)
def get_past_incident_memories() -> PastIncidentsMemoryResponse:
    """Return historical memories and verified fixes stored in Hindsight (or local database fallback)."""
    mem = cycle.get_memory()
    memories: List[MemoryItem] = []
    seen_ids = set()

    # 1. If Hindsight memory is enabled, recall stored memories
    if getattr(mem, "enabled", False):
        try:
            # Query Hindsight memory bank for past incidents
            recall_res = mem.recall(
                incident={"fingerprints": []},
                query="incident root cause resolution fix error outage",
                exclude_id=None,
            )
            for m in getattr(recall_res, "matches", []):
                fix = m.fix
                failed = m.failed or []
                local = incidents.get(m.incident_id)
                if not fix and local:
                    local_fix, local_failed = split_actions(local.get("actions") or [], None)
                    fix = local_fix or local.get("proposed_action")
                    if not failed and local_failed:
                        failed = local_failed

                memories.append(
                    MemoryItem(
                        incident_id=m.incident_id,
                        narrative=m.text,
                        score=m.score,
                        fix=fix,
                        failed=failed,
                        source=m.source,
                        tags=m.tags or [],
                    )
                )
                seen_ids.add(m.incident_id)
        except Exception as exc:
            log.warning("Hindsight memory query failed (%s); falling back to database", exc)

    # 2. Augment with resolved incidents from local SQLite store (fail-safe and comprehensive)
    resolved_incidents = incidents.list_all(status="RESOLVED", limit=100)
    for inc in resolved_incidents:
        inc_id = inc["id"]
        if inc_id in seen_ids:
            continue
        fix, failed = split_actions(inc.get("actions") or [], None)
        if not fix and inc.get("proposed_action"):
            fix = inc["proposed_action"]

        narrative_parts = [
            f"Incident {inc_id} ({inc.get('service', 'unknown')})",
            f"Evidence: {'; '.join(inc.get('evidence') or [])}",
            f"RCA: {inc.get('rca_text') or 'No diagnosis'}",
        ]
        if inc.get("context_sample"):
            narrative_parts.append(f"Context: {inc['context_sample']}")
        if fix:
            narrative_parts.append(f"Working fix: {fix}")

        memories.append(
            MemoryItem(
                incident_id=inc_id,
                narrative=". ".join(narrative_parts),
                score=1.0,
                fix=fix,
                failed=failed,
                source=inc.get("source") or "live",
                tags=[f"svc:{inc.get('service')}", "outcome:resolved"] + ([f"fix:{fix}"] if fix else []),
            )
        )
        seen_ids.add(inc_id)

    return PastIncidentsMemoryResponse(
        enabled=bool(getattr(mem, "enabled", False)),
        bank_id=config.HINDSIGHT_BANK,
        total=len(memories),
        memories=memories,
    )


@router.get("/telemetry/overview", response_model=TelemetryOverviewResponse)
def get_telemetry_overview(
    service: Optional[str] = Query(None, description="Target service (defaults to config.SERVICE_NAME)"),
    window_minutes: float = Query(5.0, ge=1.0, le=60.0, description="Telemetry window in minutes"),
) -> TelemetryOverviewResponse:
    """Return live telemetry snapshot: service health, error rates, latencies, and active incidents."""
    svc = service or config.SERVICE_NAME
    if svc == config.SERVICE_NAME:
        health = tools.get_service_health()
        error_rate = tools.get_error_rate(window_minutes)
        latency = tools.get_latency(window_minutes)
    else:   # the agent tools are bound to the monitored service; any other service is read from the event store directly
        health = store.health(svc)
        error_rate = store.error_rate(svc, window_minutes)
        latency = store.latency(svc, window_minutes)
    active = incidents.list_active(svc)
    all_incidents = incidents.list_all(service=svc, limit=500)

    return TelemetryOverviewResponse(
        service=svc,
        health=health,
        error_rate=error_rate,
        latency=latency,
        active_incidents=len(active),
        total_incidents=len(all_incidents),
    )


@router.post("/incidents/{incident_id}/feedback", response_model=IncidentFeedbackResponse)
def submit_incident_feedback(incident_id: str, request: IncidentFeedbackRequest) -> IncidentFeedbackResponse:
    """Submit operator verification feedback for an attempted remediation action on an incident."""
    inc = incidents.get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    if inc["status"] == "RESOLVED" and not request.worked:
        raise HTTPException(status_code=409, detail=f"Incident {incident_id} is already resolved; a failed-fix report "
                                                    "would overwrite its verified memory.")

    action_name = (request.action or "").strip()
    # Normalize common operator action aliases
    action_aliases = {
        "restart_pod": "restart_worker",
        "restart_service": "restart_worker",
        "reboot_worker": "restart_worker",
    }
    action_name = action_aliases.get(action_name, action_name)

    if not action_name or not re.match(r"^[a-zA-Z0-9_\-\.]+$", action_name):
        raise HTTPException(status_code=400, detail=f"Invalid action '{action_name}'. Must be a valid action identifier.")

    proposed_action = (inc.get("proposed_action") or "").strip().lower()
    system_extended_actions = {
        "create_missing_table", "run_db_migration", "apply_schema_migration",
        "disable_fault", "hotfix_query", "patch_code", "flush_pool",
        "restart_worker", "rollback_config", "rollback_release", "enable_fallback",
    }
    all_allowed = set(config.ALLOWED_ACTIONS) | system_extended_actions
    if proposed_action:
        all_allowed.add(proposed_action)

    if action_name not in all_allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action '{action_name}'. Must be one of: {sorted(list(all_allowed))}"
        )

    now_ms = int(time.time() * 1000)
    current_actions = inc.get("actions") or []
    action_record = {
        "action": action_name,
        "worked": request.worked,
        "notes": (request.notes or "").strip(),
        "ts": now_ms,
        "actor": "operator",
    }
    updated_actions = current_actions + [action_record]
    incidents.set_actions(incident_id, updated_actions)

    mem = cycle.get_memory()
    fix_action = action_name if request.worked else None
    failed_actions = list(dict.fromkeys(
        a["action"] for a in updated_actions if a.get("worked") is False and a.get("action") != fix_action))
    context_str = inc.get("context_sample") or ""

    if request.worked:
        # Success path: resolve incident and retain positive procedural fix memory in Hindsight
        updated_status = "RESOLVED"
        if inc["status"] != "RESOLVED":
            try:
                incidents.transition(
                    incident_id=incident_id,
                    new_status="RESOLVED",
                    expected_version=None,
                    actor="operator",
                    now=now_ms,
                    detail=f"Verified fix: {action_name}. Notes: {request.notes or 'None'}",
                )
            except Exception as exc:
                log.warning("Transition to RESOLVED failed (%s)", exc)

        min_to_resolve = round((now_ms - inc["opened_at"]) / 60000.0, 1) if inc.get("opened_at") else None
        narrative = build_narrative(
            incident=inc,
            actions=updated_actions,
            fix=fix_action,
            failed=failed_actions,
            context=context_str,
            minutes_to_resolve=min_to_resolve,
        )
        retain_outcome = mem.retain(
            incident={**inc, "status": "RESOLVED"},
            narrative=narrative,
            actions=updated_actions,
            fix=fix_action,
            failed=failed_actions,
        )
        incidents.update_fields(
            incident_id,
            proposed_action=action_name,
            memory_status=retain_outcome.status,
            memory_op=retain_outcome.operation_id,
            memory_detail=retain_outcome.detail or "Verified by operator",
        )

        return IncidentFeedbackResponse(
            incident_id=incident_id,
            status=updated_status,
            action=action_name,
            worked=True,
            memory_retained=(retain_outcome.status == "retained"),
            new_recommendation=None,
            message=_retain_message(retain_outcome.status, retain_outcome.detail),
        )
    else:
        # Failure path: keep incident active, retain failed action tag, and suggest alternative fix
        if inc["status"] in ("OPEN", "ACKED"):
            try:
                incidents.transition(
                    incident_id=incident_id,
                    new_status="REMEDIATING",
                    expected_version=None,
                    actor="operator",
                    now=now_ms,
                    detail=f"Action failed: {action_name}. Notes: {request.notes or 'None'}",
                )
                updated_status = "REMEDIATING"
            except Exception:
                updated_status = inc["status"]
        else:
            updated_status = inc["status"]
            incidents.record_event(incident_id, "action:failed", "operator",
                                   f"Action failed: {action_name}. Notes: {request.notes or 'None'}", now_ms)

        narrative = f"Attempted action '{action_name}' on {inc['service']} failed to resolve incident {incident_id}. Notes: {request.notes or 'None'}"
        if config.MEMORY_RETAIN_FAILED_ATTEMPTS:
            retain_outcome = mem.retain(
                incident=inc,
                narrative=narrative,
                actions=updated_actions,
                fix=None,
                failed=failed_actions,
            )
        else:   # recorded on the incident now, stored in memory with the fix once the incident resolves
            retain_outcome = RetainOutcome("skipped", detail="failed attempts are stored when the incident resolves")

        # Ask the model to suggest the next action based on the original diagnosis and what has failed
        next_rec = _llm_suggest_next_action(
            rca_text=inc.get("rca_text") or "",
            failed_actions=failed_actions,
            service=inc.get("service", "unknown"),
        )
        failed_set = set(failed_actions)
        if next_rec and (next_rec in failed_set or next_rec not in all_allowed):
            log.info("Ignoring model suggestion '%s' (already failed or not an allowed action)", next_rec)
            next_rec = None
        if not next_rec:
            # Fallback if the model is unavailable, repeats a failed action, or proposes one outside the allow-list
            alternatives = [a for a in config.ALLOWED_ACTIONS if a not in failed_set and a != "noop"]
            next_rec = alternatives[0] if alternatives else "escalate_to_human_sre"

        incidents.update_fields(
            incident_id,
            proposed_action=next_rec,
            memory_status=retain_outcome.status,
            memory_detail=f"Action {action_name} failed. Model-suggested next action: {next_rec or 'none'}",
        )

        rec_msg = f"Next proposed recommendation: '{next_rec}'." if next_rec else "No further recommendation available."
        return IncidentFeedbackResponse(
            incident_id=incident_id,
            status=updated_status,
            action=action_name,
            worked=False,
            memory_retained=(retain_outcome.status == "retained"),
            new_recommendation=next_rec,
            message=f"Action '{action_name}' recorded as failed. {rec_msg}",
        )
