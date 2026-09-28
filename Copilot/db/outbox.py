"""Durable outbound notification queue.

* ``enqueue`` is idempotent on ``dedupe_key`` (a restart or a retry cannot create a second alert).
* ``claim`` hands out one due item at a time under a lease, so a sender that crashes mid-delivery does not lose it.
* Items for the same incident are delivered in order: a later item is never claimed while an earlier one is pending
  or in flight (an "opened" alert always precedes its "resolved" alert).
* After ``max_attempts`` failed deliveries an item becomes ``dead`` and stays visible for inspection.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from db.database import get_db_connection


def enqueue(kind: str, dedupe_key: str, incident_id: Optional[str], payload: Dict[str, Any], now: int) -> bool:
    """Returns True if newly queued, False if an item with this dedupe key already exists."""
    conn = get_db_connection()
    try:
        before = conn.total_changes
        conn.execute(
            "INSERT OR IGNORE INTO outbox (kind, dedupe_key, incident_id, payload_json, next_attempt_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, dedupe_key, incident_id, json.dumps(payload), now, now),
        )
        conn.commit()
        return conn.total_changes > before
    finally:
        conn.close()


def claim(now: int, lease_ms: int) -> Optional[Dict[str, Any]]:
    """Claim the oldest due item whose incident has nothing earlier still pending or in flight."""
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Expired leases go back to pending (their attempt already counted when they were claimed).
        conn.execute("UPDATE outbox SET status = 'pending', lease_until = NULL "
                     "WHERE status = 'inflight' AND lease_until IS NOT NULL AND lease_until <= ?", (now,))
        row = conn.execute(
            "SELECT o.* FROM outbox o WHERE o.status = 'pending' AND o.next_attempt_at <= ? "
            "AND NOT EXISTS (SELECT 1 FROM outbox e WHERE e.incident_id IS NOT NULL AND e.incident_id = o.incident_id "
            "AND e.id < o.id AND e.status IN ('pending', 'inflight')) "
            "ORDER BY o.id LIMIT 1",
            (now,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute("UPDATE outbox SET status = 'inflight', lease_until = ?, attempts = attempts + 1 WHERE id = ?",
                     (now + lease_ms, row["id"]))
        conn.commit()
        item = dict(row)
        item["attempts"] += 1
        item["payload"] = json.loads(item.pop("payload_json"))
        return item
    finally:
        conn.close()


def complete(item_id: int) -> None:
    _set(item_id, "done")


def skip(item_id: int, reason: str) -> None:
    _set(item_id, "skipped", reason)


def retry(item_id: int, delay_ms: int, error: str, now: int, max_attempts: int) -> str:
    """Schedule another attempt, or mark the item dead once ``max_attempts`` is reached. Returns the new status."""
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT attempts FROM outbox WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return "missing"
        if row["attempts"] >= max_attempts:
            conn.execute("UPDATE outbox SET status = 'dead', lease_until = NULL, last_error = ? WHERE id = ?",
                         (error[:300], item_id))
            status = "dead"
        else:
            conn.execute("UPDATE outbox SET status = 'pending', lease_until = NULL, next_attempt_at = ?, last_error = ? "
                         "WHERE id = ?", (now + delay_ms, error[:300], item_id))
            status = "pending"
        conn.commit()
        return status
    finally:
        conn.close()


def _set(item_id: int, status: str, error: Optional[str] = None) -> None:
    conn = get_db_connection()
    try:
        conn.execute("UPDATE outbox SET status = ?, lease_until = NULL, last_error = ? WHERE id = ?",
                     (status, error, item_id))
        conn.commit()
    finally:
        conn.close()


def counts() -> Dict[str, int]:
    conn = get_db_connection()
    try:
        return {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM outbox GROUP BY status")}
    finally:
        conn.close()
