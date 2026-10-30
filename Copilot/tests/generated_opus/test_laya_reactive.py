"""Laya System 1 classifier: chat intent routing and incident context matching (heuristic + router paths)."""

from unittest.mock import MagicMock

import pytest

from agent.laya_classifier import LayaClassifier, parse_context_attributes

VALID_INTENTS = {"rca", "query"}
VALID_CONTEXTS = {"same_context", "different_context"}


@pytest.fixture
def clf():
    c = LayaClassifier()
    c._router = None
    c._laya_available = False
    return c


# ── parse_context_attributes ─────────────────────────────────────────────────────────────────

def test_laya_parse_context_equals_format():
    """REQ: parse_context_attributes extracts release and config_rev from runtime context text
    (laya_classifier.parse_context_attributes docstring; event context is written as 'release=v2.4.0 config_rev=cfg-r41'
    per tests/test_memory.py fixtures and agent/prompts.py)."""
    assert parse_context_attributes("release=v2.4.0 config_rev=cfg-r41") == {"release": "v2.4.0", "config_rev": "cfg-r41"}


def test_laya_parse_context_colon_format_with_commas():
    """REQ: parse_context_attributes handles 'release: X, config_rev: Y' (format used in ARCHITECTURE §5 examples/tests)."""
    assert parse_context_attributes("release: v2.3.1, config_rev: cfg-r41, pool: 12/20") == {
        "release": "v2.3.1", "config_rev": "cfg-r41"}


def test_laya_parse_context_version_and_revision_synonyms():
    """REQ: 'version' is a release synonym and 'revision' a config revision synonym (laya_classifier regex intent)."""
    attrs = parse_context_attributes("version: 1.2.3 revision: abc123")
    assert attrs == {"release": "1.2.3", "config_rev": "abc123"}


@pytest.mark.parametrize("text", ["", None])
def test_laya_parse_context_empty_input_returns_none_attrs(text):
    """REQ: parse_context_attributes returns both attributes as None when nothing is present (docstring: 'extract known')."""
    assert parse_context_attributes(text) == {"release": None, "config_rev": None}


def test_laya_parse_context_unrelated_text_has_no_attrs():
    """REQ: only release and config_rev are extracted; unrelated runtime facts yield None (docstring)."""
    assert parse_context_attributes("pool=12/20 leaked_mb=400") == {"release": None, "config_rev": None}


def test_laya_parse_context_is_case_insensitive_on_keys():
    """REQ: attribute keys are matched case-insensitively (regex compiled with IGNORECASE)."""
    assert parse_context_attributes("RELEASE: v9.0.1")["release"] == "v9.0.1"


# ── classify_incident_context (heuristic fallback) ───────────────────────────────────────────

def test_laya_context_identical_ignoring_case_is_same(clf):
    """REQ: same_context when runtime environment, release and configuration match (classify_incident_context docstring)."""
    assert clf.classify_incident_context("Release=v2.4.0 Config_rev=cfg-r41",
                                         past_context="release=v2.4.0 config_rev=cfg-r41") == "same_context"


def test_laya_context_attribute_order_does_not_matter(clf):
    """REQ: same_context when release and configuration match, regardless of text ordering (docstring)."""
    assert clf.classify_incident_context("config_rev=cfg-r41 release=v2.4.0",
                                         past_context="release=v2.4.0 config_rev=cfg-r41") == "same_context"


def test_laya_context_release_present_only_in_current_is_different(clf):
    """REQ: different_context on a release mismatch (docstring); a release known only on one side is a mismatch."""
    assert clf.classify_incident_context("release=v2.4.0 config_rev=cfg-r41",
                                         past_context="config_rev=cfg-r41") == "different_context"


def test_laya_context_config_present_only_in_past_is_different(clf):
    """REQ: different_context on a config revision mismatch (classify_incident_context docstring)."""
    assert clf.classify_incident_context("release=v2.4.0",
                                         past_context="release=v2.4.0 config_rev=cfg-r41") == "different_context"


def test_laya_context_different_evidence_signature_is_different(clf):
    """REQ: 'different_context' if there is a mismatch in release, config revision, OR EVIDENCE SIGNATURES
    (classify_incident_context docstring; ARCHITECTURE.md §5 'Compares runtime facts (release versions, config revisions,
    exception signatures)')."""
    ctx = "release=v2.4.0 config_rev=cfg-r41"
    res = clf.classify_incident_context(
        current_context=ctx,
        current_evidence=["503 DatabaseConnectionTimeout: Connection pool exhausted"],
        past_context=ctx,
        past_evidence=["500 UndefinedTable: relation payment_transactions_v2 does not exist"],
    )
    assert res == "different_context"


@pytest.mark.parametrize("cur,past", [("", ""), (None, None), ("   ", "release=v1"), ("release=v1", "  \n ")])
def test_laya_context_missing_side_is_different_for_safety(clf, cur, past):
    """REQ: missing/blank context on either side is treated as different_context 'for safety' (heuristic comment);
    must not crash on None."""
    assert clf.classify_incident_context(cur, past_context=past) == "different_context"


def test_laya_context_unicode_and_long_input_returns_valid_label(clf):
    """REQ: classifier always returns one of the two documented labels and never crashes (module docstring:
    'zero crashes and consistent routing')."""
    cur = "release=v2.4.0 ünïcødé 🚀 " + "x" * 50_000
    assert clf.classify_incident_context(cur, ["é"], "release=v2.4.0 日本", ["ß"]) in VALID_CONTEXTS


# ── classify_incident_context (laya router path) ─────────────────────────────────────────────

def _with_router(decide):
    c = LayaClassifier()
    c._router = MagicMock()
    c._router.decide.side_effect = decide
    c._laya_available = True
    return c


