from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.dashboard import router as dashboard_router
from db import events as store, incidents


@pytest.fixture
def dashboard_client():
    app = FastAPI(redirect_slashes=False)
    app.include_router(dashboard_router)
    return TestClient(app)


def test_get_incidents_empty(dashboard_client):
    res = dashboard_client.get("/api/incidents")
    assert res.status_code == 200
    assert res.json() == []


def test_get_incidents_with_filtering(dashboard_client):
    t0 = 1_700_000_000_000
    fp1 = [{"fp": "fp-1", "kind": "error_rate", "route": "POST /todos", "sig": "5xx"}]
    fp2 = [{"fp": "fp-2", "kind": "latency", "route": "GET /todos", "sig": "latency"}]

    inc1 = incidents.create("todo-app", fp1, "critical", ["500 error spike"], t0)
    inc2 = incidents.create("other-service", fp2, "warning", ["slow response"], t0 + 1000)

    # Transition inc1 to RESOLVED
    incidents.transition(inc1["id"], "ACKED", None, "tester", t0 + 2000)
    incidents.transition(inc1["id"], "RESOLVED", None, "tester", t0 + 3000, detail="pool flushed")

    # 1. All incidents
    res = dashboard_client.get("/api/incidents")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2

    # 2. Filter by service
    res_svc = dashboard_client.get("/api/incidents?service=todo-app")
    assert res_svc.status_code == 200
    data_svc = res_svc.json()
    assert len(data_svc) == 1
    assert data_svc[0]["id"] == inc1["id"]
    assert data_svc[0]["service"] == "todo-app"

    # 3. Filter by status
    res_stat = dashboard_client.get("/api/incidents?status=RESOLVED")
    assert res_stat.status_code == 200
    data_stat = res_stat.json()
    assert len(data_stat) == 1
    assert data_stat[0]["id"] == inc1["id"]
    assert data_stat[0]["status"] == "RESOLVED"

    res_open = dashboard_client.get("/api/incidents?status=OPEN")
    assert res_open.status_code == 200
    assert len(res_open.json()) == 1
    assert res_open.json()[0]["id"] == inc2["id"]


def test_get_incident_detail_and_timeline(dashboard_client):
    t0 = 1_700_000_000_000
    fps = [{"fp": "fp-10", "kind": "error_rate", "route": "POST /todos", "sig": "PoolStarvation"}]
    inc = incidents.create("todo-app", fps, "critical", ["connection pool exhausted"], t0)
    incidents.transition(inc["id"], "ACKED", None, "oncall", t0 + 1000, detail="Investigating pool")
    incidents.set_rca(inc["id"], "Connection leak in worker. Proposed action: flush_pool.", "ok", 1, 150)

    # Fetch detail
    res = dashboard_client.get(f"/api/incidents/{inc['id']}")
    assert res.status_code == 200
    d = res.json()
    assert d["id"] == inc["id"]
    assert d["severity"] == "critical"
    assert d["rca_status"] == "ok"
    assert "flush_pool" in (d["rca_text"] or "")
    assert len(d["timeline"]) >= 2  # 'opened' and 'OPEN->ACKED'
    assert d["timeline"][0]["kind"] == "opened"
    assert d["timeline"][1]["kind"] == "OPEN->ACKED"


def test_get_incident_detail_not_found(dashboard_client):
    res = dashboard_client.get("/api/incidents/INC-9999")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_get_memory_past_incidents(dashboard_client):
    t0 = 1_700_000_000_000
    fps = [{"fp": "fp-hist", "kind": "error_rate", "route": "POST /items", "sig": "DBTimeout"}]
    inc = incidents.insert_resolved(
        service="todo-app",
        fingerprints=fps,
        severity="critical",
        evidence=["DBTimeout on /items"],
        opened_at=t0,
        resolved_at=t0 + 600_000,
        rca_text="Database pool exhausted by rogue query.",
        actions=[{"action": "restart_worker", "ts": t0 + 100_000}, {"action": "flush_pool", "ts": t0 + 200_000}],
        context="release=v2.1.0 pool_size=10",
        source="seed",
    )

    res = dashboard_client.get("/api/memory/past-incidents")
    assert res.status_code == 200
    data = res.json()
    assert "bank_id" in data
    assert data["total"] >= 1
    match = next((m for m in data["memories"] if m["incident_id"] == inc["id"]), None)
    assert match is not None
    assert match["fix"] == "flush_pool"
    assert "restart_worker" in match["failed"]
    assert "release=v2.1.0" in match["narrative"]


def test_get_telemetry_overview(dashboard_client):
    t0 = store.now_ms()
    events = [
        {"id": "ev-1", "service": "todo-app", "ts": t0, "level": "INFO", "route": "GET /todos", "status": 200, "duration_ms": 15.0, "message": "ok"},
        {"id": "ev-2", "service": "todo-app", "ts": t0, "level": "ERROR", "route": "POST /todos", "status": 500, "duration_ms": 120.0, "message": "error"},
    ]
    store.insert_events("todo-app", events, t0)

    res = dashboard_client.get("/api/telemetry/overview?service=todo-app")
    assert res.status_code == 200
    data = res.json()
    assert data["service"] == "todo-app"
    assert "health" in data
    assert "error_rate" in data
    assert "latency" in data
    assert isinstance(data["active_incidents"], int)
    assert isinstance(data["total_incidents"], int)


def test_dashboard_ui_page_served():
    import os
    from fastapi.responses import FileResponse
    from agent.config import _PROJECT_ROOT
    frontend_dir = _PROJECT_ROOT / "frontend"
    app = FastAPI(redirect_slashes=False)

    @app.get("/dashboard")
    def dash():
        return FileResponse(os.path.join(frontend_dir, "dashboard.html"))

    client = TestClient(app)
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert "Incident Response Dashboard" in res.text
    assert "/api/telemetry/overview" in res.text


def test_chat_rca_registers_incident(dashboard_client):
    from api.router import register_or_update_chat_incident

    user_prompt = "Checkout is failing with 500 errors"
    agent_reply = (
        "**Symptom**: Checkout route failing with 100% 5xx errors.\n"
        "**Evidence**: Recent logs show database connection pool timeout.\n"
        "**Root Cause**: Connection pool exhausted under load.\n"
        "Proposed action: flush_pool"
    )

    inc_id = register_or_update_chat_incident(
        chat_id=42,
        user_prompt=user_prompt,
        agent_reply=agent_reply,
        llm_calls=2,
    )
    assert inc_id is not None
    assert inc_id.startswith("INC-")

    # Verify incident in database
    inc = incidents.get(inc_id)
    assert inc is not None
    assert inc["service"] == "checkout"
    assert inc["status"] == "OPEN"
    assert inc["proposed_action"] == "flush_pool"
    assert inc["rca_status"] == "ok"
    assert "Connection pool exhausted" in inc["rca_text"]

    # Verify incident appears in dashboard API
    res = dashboard_client.get("/api/incidents")
    assert res.status_code == 200
    incident_ids = [i["id"] for i in res.json()]
    assert inc_id in incident_ids


