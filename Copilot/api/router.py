"""FastAPI router — Google ADK chat sessions with SQLite persistence and Redis cache."""

import json
import logging
from typing import List

from fastapi import APIRouter, HTTPException
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent.agent import root_agent
from db.database import get_db_connection
from db.redis_client import redis_client
from api.schema import (
    AskChatRequest,
    AskChatResponse,
    ChatInfoResponse,
    ChatResponse,
    NewChatResponse,
    RenameChatRequest,
    SuccessResponse,
)

log = logging.getLogger("copilot.router")
router = APIRouter()

APP_NAME = "sre_copilot"
USER_ID = "user"

session_service = InMemorySessionService()
runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
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


def _update_cache(chat_id: int, data: dict):
    try:
        redis_client.set(f"chat:{chat_id}", json.dumps(data), ex=3600)
    except Exception:
        pass


def _delete_cache(chat_id: int):
    try:
        redis_client.delete(f"chat:{chat_id}")
    except Exception:
        pass


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
    cached = redis_client.get(f"chat:{id}")
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass
    data = get_chat_from_db(id)
    if not data:
        raise HTTPException(status_code=404, detail="Chat not found")
    _update_cache(id, data)
    return data


@router.post("/chat/new", response_model=NewChatResponse)
def new_chat():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chats (title) VALUES (?)", ("New Chat",))
        conn.commit()
        new_id = cursor.lastrowid
        _update_cache(new_id, {"id": new_id, "title": "New Chat", "messages": []})
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
        _delete_cache(id)

    content = types.Content(
        role="user",
        parts=[types.Part(text=request.message)],
    )

    session_id = f"chat-{id}"
    agent_reply = ""
    llm_calls = 0
    try:
        async for event in runner.run_async(
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
                for part in event.content.parts:
                    if part.text:
                        agent_reply += part.text
    except Exception as exc:
        log.exception("ADK runner failed for chat_id=%s", id)
        agent_reply = f"Agent error: {exc}"

    if not agent_reply.strip():
        agent_reply = "Agent produced no output."

    _insert_message(id, "agent", agent_reply, llm_calls)

    updated = get_chat_from_db(id)
    if updated:
        _update_cache(id, updated)

    return {"response": agent_reply, "llm_calls": llm_calls}


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
    _delete_cache(id)
    data = get_chat_from_db(id)
    if data:
        _update_cache(id, data)
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
    _delete_cache(id)
    return {"success": True}
