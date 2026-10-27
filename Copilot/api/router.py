"""FastAPI router: Google ADK chat sessions with SQLite persistence."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
try:
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
except ImportError:
    class InMemorySessionService:  # type: ignore
        pass

    class Runner:  # type: ignore
        def __init__(self, *args, **kwargs):
            self.agent = kwargs.get("agent")

        async def run_async(self, *args, **kwargs):
            if False:
                yield None

    class _Types:
        class Content:
            def __init__(self, *args, **kwargs):
                pass
        class Part:
            def __init__(self, *args, **kwargs):
                pass

    types = _Types()  # type: ignore

from agent import config
from agent.agent import root_agent
from agent.laya_classifier import get_laya_classifier
from agent.llm import clean_agent_output, get_nemotron_model_name, scrub_secrets
from agent.laya_classifier import _context_facts
from agent.memory import parse_proposed_action, context_of
from agent.query_agent import query_agent
from agent.rca_agent import rca_agent_service, select_model_tier
from db import events as store
from db import incidents
from db.database import get_db_connection
from api.schema import (
    AskChatRequest,
    AskChatResponse,
    ChatInfoResponse,
    ChatResponse,
    NewChatResponse,
    RenameChatRequest,
    SuccessResponse,
)
import asyncio
import hashlib
import json
import os
import re
import time
import uuid

log = logging.getLogger("copilot.router")
router = APIRouter()

APP_NAME = "incident_copilot"
USER_ID = "user"

session_service = InMemorySessionService()
runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
    auto_create_session=True,
)
query_runner = Runner(
    agent=query_agent,
    app_name="incident_copilot_query",
    session_service=session_service,
    auto_create_session=True,
)


# One RCA runner per Nemotron tier, created on first use. `runner` (root_agent, built from AGENT_MODEL) serves the tier
# AGENT_MODEL names, or every tier when AGENT_MODEL is an explicit provider/model override.
_tier_runners: Dict[str, Any] = {}
# A chat keeps the tier chosen on its first RCA turn so the ADK session history stays with one agent.
_chat_tiers: Dict[int, str] = {}
_TIERS = ("nano", "super", "ultra")


def _forced_model() -> str:
    return (os.getenv("AGENT_MODEL") or "super").strip().lower()


def _rca_runner_for(tier: str) -> Any:
    forced = _forced_model()
    if forced not in _TIERS or tier == forced:
        return runner
    if tier not in _tier_runners:
        _tier_runners[tier] = Runner(
            agent=rca_agent_service.get_agent_for_tier(tier),
            app_name=APP_NAME,
            session_service=session_service,
            auto_create_session=True,
        )
    return _tier_runners[tier]


def _model_for_tier(tier: str) -> str:
    forced = _forced_model()
    return get_nemotron_model_name(tier) if forced in _TIERS else forced


def _live_error_context(service: str) -> Tuple[str, List[str]]:
    """Runtime context and evidence line of the newest 5xx event of the service (empty when there is none)."""
    try:
        msg = store.sample_error_message(service, None, int(time.time() * 1000) - 15 * 60_000)
    except Exception as exc:  # noqa: BLE001 - tier selection must never break a chat
        log.warning("live error context unavailable: %s", exc)
        return "", []
    if not msg:
        return "", []
    body = msg.rsplit(" | ", 1)[0]
    try:                                  # Loki lines arrive as JSON; the evidence is the log message, not its envelope
        payload = json.loads(body)
        body = " ".join(str(payload[k]) for k in ("message", "exception") if isinstance(payload, dict) and payload.get(k))
    except (TypeError, ValueError):
        pass
    return context_of(msg), [body]


def runtime_context_text(service: str) -> str:
    """The runtime facts (release, config_rev, pool, ...) of the service's newest 5xx event as 'key=value ...'."""
    facts, _ = _context_facts(_live_error_context(service)[0])
    return " ".join(f"{k}={v}" for k, v in sorted(facts.items()))


