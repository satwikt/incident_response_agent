"""Incident persistence and state machine.

States:  OPEN -> ACKED -> REMEDIATING -> MONITORING -> RESOLVED      (SUPPRESSED is a side state)

Every change bumps ``version``. Operator-facing transitions take ``expected_version`` (compare-and-swap), so two
people acting on the same incident cannot both win: the loser gets ``ConflictError`` (an HTTP 409 at the API layer).
RESOLVED is terminal; a recurrence is a NEW incident linked through ``recurrence_of`` so MTTR stays clean.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from db.database import get_db_connection

ACTIVE = ("OPEN", "ACKED", "REMEDIATING", "MONITORING", "SUPPRESSED")

# from-state -> allowed to-states
TRANSITIONS: Dict[str, tuple] = {
    "OPEN": ("ACKED", "REMEDIATING", "SUPPRESSED", "RESOLVED"),
    "ACKED": ("REMEDIATING", "SUPPRESSED", "RESOLVED", "OPEN"),
    "REMEDIATING": ("MONITORING", "OPEN", "RESOLVED"),
    "MONITORING": ("RESOLVED", "OPEN"),
    "SUPPRESSED": ("OPEN", "RESOLVED"),
    "RESOLVED": (),
}


class IncidentError(Exception):
    """Base class for incident store errors."""


class NotFound(IncidentError):
    pass


class ConflictError(IncidentError):
    """The incident changed since the caller read it (stale ``expected_version``)."""


class InvalidTransition(IncidentError):
    pass


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    d["fingerprints"] = json.loads(d.pop("fingerprints_json") or "[]")
    d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
    d["recalled"] = json.loads(d.pop("recalled_json", None) or "[]")
    d["actions"] = json.loads(d.pop("actions_json", None) or "[]")
    return d


def get(incident_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        r = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def list_active(service: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        marks = ",".join("?" * len(ACTIVE))
        rows = conn.execute(
            f"SELECT * FROM incidents WHERE service = ? AND status IN ({marks}) ORDER BY last_seen_at DESC, seq DESC",
            (service, *ACTIVE),
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def list_recent(service: str, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM incidents WHERE service = ? ORDER BY seq DESC LIMIT ?", (service, max(1, min(limit, 500)))
        ).fetchall()
        return [_row(r) for r in rows]
    finally:
        conn.close()


def find_recent_resolved(service: str, fingerprint_ids: set, since_ms: int) -> Optional[Dict[str, Any]]:
    """Most recently resolved incident sharing a fingerprint, resolved at or after ``since_ms``."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM incidents WHERE service = ? AND status = 'RESOLVED' AND resolved_at >= ? "
            "ORDER BY resolved_at DESC, seq DESC",
            (service, since_ms),
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        inc = _row(r)
        if fingerprint_ids & {f["fp"] for f in inc["fingerprints"]}:
            return inc
    return None


def create(service: str, fingerprints: List[dict], severity: str, evidence: List[str], now: int,
           recurrence_of: Optional[str] = None) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "INSERT INTO incidents (service, status, severity, fingerprint, fingerprints_json, evidence_json, "
            "opened_at, last_seen_at, recurrence_of) VALUES (?, 'OPEN', ?, ?, ?, ?, ?, ?, ?)",
            (service, severity, fingerprints[0]["fp"], json.dumps(fingerprints), json.dumps(evidence), now, now,
             recurrence_of),
        )
        seq = cur.lastrowid
        inc_id = f"INC-{seq:04d}"
        conn.execute("UPDATE incidents SET id = ? WHERE seq = ?", (inc_id, seq))
        conn.execute(
            "INSERT INTO incident_events (incident_id, ts, kind, actor, detail) VALUES (?, ?, 'opened', 'watcher', ?)",
            (inc_id, now, "; ".join(evidence)[:1000]),
        )
        conn.commit()
    finally:
        conn.close()
    return get(inc_id)  # type: ignore[return-value]


def record_event(incident_id: str, kind: str, actor: str, detail: str, now: int) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO incident_events (incident_id, ts, kind, actor, detail) VALUES (?, ?, ?, ?, ?)",
            (incident_id, now, kind, actor, (detail or "")[:1000]),
        )
        conn.commit()
    finally:
        conn.close()


def events(incident_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT ts, kind, actor, detail FROM incident_events WHERE incident_id = ? ORDER BY id", (incident_id,)
        ).fetchall()]
    finally:
        conn.close()


def touch_breach(incident_id: str, now: int, fingerprints: List[dict], evidence: List[str],
                 severity: str) -> Dict[str, Any]:
    """The incident was seen breaching again: merge fingerprints, refresh evidence, reset the healthy streak."""
    inc = get(incident_id)
    if inc is None:
        raise NotFound(incident_id)
    known = {f["fp"] for f in inc["fingerprints"]}
    merged = inc["fingerprints"] + [f for f in fingerprints if f["fp"] not in known]
    worse = "critical" if "critical" in (severity, inc["severity"]) else severity
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE incidents SET last_seen_at = ?, fingerprints_json = ?, evidence_json = ?, severity = ?, "
            "healthy_windows = 0, breach_windows = breach_windows + 1, version = version + 1 WHERE id = ?",
            (now, json.dumps(merged), json.dumps(evidence), worse, incident_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get(incident_id)  # type: ignore[return-value]


def set_healthy_windows(incident_id: str, count: int) -> None:
    conn = get_db_connection()
    try:
        conn.execute("UPDATE incidents SET healthy_windows = ?, version = version + 1 WHERE id = ?", (count, incident_id))
        conn.commit()
    finally:
        conn.close()


def set_rca(incident_id: str, text: str, status: str, llm_calls: Optional[int] = None,
            llm_tokens: Optional[int] = None) -> None:
    conn = get_db_connection()
    try:
        conn.execute("UPDATE incidents SET rca_text = ?, rca_status = ?, llm_calls = ?, llm_tokens = ?, "
                     "version = version + 1 WHERE id = ?", (text, status, llm_calls, llm_tokens, incident_id))
        conn.commit()
    finally:
        conn.close()


def tokens_used_since(since_ms: int) -> int:
    """Total model tokens recorded on incidents opened at or after since_ms (a daily-budget estimate)."""
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT COALESCE(SUM(llm_tokens), 0) AS n FROM incidents WHERE opened_at >= ?",
                           (since_ms,)).fetchone()
        return int(row["n"])
    finally:
        conn.close()


