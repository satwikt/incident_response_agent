"""Discord notifications, split into pure rendering and network delivery so the outbox can retry safely.

``render`` builds the embed from the incident as stored in the database at delivery time (so a retried alert always
shows current state). ``deliver`` reports what happened as a :class:`Delivery` for the outbox to act on; it never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from db import incidents

from . import config

log = logging.getLogger("copilot.notifier")

_RED, _ORANGE, _YELLOW, _GREEN = 0xE74C3C, 0xE67E22, 0xF1C40F, 0x2ECC71
_EMBED_BUDGET = 5800          # Discord's hard limit is 6000 characters of embed text in total
_CUT = " ... (truncated)"
_TITLES = {
    "opened": "🚨", "diagnosis": "🧠", "reminder": "⏰", "regressed": "🔁", "resolved": "✅",
}


@dataclass
class Delivery:
    status: str                    # ok | retry | skip | fail
    retry_after_s: float = 0.0
    detail: str = ""


def _memory_field(incident: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What memory knows about this incident, or that memory is unavailable."""
    detail = incident.get("memory_detail") or ""
    if detail.startswith("recall degraded"):
        return {"name": "🧠 Memory", "value": "Unavailable right now; continuing without it.", "inline": False}
    recalled = incident.get("recalled") or []
    if not recalled:
        return None
    lines = []
    for m in recalled[:3]:
        if m.get("exact"):
            line = f"**Known issue** {m['incident_id']} ({m.get('source', 'live')})"
            if m.get("fix"):
                same = sum(1 for x in recalled if x.get("exact") and x.get("fix") == m["fix"])
                line += f": fix that worked was **{m['fix']}** ({same} of {sum(1 for x in recalled if x.get('exact'))} similar incidents)"
            if m.get("failed"):
                line += "; did not work: " + ", ".join(m["failed"])
        else:
            line = f"Similar: {m['incident_id']} ({m.get('source', 'live')}, score {m.get('score', 0):.2f})"
        lines.append(line)
    return {"name": "🧠 Memory (a hypothesis, verified below)", "value": _truncate("\n".join(lines), 700), "inline": False}


def _truncate(text: str, limit: int = 1024) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _ago(ms: int) -> str:
    s = max(0, int(ms / 1000))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{round(s / 60)}m"
    return f"{s / 3600:.1f}h"