def select_rca_tier(user_message: str, past_items: List[dict], service: str) -> str:
    """Nemotron tier for a chat RCA: no precedent -> ultra, same context as the top precedent -> nano, else super."""
    if not past_items:
        return select_model_tier({}, [])
    top = past_items[0]
    try:
        local = incidents.get(top.get("incident_id") or "") or {}
        cur_ctx, cur_ev = _live_error_context(service)
        incident = {"context_sample": cur_ctx, "evidence": cur_ev + [user_message]}
        match = {
            "incident_id": top.get("incident_id"),
            "context": local.get("context_sample") or "",
            # `summary` stays empty: select_model_tier would read it as the precedent's runtime context.
            "summary": "",
            "evidence": list(local.get("evidence") or [])
            + [top.get("summary") or "", local.get("rca_text") or top.get("root_cause_summary") or ""],
        }
        return select_model_tier(incident, [match])
    except Exception as exc:  # noqa: BLE001
        log.warning("tier selection failed (%s); using super", exc)
        return "super"


def user_safe_error(exc: BaseException) -> str:
    """What the user sees when the agent fails: a reference id only. The details (scrubbed) go to the server log."""
    ref = "ERR-" + uuid.uuid4().hex[:8]
    log.error("agent failure %s: %s: %s", ref, type(exc).__name__, scrub_secrets(str(exc)))
    return f"Agent error: the assistant could not complete this request (reference {ref}). Please try again."


# ─── DB helpers ─────────────────────────────────────────────────────────────────

def get_chat_from_db(chat_id: int):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, created_at FROM chats WHERE id = ?", (chat_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            "SELECT role, message, llm_calls FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        )
        msgs = cursor.fetchall()
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "messages": [{"role": r["role"], "message": r["message"], "llm_calls": r["llm_calls"]} for r in msgs],
        }
    finally:
        conn.close()


def _insert_message(chat_id: int, role: str, message: str, llm_calls: int = 0) -> None:
    import sqlite3
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (chat_id, role, message, llm_calls) VALUES (?, ?, ?, ?)",
            (chat_id, role, message, llm_calls),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()


_KNOWN_SERVICES = ("checkout", "catalog", "frontend")


def _first_service_in(text: str) -> Optional[str]:
    low = (text or "").lower()
    hits = [(low.find(name), name) for name in dict.fromkeys(_KNOWN_SERVICES + (config.SERVICE_NAME,))
            if name and low.find(name) >= 0]
    return min(hits)[1] if hits else None


def _detect_service(user_prompt: str, reply: str) -> str:
    """The service the conversation is about: the one the operator names first, else the one the answer names, else
    the monitored service (config.SERVICE_NAME)."""
    return _first_service_in(user_prompt) or _first_service_in(reply) or config.SERVICE_NAME


def _is_rca_result(intent: str, reply: str) -> bool:
    """Only a genuine RCA answer to an RCA request becomes an incident; the read-only Query Agent never does."""
    if not reply or reply.startswith("Agent error:") or reply == "Agent produced no output.":
        return False
    return intent == "rca"


def _clean_evidence_item(text: str) -> Optional[str]:
    raw = re.sub(r"^\s*(?:[-*\u2022]+|\d+[.)])\s*", "", text).strip()
    if not raw:
        return None
    low = raw.lower().rstrip(":")
    if low in ("**logs**", "logs", "**evidence**", "evidence", "**symptoms**", "symptoms"):
        return None
    if "{" in raw and "}" in raw:
        try:
            start_idx = raw.index("{")
            end_idx = raw.rindex("}") + 1
            pj = json.loads(raw[start_idx:end_idx])
            if isinstance(pj, dict):
                msg = pj.get("message") or pj.get("msg")
                exc = pj.get("exception")
                lvl = pj.get("level")
                prefix = raw[:start_idx].strip().rstrip("|").strip()
                replacement = f"{msg}" + (f" ({exc})" if exc else "")
                if prefix:
                    return f"{prefix} | {replacement}"
                elif lvl:
                    return f"[{lvl}] {replacement}"
                return replacement
        except Exception:
            pass
    return raw


