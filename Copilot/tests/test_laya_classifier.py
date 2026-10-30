"""Unit tests for LayaClassifier: System 1 context and intent routing."""

from unittest.mock import MagicMock

from agent.laya_classifier import LayaClassifier, get_laya_classifier, set_laya_classifier


def test_classify_context_identical():
    classifier = LayaClassifier()
    context = "release: v2.3.1, config_rev: cfg-r41, pool: 12/20"
    result = classifier.classify_incident_context(current_context=context, past_context=context)
    assert result == "same_context"


def test_classify_context_different_release():
    classifier = LayaClassifier()
    current = "release: v2.4.0, config_rev: cfg-r41"
    past = "release: v2.3.1, config_rev: cfg-r41"
    result = classifier.classify_incident_context(current_context=current, past_context=past)
    assert result == "different_context"


def test_classify_context_different_config():
    classifier = LayaClassifier()
    current = "release: v2.3.1, config_rev: cfg-r42"
    past = "release: v2.3.1, config_rev: cfg-r41"
    result = classifier.classify_incident_context(current_context=current, past_context=past)
    assert result == "different_context"


def test_classify_context_missing():
    classifier = LayaClassifier()
    result = classifier.classify_incident_context(current_context="", past_context="release: v2.3.1")
    assert result == "different_context"


def test_classify_chat_intent_query():
    classifier = LayaClassifier()
    queries = [
        "fetch logs for the last 10 minutes",
        "show recent traces on /todos",
        "what is the current p95 latency for get_todos?",
        "check service health status",
        "search documentation for pool exhaustion runbook",
    ]
    for q in queries:
        assert classifier.classify_chat_intent(q) == "query", f"Expected 'query' for: {q}"


def test_classify_chat_intent_rca():
    classifier = LayaClassifier()
    queries = [
        "why is the todo-app throwing 500 errors?",
        "what is the root cause of this incident?",
        "diagnose the outage on /post_todos",
        "recommend a fix for this crash",
        "what went wrong with the database?",
    ]
    for q in queries:
        assert classifier.classify_chat_intent(q) == "rca", f"Expected 'rca' for: {q}"


def test_mocked_laya_router_success():
    classifier = LayaClassifier()
    mock_router = MagicMock()
    mock_router.decide.return_value = {"context_match": "same_context", "intent": "rca"}
    classifier._router = mock_router
    classifier._laya_available = True

    assert classifier.classify_incident_context("ctx1", past_context="ctx2") == "same_context"
    assert classifier.classify_chat_intent("random query") == "rca"
    assert mock_router.decide.call_count == 2


def test_singleton_accessor():
    clf1 = get_laya_classifier()
    clf2 = get_laya_classifier()
    assert clf1 is clf2
    set_laya_classifier(None)
    clf3 = get_laya_classifier()
    assert clf3 is not None