def render(incident: Dict[str, Any], event: str, now_ms: int, window_minutes: float) -> Dict[str, Any]:
    """Build the Discord webhook JSON for one incident event."""
    inc_id = incident["id"]
    title_icon = _TITLES.get(event, "🚨")
    colour = _GREEN if event == "resolved" else (_RED if incident["severity"] == "critical" else _ORANGE)
    lines: List[str] = [f"• {b}" for b in incident.get("evidence", [])] or ["N/A"]

    fields = [{"name": "⚠️ Evidence", "value": _truncate("\n".join(lines)), "inline": False}]
    if incident.get("recurrence_of"):
        fields.insert(0, {"name": "🔁 Recurrence", "value": f"Same fingerprint as **{incident['recurrence_of']}**, "
                                                           "which was resolved earlier.", "inline": False})
    mem_field = _memory_field(incident)
    if mem_field and event in ("opened", "diagnosis", "reminder", "regressed"):
        fields.append(mem_field)
    rca = (incident.get("rca_text") or "").strip()
    if event == "opened" and incident.get("rca_status", "pending") == "pending":
        fields.append({"name": "📋 Diagnosis", "value": "In progress. It will follow as a separate message.", "inline": False})
    if event in ("opened", "diagnosis", "reminder", "regressed") and rca:
        # Discord rejects an embed whose text totals more than 6000 characters (a 400, which is not retryable), so the
        # diagnosis gets whatever budget is left after the other fields, the title and the details line.
        used = sum(len(f["name"]) + len(f["value"]) for f in fields) + 700
        budget = max(0, _EMBED_BUDGET - used)
        if len(rca) > budget:
            rca = rca[: max(0, budget - len(_CUT))] + _CUT
        chunks = [rca[i:i + 1000] for i in range(0, len(rca), 1000)]
        for n, chunk in enumerate(chunks, 1):
            fields.append({"name": f"📋 Diagnosis ({n}/{len(chunks)})", "value": _truncate(chunk), "inline": False})

    if event == "resolved":
        took = _ago((incident.get("resolved_at") or now_ms) - incident["opened_at"])
        description = f"**{inc_id} resolved** after {took} ({incident.get('resolved_by') or 'auto'})."
    elif event == "reminder":
        description = f"**{inc_id} is still open** ({_ago(now_ms - incident['opened_at'])} so far, status {incident['status']})."
    elif event == "regressed":
        description = f"**{inc_id} is breaching again** while being monitored for recovery."
    elif event == "diagnosis":
        cost = ""
        if incident.get("llm_calls"):
            cost = f" · {incident['llm_calls']} model call(s)"
            if incident.get("llm_tokens"):
                cost += f", {incident['llm_tokens'] / 1000:.1f}k tokens"
        description = f"**Diagnosis for {inc_id}** ({incident.get('rca_status') or 'pending'}){cost}."
    else:
        description = f"**{inc_id}: {len(incident.get('evidence', []))} threshold(s) breached.**"

    if event == "diagnosis" and incident.get("proposed_action"):
        act = incident["proposed_action"]
        fields.append({"name": "🛠 Proposed action",
                       "value": f"**{act}** (from the allow-list). A person decides and runs it, for example: POST /ops/{act}",
                       "inline": False})
    if event == "resolved" and incident.get("memory_status") in ("pending", "retained", "indexed"):
        fields.append({"name": "🧠 Memory", "value": "This incident is being saved so a repeat is recognised.", "inline": False})

    details = f"Service: `{incident['service']}` | Status: {incident['status']} | Window: {window_minutes:g}m"
    if config.PUBLIC_BASE_URL:
        details += f" | {config.PUBLIC_BASE_URL}/incidents/{inc_id}"
    fields.append({"name": "🕒 Details", "value": _truncate(details), "inline": False})

    return {
        "username": "Incident Copilot",
        # The text below can contain LLM output and quoted log lines (attacker-influenced). Never let it ping anyone.
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": f"{title_icon} {inc_id} · {incident['service']}",
            "description": description,
            "color": colour,
            "fields": fields,
            "footer": {"text": "Incident Copilot"},
            "timestamp": datetime.fromtimestamp(now_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }],
    }


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(1.0, float(resp.json().get("retry_after", 0)))
    except Exception:  # noqa: BLE001
        pass
    try:
        return max(1.0, float(resp.headers.get("Retry-After", "5")))
    except ValueError:
        return 5.0


def deliver(item: Dict[str, Any], now_ms: int, window_minutes: float,
            webhook_url: Optional[str] = None, client: Optional[httpx.Client] = None) -> Delivery:
    """Deliver one outbox item to Discord. Never raises."""
    url = config.DISCORD_WEBHOOK_URL if webhook_url is None else webhook_url
    if not url:
        return Delivery("skip", detail="DISCORD_WEBHOOK_URL not configured")
    payload = item.get("payload") or {}
    incident = incidents.get(item.get("incident_id") or "")
    if incident is None:
        return Delivery("fail", detail="incident not found")
    body = render(incident, payload.get("event", "opened"), now_ms, window_minutes)
    try:
        if client is not None:
            resp = client.post(url, json=body)
        else:
            with httpx.Client(timeout=10) as c:
                resp = c.post(url, json=body)
    except Exception as exc:  # noqa: BLE001 - network problems are retryable
        return Delivery("retry", detail=type(exc).__name__)
    if resp.status_code < 300:
        return Delivery("ok")
    if resp.status_code == 429:
        return Delivery("retry", retry_after_s=_retry_after(resp), detail="rate limited")
    if resp.status_code >= 500:
        return Delivery("retry", detail=f"HTTP {resp.status_code}")
    return Delivery("fail", detail=f"HTTP {resp.status_code}")   # 4xx: retrying will not help
