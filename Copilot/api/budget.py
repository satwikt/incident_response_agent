"""Demo cost guard for the chat: a durable call budget, and a labelled replay when it is used up.

The public demo has to stay free and reachable for weeks, so model spend is capped. The budget is counted from the stored chat
messages (``llm_calls`` of every agent reply), which survive restarts, so a crash loop cannot reset it. Both limits default to 0
(= unlimited); the hosted deployment sets them.

When a limit is reached the chat makes no model call. It says so, and, if a recorded run exists (``scripts/benchmark_tiers.py``
writes ``data/replay.json`` from a real run), shows that run clearly labelled as a replay. Nothing here invents an answer.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from agent import config
from db.database import get_db_connection

log = logging.getLogger("copilot.budget")

_MISSING = object()
_replay_cache: dict = {"path": None, "data": _MISSING}


def chat_calls_since(minutes: int) -> int:
    """Model calls made by chat agents in the last ``minutes`` minutes."""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(llm_calls), 0) AS n FROM messages WHERE role = 'agent' AND created_at >= datetime('now', ?)",
            (f"-{int(minutes)} minutes",),
        ).fetchone()
        return int(row["n"] if row else 0)
    finally:
        conn.close()


def exhausted_reason() -> Optional[str]:
    """Why live chat is paused (a short phrase), or None while there is budget left."""
    hourly, daily = config.CHAT_MAX_LLM_CALLS_PER_HOUR, config.CHAT_MAX_LLM_CALLS_PER_DAY
    try:
        if hourly and chat_calls_since(60) >= hourly:
            return f"the hourly limit of {hourly} model calls is used up"
        if daily and chat_calls_since(24 * 60) >= daily:
            return f"the daily limit of {daily} model calls is used up"
    except Exception as exc:  # noqa: BLE001 - a counting failure must not take the chat down
        log.warning("chat budget check failed (%s); allowing the request", exc)
    return None


def load_replay() -> Optional[dict]:
    path = Path(config.REPLAY_PATH)
    if _replay_cache["path"] != str(path) or _replay_cache["data"] is _MISSING:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data.get("replies"), dict):
                raise ValueError("no 'replies' object")
        except (OSError, ValueError) as exc:
            if path.exists():
                log.warning("replay file %s unusable: %s", path, exc)
            data = None
        _replay_cache.update(path=str(path), data=data)
    return _replay_cache["data"]


def pick_scenario(message: str) -> str:
    text = (message or "").lower()
    if re.search(r"\b503\b|pool|connection|timeout", text):
        return "context_shift"
    if re.search(r"\b(again|repeat|recurr\w*|back)\b", text):
        return "repeat_after_fix"
    return "new_incident"


def budget_reply(message: str, reason: str) -> str:
    """The chat text when live calls are paused: the notice, plus a labelled replay when one was recorded."""
    notice = (f"**Demo budget reached.** Live model calls are paused ({reason}) so this public demo stays free for every "
              "reviewer. Nothing was run for your question.")
    replay = load_replay()
    if not replay:
        return notice + " Try again later; the dashboard, incidents and memory remain fully usable."
    rec = (replay.get("replies") or {}).get(pick_scenario(message)) or next(iter(replay["replies"].values()), None)
    if not rec or not rec.get("response"):
        return notice
    body = re.sub(r"\n+🛡️ \*\*Incident Registered:.*$", "", rec["response"], flags=re.DOTALL).strip()
    meta = replay.get("meta") or {}
    return (f"{notice}\n\n> **REPLAY, not a live answer.** Recorded {meta.get('recorded', 'earlier')} from a real run "
            f"(question: *{rec.get('question', 'n/a')}*; tier `{rec.get('tier', 'n/a')}`, model `{rec.get('model', 'n/a')}`).\n\n---\n\n{body}")
