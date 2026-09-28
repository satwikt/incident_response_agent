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
    conn.commit()
    conn.close()


init_db()