def test_laya_context_router_invalid_answer_falls_back_to_heuristic():
    """REQ: when laya returns no usable answer the deterministic heuristic is used (module docstring 'Resilience')."""
    c = _with_router(lambda **kw: {"context_match": "maybe"})
    assert c.classify_incident_context("release=v1", past_context="release=v2") == "different_context"
    assert c._router.decide.called


def test_laya_context_router_exception_falls_back_to_heuristic():
    """REQ: a failing laya router never crashes classification; fallback heuristic is used (module docstring)."""
    def boom(**kw):
        raise RuntimeError("router down")
    c = _with_router(boom)
    assert c.classify_incident_context("release=v1", past_context="release=v1") == "same_context"


def test_laya_context_router_receives_both_contexts_and_evidence():
    """REQ: router compares current incident context against historical context (module docstring task 1)."""
    seen = {}

    def decide(state, questions):
        seen["state"], seen["q"] = state, questions
        return {"context_match": "different_context"}
    c = _with_router(decide)
    assert c.classify_incident_context("CUR-CTX", ["cur-ev"], "PAST-CTX", ["past-ev"]) == "different_context"
    for piece in ("CUR-CTX", "cur-ev", "PAST-CTX", "past-ev"):
        assert piece in seen["state"]
    assert set(seen["q"]["context_match"]["options"]) == VALID_CONTEXTS


# ── classify_chat_intent ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "fetch the error logs for checkout",
    "list recent traces for /api/checkout",
    "show p95 latency for catalog",
    "search docs for the pool exhaustion runbook",
    "show slow requests in the last 10 minutes",
    "display the error rate per route",
])
def test_laya_intent_telemetry_lookups_route_to_query(clf, msg):
    """REQ: 'query' for telemetry lookups, logs, metrics, traces, and documentation searches
    (classify_chat_intent docstring; ARCHITECTURE.md §5.2)."""
    assert clf.classify_chat_intent(msg) == "query"


@pytest.mark.parametrize("msg", [
    "Payments are failing since the last deploy",
    "the catalog service is slow",
    "How do I remediate the pool exhaustion?",
    "WHY IS CHECKOUT DOWN",
    "what's wrong with checkout?",
    "the frontend is broken",
])
def test_laya_intent_diagnosis_and_remediation_route_to_rca(clf, msg):
    """REQ: 'rca' for incident diagnosis, root cause questions, and remediation requests (classify_chat_intent docstring)."""
    assert clf.classify_chat_intent(msg) == "rca"


def test_laya_intent_crashing_statement_routes_to_rca(clf):
    """REQ: 'rca' for incident diagnosis (classify_chat_intent docstring); 'crash' is an explicit RCA keyword and the
    heuristic comment says diagnostic statements without query verbs are RCA. 'checkout keeps crashing' is a crash report."""
    assert clf.classify_chat_intent("checkout keeps crashing") == "rca"


def test_laya_intent_keyword_word_boundaries_avoid_false_rca(clf):
    """REQ: 'query' for log lookups (docstring); words merely containing RCA keywords ('fixture', 'downstream') must not
    flip a log lookup to RCA."""
    assert clf.classify_chat_intent("show the fixture logs for downstream calls") == "query"


@pytest.mark.parametrize("msg", [None, "", "   ", "🚀🚀🚀", "日本語のメッセージ", "a" * 100_000],
                         ids=["none", "empty", "blank", "emoji", "cjk", "very-long"])
def test_laya_intent_odd_inputs_return_valid_label(clf, msg):
    """REQ: classify_chat_intent returns 'rca' or 'query' and never crashes (docstring Returns; module 'zero crashes')."""
    assert clf.classify_chat_intent(msg) in VALID_INTENTS


def test_laya_intent_unicode_rca_message(clf):
    """REQ: diagnosis requests are 'rca' irrespective of surrounding unicode (docstring)."""
    assert clf.classify_chat_intent("why is checkout failing 🚨 ñandú?") == "rca"


def test_laya_intent_router_answer_used_when_valid():
    """REQ: when laya is installed the Router decides intent (module docstring task 2)."""
    c = _with_router(lambda **kw: {"intent": "query"})
    assert c.classify_chat_intent("why is it broken") == "query"
    assert "why is it broken" in c._router.decide.call_args.kwargs["state"]


def test_laya_intent_router_invalid_answer_falls_back():
    """REQ: unusable laya output falls back to the deterministic heuristic (module docstring 'Resilience')."""
    c = _with_router(lambda **kw: {"intent": "chitchat"})
    assert c.classify_chat_intent("diagnose the outage") == "rca"


def test_laya_intent_router_exception_falls_back():
    """REQ: router exceptions never crash routing (module docstring 'zero crashes')."""
    def boom(**kw):
        raise TimeoutError()
    c = _with_router(boom)
    assert c.classify_chat_intent("show logs") == "query"


def test_laya_intent_is_deterministic_on_repeat(clf):
    """REQ: heuristic fallback is deterministic ('consistent routing', module docstring)."""
    msgs = ["why is checkout down", "show logs", "service is slow", "list traces"]
    first = [clf.classify_chat_intent(m) for m in msgs]
    assert [clf.classify_chat_intent(m) for m in msgs] == first


@pytest.mark.skip(reason="AMBIGUOUS: 'show me the logs for the checkout outage' is both a log lookup (query per docstring "
                         "and QUERY_SYSTEM_INSTRUCTION rule 1) and mentions an outage (RCA keyword). Code returns 'rca'.")
def test_laya_intent_log_request_mentioning_outage():
    """REQ: unclear - log lookup ('query') vs outage mention ('rca')."""
