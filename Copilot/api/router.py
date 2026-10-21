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
from agent.llm import clean_agent_output
from agent.memory import parse_proposed_action, context_of
from agent.query_agent import query_agent
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
import hashlib
import json
import time

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


def _detect_service(user_prompt: str, reply: str) -> str:
    combined = f"{user_prompt} {reply}".lower()
    for svc in ("checkout", "catalog", "frontend"):
        if svc in combined:
            return svc
    return getattr(config, "APP_SERVICE_NAME", "checkout") or "checkout"


def _is_rca_result(intent: str, reply: str) -> bool:
    if not reply or reply.startswith("Agent error:") or reply == "Agent produced no output.":
        return False
    if intent == "rca":
        return True
    reply_lower = reply.lower()
    if "proposed action:" in reply_lower:
        return True
    if "root cause:" in reply_lower or "**root cause**" in reply_lower:
        return True
    return False


def _clean_evidence_item(text: str) -> Optional[str]:
    raw = text.lstrip("-*• 123456789. ").strip()
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
            if line_clean.startswith(("-", "*", "•", "1.", "2.", "3.")):
                cleaned = _clean_evidence_item(line_clean)
                if cleaned:
                    evidence.append(cleaned)

    if not evidence:
        evidence = [f"Reported in chat: {user_prompt[:120]}"]

    reply_lower = reply.lower()
    is_critical = any(kw in reply_lower for kw in ("5xx", "critical", "crash", "outage", "error rate: 100", "500", "exception"))
    severity = "critical" if is_critical else "warning"
    return evidence[:5], severity


def register_or_update_chat_incident(chat_id: int, user_prompt: str, agent_reply: str, llm_calls: int) -> Optional[str]:
    service = _detect_service(user_prompt, agent_reply)
    proposed_action = parse_proposed_action(agent_reply, set(config.ALLOWED_ACTIONS))
    context_sample = context_of(agent_reply) or f"chat_session={chat_id}"
    evidence, severity = _extract_evidence_and_severity(user_prompt, agent_reply)
    now_ms = int(time.time() * 1000)

    # 1. Correlate with active incident if one exists that needs diagnosis
    active_incidents = incidents.list_active(service)
    pending_inc = next((inc for inc in active_incidents if inc.get("rca_status") != "ok"), None)

    if pending_inc:
        target_id = pending_inc["id"]
        incidents.set_rca(target_id, agent_reply, "ok", llm_calls=llm_calls)
        incidents.update_fields(
            target_id,
            proposed_action=proposed_action,
            context_sample=context_sample,
        )
        incidents.record_event(
            target_id,
            "diagnosis:ok",
            "chat_operator",
            f"Diagnosed via Chat #{chat_id}",
            now_ms,
        )
        return target_id

    # 2. Otherwise create a new incident dossier
    fp_hash = hashlib.sha1(f"{service}|chat_rca|{chat_id}|{now_ms}".encode()).hexdigest()[:10]
    fingerprints = [{"fp": f"fp-{fp_hash}", "kind": "chat_rca", "route": "chat", "sig": "chat_investigation"}]

    created = incidents.create(
        service=service,
        fingerprints=fingerprints,
        severity=severity,
        evidence=evidence,
        now=now_ms,
    )
    inc_id = created["id"]
    incidents.set_rca(inc_id, agent_reply, "ok", llm_calls=llm_calls)
    incidents.update_fields(
        inc_id,
        proposed_action=proposed_action,
        context_sample=context_sample,
    )
    incidents.record_event(
        inc_id,
        "diagnosis:ok",
        "chat_operator",
        f"Diagnosed via Chat #{chat_id}",
        now_ms,
    )
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
    active_runner = query_runner if intent == "query" else runner
    agent_name = "query_agent" if intent == "query" else "rca_agent"

    message_text = request.message
    if intent != "query":
        # Pre-recall Hindsight memories for relevant past incidents
        try:
            from agent.tools import recall_past_incidents
            past_res = recall_past_incidents(query=request.message, limit=2)
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

    content = types.Content(
        role="user",
        parts=[types.Part(text=message_text)],
    )

    session_id = f"chat-{id}"
    agent_reply = ""
    llm_calls = 0
    try:
        async for event in active_runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=content,
        ):
            if getattr(event, "content", None) and getattr(event.content, "role", None) == "model" and not getattr(event, "partial", False):
                llm_calls += 1

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
    except Exception as exc:
        log.exception("ADK runner failed for chat_id=%s (agent=%s)", id, agent_name)
        agent_reply = f"Agent error: {exc}"

    agent_reply = clean_agent_output(agent_reply)
    if not agent_reply.strip():
        agent_reply = "Agent produced no output."

    registered_inc_id = None
    if _is_rca_result(intent, agent_reply):
        try:
            registered_inc_id = register_or_update_chat_incident(id, request.message, agent_reply, llm_calls)
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
