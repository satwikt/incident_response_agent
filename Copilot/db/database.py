"""SQLite database for chat history and ingested telemetry events."""

import os
import sqlite3

from agent.config import COPILOT_DB_PATH

DB_PATH = COPILOT_DB_PATH
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            llm_calls INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
        )
        """
    )
    # Ingested telemetry. ``id`` is "<service>:<client id>" so one service cannot suppress another's events.
    # Time windows use received_at (server clock); ts is the client's clock, kept for display only.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            pk INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            service TEXT NOT NULL,
            ts INTEGER NOT NULL,
            received_at INTEGER NOT NULL,
            level TEXT NOT NULL,
            route TEXT,
            status INTEGER,
            duration_ms REAL,
            message TEXT,
            exception TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_svc_recv ON events (service, received_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_svc_route_recv ON events (service, route, received_at)")
    # Incidents: one row per ongoing or past problem. `version` is bumped on every change so operator actions
    # can use compare-and-swap. All timestamps are epoch milliseconds (UTC).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE,
            service TEXT NOT NULL,
            status TEXT NOT NULL,
            severity TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            fingerprints_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            opened_at INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL,
            acked_at INTEGER,
            resolved_at INTEGER,
            resolved_by TEXT,
            healthy_windows INTEGER NOT NULL DEFAULT 0,
            breach_windows INTEGER NOT NULL DEFAULT 1,
            rca_text TEXT,
            rca_status TEXT NOT NULL DEFAULT 'pending',
            last_notified_at INTEGER,
            notify_count INTEGER NOT NULL DEFAULT 0,
            recurrence_of TEXT,
            version INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_svc_status ON incidents (service, status)")
    # Cost of the diagnosis (LLM calls and tokens). Added after the first release, so existing databases are migrated.
    new_columns = {
        "llm_calls": "INTEGER", "llm_tokens": "INTEGER",
        "recalled_json": "TEXT",       # matches recalled from memory when the incident opened
        "actions_json": "TEXT",        # operational actions taken during the incident (from the action journal)
        "proposed_action": "TEXT",     # allow-listed action the diagnosis proposed
        "diagnosis_mode": "TEXT",      # full | verify (memory-first fast path) | templated
        "memory_status": "TEXT",       # off | pending | retained | indexed | failed
        "memory_op": "TEXT",           # Hindsight operation id of the retain
        "memory_detail": "TEXT",
        "context_sample": "TEXT",      # runtime context (release, config revision, pool...) seen on the errors
        "source": "TEXT",              # live | seed
    }
    have = {r["name"] for r in cursor.execute("PRAGMA table_info(incidents)")}
    for column, ddl in new_columns.items():
        if column not in have:
            cursor.execute(f"ALTER TABLE incidents ADD COLUMN {column} {ddl}")
    # Audit trail / timeline: every state change and notable event, with who did it.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS incident_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            ts INTEGER NOT NULL,
            kind TEXT NOT NULL,
            actor TEXT NOT NULL,
            detail TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_incident_events_inc ON incident_events (incident_id, id)")
    # Durable outbound notifications. `dedupe_key` makes enqueueing idempotent; `lease_until` lets a crashed sender's
    # in-flight items be picked up again.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            incident_id TEXT,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at INTEGER NOT NULL,
            lease_until INTEGER,
            created_at INTEGER NOT NULL,
            last_error TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox (status, next_attempt_at)")
    conn.commit()
    conn.close()


init_db()