# A 5xx status code, not a number that merely starts with 5 (1500 ms, 5.5 s, 512 MB, 50%).
_STATUS_5XX = re.compile(r"(?<![\d.,])5\d\d(?![\d.,]*\d)(?!\s*(?:ms|s\b|mb|gb|kb|%))")


def _extract_evidence_and_severity(user_prompt: str, reply: str) -> tuple[list[str], str]:
    evidence: list[str] = []
    in_evidence = False
    for line in reply.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        line_lower = line_clean.lower()
        if "evidence" in line_lower:
            in_evidence = True
            continue
        if in_evidence:
            if any(line_lower.startswith(k) for k in ("root cause", "**root cause", "recommendation", "**recommendation", "proposed action", "### 2", "### 3")):
                in_evidence = False
                break
            if line_clean.startswith(("-", "*", "\u2022", "1.", "2.", "3.")):
                cleaned = _clean_evidence_item(line_clean)
                if cleaned:
                    evidence.append(cleaned)

    if not evidence:
        evidence = [f"Reported in chat: {user_prompt[:120]}"]

    reply_lower = reply.lower()
    is_critical = bool(_STATUS_5XX.search(reply_lower)) or any(
        kw in reply_lower for kw in ("5xx", "critical", "crash", "outage", "error rate: 100", "exception"))
    severity = "critical" if is_critical else "warning"
    return evidence[:5], severity


def _recalled_matches(items: List[dict]) -> List[dict]:
    """Past incidents the agent was shown, in the shape stored on an incident's `recalled` list."""
    return [{"incident_id": p.get("incident_id"), "text": (p.get("summary") or "")[:400], "exact": False,
             "score": p.get("confidence"), "fix": p.get("working_fix"), "failed": p.get("failed_actions") or [],
             "source": p.get("source") or "memory"} for p in items if p.get("incident_id")]


def _record_tier(incident_id: str, tier: Optional[str], now_ms: int) -> None:
    if tier:
        incidents.record_event(incident_id, "diagnosis:tier", "chat_operator",
                               f"Nemotron {tier} tier ({_model_for_tier(tier)})", now_ms)


def register_or_update_chat_incident(chat_id: int, user_prompt: str, agent_reply: str, llm_calls: int,
                                     tier: Optional[str] = None, recalled: Optional[List[dict]] = None) -> Optional[str]:
    """File a chat diagnosis. While the service already has an active incident, the diagnosis belongs to that incident
    (one fault = one incident); a new dossier is only created when nothing is active."""
    service = _detect_service(user_prompt, agent_reply)
    proposed_action = parse_proposed_action(agent_reply, set(config.ALLOWED_ACTIONS))
    # The incident's runtime context is what the service logged when it failed, not text cut out of the markdown reply.
    context_sample = runtime_context_text(service) or f"chat_session={chat_id}"
    evidence, severity = _extract_evidence_and_severity(user_prompt, agent_reply)
    now_ms = int(time.time() * 1000)

    active_incidents = incidents.list_active(service)
    if active_incidents:
        pending = [inc for inc in active_incidents if inc.get("rca_status") != "ok"]
        target = pending[0] if pending else max(active_incidents, key=lambda inc: inc.get("opened_at") or 0)
        target_id = target["id"]
        if pending:
            incidents.set_rca(target_id, agent_reply, "ok", llm_calls=llm_calls)
            incidents.update_fields(target_id, proposed_action=proposed_action, context_sample=context_sample)
            incidents.record_event(target_id, "diagnosis:ok", "chat_operator", f"Diagnosed via Chat #{chat_id}", now_ms)
        else:
            # Already diagnosed: keep that diagnosis, log that the operator asked again, adopt a proposal if it had none.
            first_line = next((ln.strip() for ln in agent_reply.splitlines() if ln.strip()), "")
            incidents.record_event(target_id, "diagnosis:chat", "chat_operator",
                                   f"Chat #{chat_id} asked again: {first_line[:160]}", now_ms)
            if proposed_action and not target.get("proposed_action"):
                incidents.update_fields(target_id, proposed_action=proposed_action)
        if recalled:
            incidents.set_recalled(target_id, recalled, "")
        _record_tier(target_id, tier, now_ms)
        return target_id

    fp_hash = hashlib.sha1(f"{service}|chat_rca|{chat_id}|{now_ms}".encode()).hexdigest()[:10]
    fingerprints = [{"fp": f"fp-{fp_hash}", "kind": "chat_rca", "route": "chat", "sig": "chat_investigation"}]
    created = incidents.create(service=service, fingerprints=fingerprints, severity=severity, evidence=evidence, now=now_ms)
    inc_id = created["id"]
    incidents.set_rca(inc_id, agent_reply, "ok", llm_calls=llm_calls)
    incidents.update_fields(inc_id, proposed_action=proposed_action, context_sample=context_sample)
    incidents.record_event(inc_id, "diagnosis:ok", "chat_operator", f"Diagnosed via Chat #{chat_id}", now_ms)
    if recalled:
        incidents.set_recalled(inc_id, recalled, "")
    _record_tier(inc_id, tier, now_ms)
    return inc_id


