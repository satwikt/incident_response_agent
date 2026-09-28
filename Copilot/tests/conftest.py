import os
import tempfile

# Must be set before the app modules are imported: config reads the environment at import time.
_TMP = tempfile.mkdtemp(prefix="copilot-tests-")
os.environ["COPILOT_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["INGEST_KEYS"] = "todo-app:key-for-todo-app,other-app:key-for-other-app"
os.environ["APP_SERVICE_NAME"] = "todo-app"
os.environ["MIN_REQUESTS"] = "20"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent import config  # noqa: E402
from api import ingest  # noqa: E402
from db import events as store  # noqa: E402
from db.database import get_db_connection  # noqa: E402

KEY_A = "key-for-todo-app"
KEY_B = "key-for-other-app"


def make_app() -> FastAPI:
    app = FastAPI(redirect_slashes=False)
    app.include_router(ingest.router)
    return app


@pytest.fixture
def client():
    return TestClient(make_app())


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    conn = get_db_connection()
    for table in ("events", "incidents", "incident_events", "outbox"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('incidents', 'outbox')")
    conn.commit()
    conn.close()
    ingest.buckets.reset()
    monkeypatch.setattr(config, "INGEST_KEYS", {KEY_A: "todo-app", KEY_B: "other-app"})
    monkeypatch.setattr(config, "INGEST_RATE_BURST", 10_000)
    monkeypatch.setattr(config, "INGEST_RATE_PER_SEC", 10_000.0)
    yield


def ev(i="e1", **kw):
    base = {"id": i, "ts": store.now_ms(), "level": "INFO", "route": "GET /todos", "status": 200,
            "duration_ms": 12.0, "message": "GET /todos -> 200"}
    base.update(kw)
    return base


def headers(key=KEY_A):
    return {"X-Api-Key": key}


def count(service=None):
    conn = get_db_connection()
    try:
        if service:
            return conn.execute("SELECT COUNT(*) FROM events WHERE service = ?", (service,)).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()


def rows():
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY pk").fetchall()]
    finally:
        conn.close()
