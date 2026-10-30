"""Unit tests for expanded, injection-proof observability and telemetry tools."""

import json
from unittest.mock import patch, MagicMock
import pytest

from agent.external_obs_tools import (
    get_feature_flags,
    get_trace_by_id,
    query_service_logs,
    query_service_metrics,
    query_service_traces,
)
from agent.tools import (
    get_logs,
    get_logs_for_service,
    get_traces,
    get_traces_for_service,
    search_docs,
)
from db import events as store


# ─── Security & Injection Rejection Tests ────────────────────────────────────

def test_query_service_metrics_rejects_injection():
    # Attempt PromQL injection via service_name
    malicious = 'checkout", status=~"5.."} or 1==1 #'
    res = query_service_metrics(service_name=malicious)
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_argument"
    assert "Invalid service_name" in res["error"]["message"]


def test_query_service_metrics_rejects_unknown_metric():
    res = query_service_metrics(service_name="checkout", metric_type="arbitrary_raw_metric")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_argument"
    assert "Unknown metric_type" in res["error"]["message"]


def test_query_service_logs_rejects_injection():
    # Attempt LogQL injection
    malicious_svc = 'checkout"} |= "bad" #'
    res = query_service_logs(service_name=malicious_svc)
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_argument"

    # Malicious log level
    res2 = query_service_logs(service_name="checkout", level="INVALID_LEVEL")
    assert res2["ok"] is False
    assert res2["error"]["code"] == "invalid_argument"


def test_get_trace_by_id_rejects_invalid_trace_id():
    # Path traversal / injection
    res = get_trace_by_id("../../etc/passwd")
    assert res["ok"] is False
    assert res["error"]["code"] == "invalid_argument"

    # Non-hex characters
    res2 = get_trace_by_id("non-hex-trace-id-1234567890")
    assert res2["ok"] is False
    assert res2["error"]["code"] == "invalid_argument"


# ─── Mocked API Tests ────────────────────────────────────────────────────────

def test_query_service_metrics_success():
    mock_resp = {
        "status": "success",
        "data": {
            "result": [
                {
                    "metric": {"service_name": "checkout"},
                    "values": [[1700000000, "0.05"], [1700000060, "0.07"]],
                }
            ]
        },
    }
    with patch("agent.external_obs_tools._http_get_json", return_value=mock_resp):
        res = query_service_metrics(service_name="checkout", metric_type="errors", minutes=5)
        assert res["ok"] is True
        assert res["source"] == "prometheus"
        assert len(res["data"]) == 1
        assert res["data"][0]["latest_value"] == "0.07"


def test_query_service_logs_success():
    mock_resp = {
        "data": {
            "result": [
                {
                    "stream": {"service_name": "checkout", "trace_id": "abcdef1234567890"},
                    "values": [["1700000000000000000", "Payment gateway timed out: HTTP 504"]],
                }
            ]
        }
    }
    with patch("agent.external_obs_tools._http_get_json", return_value=mock_resp):
        res = query_service_logs(service_name="checkout", level="ERROR", minutes=5)
        assert res["ok"] is True
        assert res["source"] == "loki"
        assert len(res["data"]) == 1
        assert "Payment gateway" in res["data"][0]["line"]
        assert res["data"][0]["trace_id"] == "abcdef1234567890"


def test_query_service_traces_tempo_success():
    mock_resp = {
        "traces": [
            {
                "traceID": "4f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c",
                "rootServiceName": "checkout",
                "rootTraceName": "POST /checkout",
                "durationMs": 850.5,
                "startTimeUnixNano": 1700000000000000000,
            }
        ]
    }
    with patch("agent.external_obs_tools._http_get_json", return_value=mock_resp):
        res = query_service_traces(service_name="checkout", minutes=10)
        assert res["ok"] is True
        assert res["source"] == "tempo"
        assert len(res["data"]) == 1
        assert res["data"][0]["trace_id"] == "4f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c"


def test_get_feature_flags_success():
    mock_resp = {
        "flags": {
            "paymentFailure": {"state": "ENABLED", "defaultVariant": "on"},
            "shippingFailure": {"state": "DISABLED"},
        }
    }
    with patch("agent.external_obs_tools._http_get_json", return_value=mock_resp):
        res = get_feature_flags()
        assert res["ok"] is True
        assert res["source"] == "flagd"
        assert "paymentFailure" in res["data"]


# ─── SQLite Event Store & RAG Tests ──────────────────────────────────────────

def test_event_store_log_and_trace_tools():
    t0 = store.now_ms()
    events = [
        {"id": "ev-1", "ts": t0 - 1000, "level": "ERROR", "route": "POST /checkout", "status": 500, "duration_ms": 1200.0, "message": "Failed"},
        {"id": "ev-2", "ts": t0 - 500, "level": "INFO", "route": "GET /cart", "status": 200, "duration_ms": 15.0, "message": "OK"},
    ]
    store.insert_events("checkout", events)

    # get_logs_for_service
    logs = get_logs_for_service("checkout", minutes=5)
    assert logs["source"] == "events"
    assert len(logs["sample_lines"]) == 2

    # get_traces_for_service now returns Tempo/Jaeger trace events from the event store.
    # The events inserted above have no source=tempo/jaeger marker so they are regular app logs,
    # not traces.  The result should be empty but must contain the expected keys.
    traces = get_traces_for_service("checkout", minutes=5, min_duration_ms=100.0)
    assert traces["source"] == "events_traces"
    assert "sample_lines" in traces
    assert "match_count" in traces


def test_search_docs_rag_tool():
    # Tests that search_docs returns results from the pre-generated index
    res = search_docs(query="checkout", limit=3)
    assert res["ok"] is True
    assert res["count"] > 0
    assert len(res["results"]) <= 3
    # First result should have title, heading, and text
    first = res["results"][0]
    assert "title" in first
    assert "heading" in first
    assert "text" in first