# ─── Chat endpoints ──────────────────────────────────────────────────────────────

@router.get("/chats", response_model=List[ChatInfoResponse])
def get_all_chats():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, created_at FROM chats ORDER BY id DESC")
        rows = cursor.fetchall()
        return [
            {"id": r["id"], "title": r["title"], "created_at": str(r["created_at"])}
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/chat/{id}", response_model=ChatResponse)
def get_chat(id: int):
    data = get_chat_from_db(id)
    if not data:
        raise HTTPException(status_code=404, detail="Chat not found")
    return data


@router.post("/chat/new", response_model=NewChatResponse)
def new_chat():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chats (title) VALUES (?)", ("New Chat",))
        conn.commit()
        new_id = cursor.lastrowid
        return {"id": new_id}
    finally:
        conn.close()


@router.post("/ask/{id}", response_model=AskChatResponse)
async def ask_chat(id: int, request: AskChatRequest):
    chat = get_chat_from_db(id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    _insert_message(id, "user", request.message)

    if chat["title"] == "New Chat" and len(chat.get("messages", [])) == 0:
        new_title = request.message[:30]
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE chats SET title = ? WHERE id = ?", (new_title, id))
            conn.commit()
        except Exception as e:
            log.warning("Failed to auto-rename chat: %s", e)
        finally:
            conn.close()

    intent = get_laya_classifier().classify_chat_intent(request.message)
    agent_name = "query_agent" if intent == "query" else "rca_agent"
    tier: Optional[str] = None

    message_text = request.message
    past_items: List[dict] = []
    if intent != "query":
        # Pre-recall Hindsight memories for relevant past incidents
        try:
            from agent.tools import recall_past_incidents
            # Off the event loop and bounded: a slow or starting Hindsight must not stall every request.
            recall_job = asyncio.get_running_loop().run_in_executor(
                None, lambda: recall_past_incidents(query=request.message, limit=2))
            past_res = await asyncio.wait_for(asyncio.shield(recall_job), timeout=config.CHAT_RECALL_TIMEOUT_S)
            past_items = past_res.get("past_incidents") or []
            if past_items:
                lines = []
                for p in past_items:
                    lines.append(
                        f"- Past Incident {p['incident_id']} ({p.get('service')}): {p.get('summary')}. "
                        f"Root Cause: {p.get('root_cause_summary') or 'N/A'}. "
                        f"Verified fix that worked: {p.get('working_fix') or 'none'}. "
                        f"Actions that failed: {', '.join(p.get('failed_actions') or []) or 'none'}."
                    )
                memory_preamble = (
                    "[HINDSIGHT HISTORICAL INCIDENT MEMORIES]:\n"
                    + "\n".join(lines)
                    + "\n(Use these past incidents as verified historical precedent. If current symptoms match, cite the incident ID and recommend the verified working fix.)\n\n"
                )
                message_text = f"{memory_preamble}Current user observation / inquiry: {request.message}"
        except Exception as e:
            log.warning("Pre-recall past incidents for chat failed: %s", e)

        tier = _chat_tiers.get(id)
        if tier is None:
            tier = select_rca_tier(request.message, past_items, _detect_service(request.message, ""))
            _chat_tiers[id] = tier
        log.info("Chat %s RCA tier=%s model=%s", id, tier, _model_for_tier(tier))
    active_runner = query_runner if intent == "query" else _rca_runner_for(tier)

    content = types.Content(
        role="user",
        parts=[types.Part(text=message_text)],
    )

    session_id = f"chat-{id}"
    agent_reply = ""
    llm_calls = 0
    prompt_tokens = completion_tokens = 0

    async def _drive() -> None:
        nonlocal agent_reply, llm_calls, prompt_tokens, completion_tokens
        async for event in active_runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=content,
        ):
            if getattr(event, "content", None) and getattr(event.content, "role", None) == "model" and not getattr(event, "partial", False):
                llm_calls += 1
            usage = getattr(event, "usage_metadata", None)
            if usage and not getattr(event, "partial", False):
                prompt_tokens += getattr(usage, "prompt_token_count", None) or 0
                completion_tokens += getattr(usage, "candidates_token_count", None) or 0

            if (
                event.is_final_response()
                and event.content
                and event.content.parts
            ):
                final_parts = [
                    str(part.text)
                    for part in event.content.parts
                    if getattr(part, "text", None) and getattr(part, "thought", None) is not True
                ]
                if final_parts:
                    agent_reply = "".join(final_parts)

    try:
        await asyncio.wait_for(_drive(), timeout=config.AGENT_TIMEOUT_S)
    except asyncio.TimeoutError:
        agent_reply = user_safe_error(TimeoutError(f"no answer within {config.AGENT_TIMEOUT_S:.0f}s"))
    except Exception as exc:
        agent_reply = user_safe_error(exc)

    log.info("Chat %s done: agent=%s tier=%s llm_calls=%d prompt_tokens=%d completion_tokens=%d",
             id, agent_name, tier, llm_calls, prompt_tokens, completion_tokens)
    agent_reply = clean_agent_output(agent_reply)
    if not agent_reply.strip():
        agent_reply = "Agent produced no output."

    registered_inc_id = None
    if _is_rca_result(intent, agent_reply):
        try:
            registered_inc_id = register_or_update_chat_incident(
                id, request.message, agent_reply, llm_calls, tier, _recalled_matches(past_items))
            if registered_inc_id and f"**{registered_inc_id}**" not in agent_reply:
                agent_reply += f"\n\n🛡️ **Incident Registered:** [`{registered_inc_id}`](/dashboard) — *Track dossier and verify fix on the [Incident Dashboard](/dashboard).*"
        except Exception as e:
            log.warning("Failed to auto-register chat incident: %s", e)

    _insert_message(id, "agent", agent_reply, llm_calls)

    return {
        "response": agent_reply,
        "llm_calls": llm_calls,
        "intent": intent,
        "agent": agent_name,
        "incident_id": registered_inc_id,
        "tier": tier,
        "model": _model_for_tier(tier) if tier else None,
        "prompt_tokens": prompt_tokens or None,
        "completion_tokens": completion_tokens or None,
    }


@router.put("/chat/{id}/rename", response_model=SuccessResponse)
def rename_chat(id: int, request: RenameChatRequest):
    title = (request.title or "").strip() or "New Chat"
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE chats SET title = ? WHERE id = ?", (title, id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Chat not found")
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


@router.delete("/chat/{id}", response_model=SuccessResponse)
def delete_chat(id: int):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE chat_id = ?", (id,))
        cursor.execute("DELETE FROM chats WHERE id = ?", (id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Chat not found")
        conn.commit()
    finally:
        conn.close()
    return {"success": True}
