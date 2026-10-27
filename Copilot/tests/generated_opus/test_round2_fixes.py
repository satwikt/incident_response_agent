"""Regression tests for the Medium/Low fixes of the second round (bug_report_v2.xlsx): B07 B08 B22 B24 B26 B13 B14."""

import pytest

from agent.agent import SYSTEM_INSTRUCTION
from agent.laya_classifier import LayaClassifier
from agent.llm import clean_agent_output
from tests.generated_opus.test_chat_router_reactive import new_chat

clf = LayaClassifier()


@pytest.mark.parametrize("msg", [
    "show failed requests for checkout",
    "fetch logs for the failing checkout service",
    "show me the logs for the checkout outage",
    "list the error traces from the last 10 minutes",
    "get the 5xx error rate for catalog",
])
def test_b07_lookup_verbs_route_to_the_query_agent(msg):
    assert clf.classify_chat_intent(msg) == "query"


@pytest.mark.parametrize("msg", [
    "show me why checkout is down",
    "why is checkout failing?",
    "get me a fix for pool exhaustion",
    "show the root cause of the 500s",
    "diagnose the checkout outage",
])
def test_b07_diagnostic_questions_still_route_to_rca(msg):
    assert clf.classify_chat_intent(msg) == "rca"


@pytest.mark.parametrize("msg", ["checkout keeps crashing", "the service crashed", "tell me about the downtime",
                                 "catalog is unavailable"])
def test_b08_inflected_diagnostic_words_route_to_rca(msg):
    assert clf.classify_chat_intent(msg) == "rca"


def test_b22_reasoning_before_the_symptom_heading_is_dropped():
    raw = ("We have error rate high, logs show consistent errors. Likely database. Choose enable_fallback.\n"
           "**Symptom**\nPOST /todos is failing.\n\n**Root cause**\nDB down.\n\nProposed action: enable_fallback")
    out = clean_agent_output(raw)
    assert out.startswith("**Symptom**") and "Choose enable_fallback" not in out and out.endswith("enable_fallback")


def test_b22_text_without_a_symptom_heading_is_untouched():
    assert clean_agent_output("Logs for checkout:\nERROR | POST /checkout") == "Logs for checkout:\nERROR | POST /checkout"


def test_b13_b14_cleanup_keeps_identifiers_and_ordinary_sentences():
    assert clean_agent_output("Raised psycopg2.errors.UndefinedTable on checkout.") == "Raised psycopg2.errors.UndefinedTable on checkout."
    assert clean_agent_output("Health checking of the gateway shows 20/20 connections.") == "Health checking of the gateway shows 20/20 connections."
    assert "Let me check" not in clean_agent_output("Let me check the logs.\nThe pool is exhausted.")
    assert "\n\n" in clean_agent_output("The pool was drained.Then the worker restarted.")


def test_b24_prompt_maps_active_faults_to_disable_fault():
    assert "disable_fault" in SYSTEM_INSTRUCTION


@pytest.mark.parametrize("message", ["", "   ", "x" * 8001])
def test_b26_bad_chat_messages_are_rejected_before_any_llm_call(chat_client, runners, message):
    rca, query = runners
    cid = new_chat(chat_client)
    assert chat_client.post(f"/ask/{cid}", json={"message": message}).status_code == 422
    assert rca.calls == [] and query.calls == []


def test_b26_rename_title_is_length_limited(chat_client):
    cid = new_chat(chat_client)
    assert chat_client.put(f"/chat/{cid}/rename", json={"title": "T" * 201}).status_code == 422
    assert chat_client.put(f"/chat/{cid}/rename", json={"title": "T" * 200}).status_code == 200


def test_diagnosis_retries_once_when_the_model_answers_nothing():
    """A reasoning model can spend its whole token budget thinking and return no text: one retry, then the real answer."""
    import asyncio

    from agent.diagnosis import DiagnosisResult, DiagnosisService
    from db import incidents

    inc = incidents.create("checkout", [{"fp": "fp-d", "kind": "error_rate", "route": "POST /checkout", "sig": "5xx"}],
                           "critical", ["5xx"], 1_700_000_000_000)
    answers = iter(["", "**Symptom**: ok"])
    calls = []

    async def diagnose(prompt):
        calls.append(prompt)
        return DiagnosisResult(text=next(answers))

    svc = DiagnosisService(diagnose, timeout_s=5)
    assert asyncio.run(svc.process(inc["id"], "p")) == "ok" and len(calls) == 2
    assert incidents.get(inc["id"])["rca_text"] == "**Symptom**: ok"


