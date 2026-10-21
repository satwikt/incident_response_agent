"""Unit tests for Three-Agent Architecture: Proactive, Reactive RCA, and Reactive Query."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.query_agent import build_query_agent, query_agent, QUERY_SYSTEM_INSTRUCTION
from agent.rca_agent import build_rca_agent, rca_agent_service, select_model_tier
from api.router import router as chat_router


@pytest.fixture
def test_client():
    app = FastAPI(redirect_slashes=False)
    app.include_router(chat_router)
    return TestClient(app)


def test_rca_tier_selection_no_prior_memory():
    incident = {
        "id": "inc-001",
        "service": "todo-service",
        "evidence": ["500 Internal Server Error"],
        "context_sample": "release: v2.3.1",
        "recalled": [],
    }
    tier = select_model_tier(incident, recalled_matches=[])
    assert tier == "ultra"


def test_rca_tier_selection_same_context():
    incident = {
        "id": "inc-002",
        "service": "todo-service",
        "evidence": ["503 PoolExhausted"],
        "context_sample": "release: v2.3.1, config_rev: cfg-r41",
    }
    recalled = [
        {
            "incident_id": "inc-001",
            "context": "release: v2.3.1, config_rev: cfg-r41",
            "evidence": ["503 PoolExhausted"],
            "fix": "flush_pool",
        }
    ]
    tier = select_model_tier(incident, recalled_matches=recalled)
    assert tier == "nano"


def test_rca_tier_selection_different_context():
    incident = {
        "id": "inc-003",
        "service": "todo-service",
        "evidence": ["500 SimulatedDatabaseStateError"],
        "context_sample": "release: v2.4.0, config_rev: cfg-r41",
    }
    recalled = [
        {
            "incident_id": "inc-001",
            "context": "release: v2.3.1, config_rev: cfg-r41",
            "evidence": ["500 SimulatedDatabaseStateError"],
            "fix": "rollback_release",
        }
    ]
    tier = select_model_tier(incident, recalled_matches=recalled)
    assert tier == "super"


def test_build_rca_agents():
    for tier in ("nano", "super", "ultra"):
        agent = build_rca_agent(tier)
        assert agent is not None
        assert agent.name == f"rca_agent_{tier}"
        assert len(agent.tools) == 15

    agent_from_service = rca_agent_service.get_agent_for_tier("nano")
    assert agent_from_service.name == "rca_agent_nano"


def test_query_agent_structure():
    agent = build_query_agent()
    assert agent.name == "query_agent"
    assert "read-only observability assistant" in agent.instruction
    assert len(agent.tools) == 15
    # Ensure all 5 tools are attached
    tool_names = {t.__name__ for t in agent.tools}
    assert "get_error_rate" in tool_names
    assert "get_latency" in tool_names
    assert "get_recent_logs" in tool_names
    assert "get_slow_requests" in tool_names
    assert "get_service_health" in tool_names


def test_chat_routing_query_intent(test_client):
    # Create a new chat
    resp = test_client.post("/chat/new")
    assert resp.status_code == 200
    chat_id = resp.json()["id"]

    # Mock query_runner.run_async to simulate query agent response
    mock_event = MagicMock()
    mock_event.is_final_response.return_value = True
    part_mock = MagicMock()
    part_mock.text = "Current error rate is 0.0% across all routes."
    mock_event.content.parts = [part_mock]
    mock_event.content.role = "model"
    mock_event.partial = False

    async def mock_run_async(*args, **kwargs):
        yield mock_event

    with patch("api.router.query_runner.run_async", side_effect=mock_run_async):
        ask_resp = test_client.post(
            f"/ask/{chat_id}",
            json={"message": "what is the current error rate for the service?"},
        )
        assert ask_resp.status_code == 200
        data = ask_resp.json()
        assert data["intent"] == "query"
        assert data["agent"] == "query_agent"
        assert "error rate" in data["response"]


def test_chat_routing_rca_intent(test_client):
    resp = test_client.post("/chat/new")
    assert resp.status_code == 200
    chat_id = resp.json()["id"]

    mock_event = MagicMock()
    mock_event.is_final_response.return_value = True
    part_mock = MagicMock()
    part_mock.text = "Root cause analysis indicates pool exhaustion."
    mock_event.content.parts = [part_mock]
    mock_event.content.role = "model"
    mock_event.partial = False

    async def mock_run_async(*args, **kwargs):
        yield mock_event

    with patch("api.router.runner.run_async", side_effect=mock_run_async):
        ask_resp = test_client.post(
            f"/ask/{chat_id}",
            json={"message": "why is the todo-app crashing? diagnose root cause."},
        )
        assert ask_resp.status_code == 200
        data = ask_resp.json()
        assert data["intent"] == "rca"
        assert data["agent"] == "rca_agent"
        assert "Root cause" in data["response"]