_SETTABLE = {"proposed_action", "diagnosis_mode", "memory_status", "memory_op", "memory_detail", "context_sample"}


def update_fields(incident_id: str, **fields: Any) -> None:
    """Set a few descriptive columns (whitelisted). Does not change the lifecycle state."""
    bad = set(fields) - _SETTABLE
    if bad:
        raise ValueError(f"not settable: {sorted(bad)}")
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = get_db_connection()
    try:
        conn.execute(f"UPDATE incidents SET {cols}, version = version + 1 WHERE id = ?", (*fields.values(), incident_id))
        conn.commit()
    finally:
        conn.close()


def set_recalled(incident_id: str, matches: List[dict], detail: str = "") -> None:
    conn = get_db_connection()
    try:
        conn.execute("UPDATE incidents SET recalled_json = ?, memory_detail = ?, version = version + 1 WHERE id = ?",
                     (json.dumps(matches), detail or None, incident_id))
        conn.commit()
    finally:
        conn.close()


def insert_resolved(service: str, fingerprints: List[dict], severity: str, evidence: List[str], opened_at: int,
                    resolved_at: int, rca_text: str, actions: List[dict], context: str, source: str = "seed") -> Dict[str, Any]:
    """A finished incident inserted directly (seed history). Marked with its source so it is never mistaken for live data."""
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "INSERT INTO incidents (service, status, severity, fingerprint, fingerprints_json, evidence_json, opened_at, "
            "last_seen_at, resolved_at, resolved_by, rca_text, rca_status, actions_json, context_sample, source, "
            "memory_status) VALUES (?, 'RESOLVED', ?, ?, ?, ?, ?, ?, ?, 'seed', ?, 'ok', ?, ?, ?, 'pending')",
            (service, severity, fingerprints[0]["fp"], json.dumps(fingerprints), json.dumps(evidence), opened_at,
             resolved_at, resolved_at, rca_text, json.dumps(actions), context, source),
        )
        seq = cur.lastrowid
        conn.execute("UPDATE incidents SET id = ? WHERE seq = ?", (f"INC-{seq:04d}", seq))
        conn.commit()
    finally:
        conn.close()
    return get(f"INC-{seq:04d}")  # type: ignore[return-value]


def set_actions(incident_id: str, actions: List[dict]) -> None:
    conn = get_db_connection()
    try:
        conn.execute("UPDATE incidents SET actions_json = ?, version = version + 1 WHERE id = ?",
                     (json.dumps(actions), incident_id))
        conn.commit()
    finally:
        conn.close()


def mark_notified(incident_id: str, now: int) -> None:
    conn = get_db_connection()
    try:
        conn.execute("UPDATE incidents SET last_notified_at = ?, notify_count = notify_count + 1 WHERE id = ?",
                     (now, incident_id))
        conn.commit()
    finally:
        conn.close()


def transition(incident_id: str, new_status: str, expected_version: Optional[int], actor: str, now: int,
               detail: str = "") -> Dict[str, Any]:
    """Move an incident to ``new_status``.

    ``expected_version`` enforces compare-and-swap for operator actions; internal callers that always act on a
    fresh read pass ``None`` to skip the version check (the state check still applies atomically).
    """
    inc = get(incident_id)
    if inc is None:
        raise NotFound(incident_id)
    if expected_version is not None and inc["version"] != expected_version:
        raise ConflictError(f"{incident_id} is at version {inc['version']}, not {expected_version}")
    if new_status not in TRANSITIONS.get(inc["status"], ()):
        raise InvalidTransition(f"{inc['status']} -> {new_status} is not allowed")

    sets = ["status = ?", "version = version + 1"]
    args: List[Any] = [new_status]
    if new_status == "ACKED":
        sets.append("acked_at = ?"); args.append(now)
    if new_status == "RESOLVED":
        sets += ["resolved_at = ?", "resolved_by = ?"]; args += [now, actor]
    if new_status == "OPEN":
        sets.append("healthy_windows = 0")

    conn = get_db_connection()
    try:
        # The WHERE clause re-checks status and version so a concurrent writer cannot be overwritten.
        cur = conn.execute(
            f"UPDATE incidents SET {', '.join(sets)} WHERE id = ? AND status = ? AND version = ?",
            (*args, incident_id, inc["status"], inc["version"]),
        )
        if cur.rowcount != 1:
            conn.rollback()
            raise ConflictError(f"{incident_id} changed while transitioning")
        conn.execute(
            "INSERT INTO incident_events (incident_id, ts, kind, actor, detail) VALUES (?, ?, ?, ?, ?)",
            (incident_id, now, f"{inc['status']}->{new_status}", actor, detail[:1000]),
        )
        conn.commit()
    finally:
        conn.close()
    return get(incident_id)  # type: ignore[return-value]