def test_diagnosis_gives_up_after_one_empty_retry():
    import asyncio

    from agent.diagnosis import DiagnosisResult, DiagnosisService
    from db import incidents

    inc = incidents.create("checkout", [{"fp": "fp-e", "kind": "error_rate", "route": "POST /checkout", "sig": "5xx"}],
                           "critical", ["5xx"], 1_700_000_000_000)

    async def diagnose(prompt):
        return DiagnosisResult(text="")

    assert asyncio.run(DiagnosisService(diagnose, timeout_s=5).process(inc["id"], "p")) == "failed"


def test_failed_attempt_retain_can_be_switched_off(dash_client, monkeypatch):
    """MEMORY_RETAIN_FAILED_ATTEMPTS=off: a Fix Failed click is recorded on the incident but costs no memory-LLM call."""
    from agent import config, cycle
    from db import incidents
    from tests.generated_opus.conftest import FakeMemory

    mem = FakeMemory()
    monkeypatch.setattr(cycle, "_memory", mem)
    monkeypatch.setattr(config, "MEMORY_RETAIN_FAILED_ATTEMPTS", False)
    iid = incidents.create("checkout", [{"fp": "fp-q", "kind": "chat_rca", "route": "chat", "sig": "s"}], "critical", ["5xx"], 1)["id"]
    body = dash_client.post(f"/api/incidents/{iid}/feedback", json={"action": "flush_pool", "worked": False}).json()
    assert body["status"] == "REMEDIATING" and body["memory_retained"] is False and mem.retained == []
    assert [a["action"] for a in incidents.get(iid)["actions"]] == ["flush_pool"]


def test_recalled_failed_actions_are_deduplicated():
    from tests.generated_opus.conftest import FakeHindsightClient
    from agent.memory import HindsightMemory
    from types import SimpleNamespace as NS

    item = NS(document_id="INC-9", text="t", scores=NS(final=0.9),
              tags=["fix:disable_fault", "failed:rollback_config", "failed:rollback_config", "failed:restart_worker"])
    client = FakeHindsightClient(results=lambda tags, match: [item])
    got = HindsightMemory(client, "bank").recall({"fingerprints": [{"fp": "x"}]}, "q").matches[0]
    assert got.failed == ["rollback_config", "restart_worker"] and got.fix == "disable_fault"


def test_recall_passes_prefer_observations_false_when_the_client_has_that_argument():
    """hindsight-client 1.1 sends the unset argument as null and the server answers 422: every recall silently failed."""
    from agent.memory import HindsightMemory
    from types import SimpleNamespace as NS

    seen = []

    class NewClient:
        def create_bank(self, **kw):
            pass

        def recall(self, bank_id, query, tags=None, tags_match="any", max_tokens=4096, prefer_observations=None):
            seen.append(prefer_observations)
            return NS(results=[])

    class OldClient(NewClient):
        def recall(self, bank_id, query, tags=None, tags_match="any", max_tokens=4096):
            seen.append("old")
            return NS(results=[])

    HindsightMemory(NewClient(), "b").recall({"fingerprints": [{"fp": "x"}]}, "q")
    HindsightMemory(OldClient(), "b").recall({"fingerprints": []}, "q")
    assert seen == [False, False, "old"]


@pytest.mark.parametrize("cur,past,expected", [
    (["POST /checkout -> 500 (9ms)"], ["POST /checkout 500", "payment_failure fault declines POST /checkout with 500"], "same_context"),
    (["POST /checkout -> 503 (9ms)", "root cause of the 503 errors"], ["POST /checkout 500", "payment_failure declines POST /checkout with 500"], "different_context"),
    (["POST /checkout -> 500", "db_pool_exhaustion leaking connections"], ["POST /checkout 500", "payment_failure declines POST /checkout"], "different_context"),
    (["GET /products -> 503 DatabaseConnectionTimeout"], ["POST /checkout 500 UndefinedTable"], "different_context"),
])
def test_context_split_by_status_code_and_named_cause(cur, past, expected):
    """Same route is not same context: a different status code or a different named cause is a different incident."""
    assert clf.classify_incident_context("", cur, "", past) == expected
