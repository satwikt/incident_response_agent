from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.dashboard import router as dashboard_router
from db import incidents
from agent import config


@pytest.fixture
def dashboard_client():
    app = FastAPI(redirect_slashes=False)
    app.include_router(dashboard_router)
    return TestClient(app)


def test_feedback_fix_worked(dashboard_client):
    t0 = 1_700_000_000_000
    fp = [{"fp": "fp-1", "kind": "error_rate", "route": "POST /todos", "sig": "5xx"}]
    inc = incidents.create("todo-app", fp, "critical", ["500 error spike"], t0)
    incidents.update_fields(inc["id"], proposed_action="restart_worker")

    res = dashboard_client.post(
        f"/api/incidents/{inc['id']}/feedback",
        json={"action": "restart_worker", "worked": True, "notes": "Pool exhaustion cleared"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["incident_id"] == inc["id"]
    assert data["status"] == "RESOLVED"
    assert data["action"] == "restart_worker"
    assert data["worked"] is True
    assert "resolved" in data["message"].lower()

    # Verify db state
    updated = incidents.get(inc["id"])
    assert updated["status"] == "RESOLVED"
    assert len(updated["actions"]) == 1
    assert updated["actions"][0]["action"] == "restart_worker"
    assert updated["actions"][0]["worked"] is True
    assert updated["actions"][0]["notes"] == "Pool exhaustion cleared"

    # Verify timeline audit log
    events = incidents.list_events(inc["id"])
    assert any("RESOLVED" in e["kind"] for e in events)


def test_feedback_fix_failed_recommends_next_action(dashboard_client):
    t0 = 1_700_000_000_000
    fp = [{"fp": "fp-2", "kind": "latency", "route": "GET /cart", "sig": "slow"}]
    inc = incidents.create("cartservice", fp, "warning", ["High latency"], t0)
    incidents.update_fields(inc["id"], proposed_action="restart_worker")

    res = dashboard_client.post(
        f"/api/incidents/{inc['id']}/feedback",
        json={"action": "restart_worker", "worked": False, "notes": "Latency remained high"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["incident_id"] == inc["id"]
    assert data["status"] == "REMEDIATING"
    assert data["action"] == "restart_worker"
    assert data["worked"] is False
    assert data["new_recommendation"] is not None
    assert data["new_recommendation"] != "restart_worker"
    assert data["new_recommendation"] in config.ALLOWED_ACTIONS

    # Verify db state
    updated = incidents.get(inc["id"])
    assert updated["status"] == "REMEDIATING"
    assert updated["proposed_action"] == data["new_recommendation"]
    assert len(updated["actions"]) == 1
    assert updated["actions"][0]["worked"] is False

    # Second feedback: the next recommended action works!
    next_action = data["new_recommendation"]
    res2 = dashboard_client.post(
        f"/api/incidents/{inc['id']}/feedback",
        json={"action": next_action, "worked": True, "notes": "Latency normalized"},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "RESOLVED"
    assert data2["worked"] is True

    updated2 = incidents.get(inc["id"])
    assert updated2["status"] == "RESOLVED"
    assert len(updated2["actions"]) == 2


def test_feedback_invalid_action(dashboard_client):
    t0 = 1_700_000_000_000
    fp = [{"fp": "fp-3", "kind": "error_rate", "route": "/checkout", "sig": "err"}]
    inc = incidents.create("checkoutservice", fp, "critical", ["Crashing"], t0)

    res = dashboard_client.post(
        f"/api/incidents/{inc['id']}/feedback",
        json={"action": "reboot_host_machine", "worked": True},
    )
    assert res.status_code == 400
    assert "Invalid action" in res.json()["detail"]


def test_feedback_action_alias_normalizes(dashboard_client):
    t0 = 1_700_000_000_000
    fp = [{"fp": "fp-alias", "kind": "error_rate", "route": "/checkout", "sig": "err"}]
    inc = incidents.create("checkoutservice", fp, "critical", ["Crashing"], t0)

    res = dashboard_client.post(
        f"/api/incidents/{inc['id']}/feedback",
        json={"action": "restart_pod", "worked": True},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["action"] == "restart_worker"
    assert data["status"] == "RESOLVED"


def test_feedback_unknown_incident(dashboard_client):
    res = dashboard_client.post(
        "/api/incidents/INC-DOES-NOT-EXIST/feedback",
        json={"action": "restart_worker", "worked": True},
    )
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()
