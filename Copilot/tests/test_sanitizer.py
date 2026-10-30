"""Unit tests for clean_agent_output (anti-thought leak sanitizer)."""

import pytest
from agent.llm import clean_agent_output


def test_clean_agent_output_empty():
    assert clean_agent_output("") == ""
    assert clean_agent_output(None) == ""


def test_clean_agent_output_strips_xml_thought_tags():
    raw = "<thought>Checking get_error_rate tool results...</thought>There is currently no error rate breach."
    cleaned = clean_agent_output(raw)
    assert cleaned == "There is currently no error rate breach."
    assert "<thought>" not in cleaned


def test_clean_agent_output_strips_think_tags():
    raw = "<think>\nLet's analyze the service metrics.\np95 latency is 150ms.\n</think>\n\nService health is nominal."
    cleaned = clean_agent_output(raw)
    assert cleaned == "Service health is nominal."
    assert "<think>" not in cleaned


def test_clean_agent_output_strips_user_reported_monologue():
    raw = (
        "The user is asking for the current error rate across all routes. I've tried get_error_rate with different "
        "time windows, and both returned 0 total requests and 0 total errors with no routes listed. The service health "
        "shows \"unknown\" with \"no events ever received\". Recent logs also show 0 matches.\n\n"
        "This indicates that there's no telemetry data currently available - the monitored service hasn't received any events, "
        "or the events don't carry both route and status information. I should report what I found honestly: there's no data "
        "available to compute an error rate because no request events have been observed.There is currently no telemetry "
        "data available to compute an error rate across all routes.\n\n"
        "The last 5 and 30 minutes both returned zero request events (total_request_count: 0, total_error_count: 0, "
        "no routes listed), and service health reports \"unknown\" with \"no events ever received.\" Without request "
        "events carrying both a route and status, an error rate cannot be calculated at this time."
    )

    cleaned = clean_agent_output(raw)

    # Internal thoughts / self-talk must be stripped
    assert "The user is asking" not in cleaned
    assert "I've tried get_error_rate" not in cleaned
    assert "I should report what I found honestly" not in cleaned

    # Relevant data paragraphs must be preserved
    assert "There is currently no telemetry data available to compute an error rate across all routes." in cleaned
    assert "The last 5 and 30 minutes both returned zero request events" in cleaned
    assert "total_request_count: 0" in cleaned


def test_clean_agent_output_preserves_legitimate_paragraphs():
    legit = (
        "### Telemetry Overview\n\n"
        "The monitored service has processed 1,420 requests over the last 15 minutes across 4 active endpoints.\n\n"
        "The aggregate 5xx error rate is currently 0.00% with p95 latency holding at 42ms."
    )
    cleaned = clean_agent_output(legit)
    assert cleaned == legit
