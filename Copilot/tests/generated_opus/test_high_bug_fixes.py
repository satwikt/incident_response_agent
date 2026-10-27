"""Regression tests for the five High bugs found in the end-to-end test round (bug_report.xlsx B01-B05).

B01 memory status is reconciled with Hindsight   B02 Groq fallback has its own endpoint
B03 provider errors never reach the user raw     B04 Nemotron tier routing is wired into chat RCA
B05 request log lines become route/status events (adapter side: otel_demo/.../tests/test_clients.py)
"""

import pytest

import api.dashboard as dashboard
import api.router as router
from agent import cycle, config, llm
from agent.llm import scrub_secrets
from db import incidents
from tests.generated_opus.conftest import FakeMemory, make_event
from tests.generated_opus.test_chat_router_reactive import RCA_REPLY, ask, new_chat


# ── B02 ───────────────────────────────────────────────────────────────────────────────────────

def test_b02_primary_call_has_no_litellm_fallbacks_kwarg(monkeypatch):
    """LiteLLM's `fallbacks=` entry re-called the primary model instead of Groq (the Groq key then hit the wrong
    endpoint). The Groq fallback is the explicit fallback_model instance instead."""
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk-test")
    calls = []

    class Fake:
        def __init__(self, model, fallback_model=None, **kw):
            calls.append({"model": model, "fallback_model": fallback_model, **kw})

    monkeypatch.setattr(llm, "_provider_safe_litellm_class", lambda: Fake)
    llm.build_nemotron_model("super")
    primary = [c for c in calls if c["model"].startswith("openai/nvidia")][0]
    groq = [c for c in calls if c["model"].startswith("groq/")][0]
    assert "fallbacks" not in primary and primary["api_base"] == config.NVIDIA_BASE_URL
    assert primary["fallback_model"] is not None and groq["api_key"] == "gsk-test" and "api_base" not in groq


def test_b02_provider_safe_model_falls_back_to_groq_instance():
    import asyncio

    cls = llm._provider_safe_litellm_class()
    if cls.__name__ != "ProviderSafeLiteLlm":
        pytest.skip("google-adk/litellm not installed")

    seen = []

    class Groq:
        model = "groq/x"

        async def generate_content_async(self, req, stream=False):
            seen.append(req.model)       # ADK uses the request's model, so it must name the fallback, not the primary
            yield "from-groq"

    m = cls(model="openai/nvidia/none", fallback_model=Groq(), api_key="k", api_base="http://127.0.0.1:9/v1",
            timeout=1, num_retries=0)

    async def run():
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types
        req = LlmRequest(model=m.model, contents=[types.Content(role="user", parts=[types.Part(text="hi")])])
        return [r async for r in m.generate_content_async(req)]

    assert asyncio.run(run()) == ["from-groq"]
    assert seen == ["groq/x"]


# ── B03 ───────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Incorrect API key provided: gsk_A67R********************************************zpga.",
    "key nvapi-abc123DEF456ghi rejected",
    "Authorization: Bearer abcdefgh12345678",
])
def test_b03_scrub_secrets_masks_keys(text):
    out = scrub_secrets(text)
    assert "gsk_" not in out and "nvapi-" not in out and "abcdefgh12345678" not in out and "<redacted>" in out


def test_b03_chat_error_reply_hides_provider_details(chat_client, runners):
    rca, _ = runners
    rca.exc = RuntimeError("AuthenticationError: Incorrect API key provided: gsk_A67R************zpga litellm.internal")
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "why is checkout failing?").json()
    assert data["response"].startswith("Agent error:") and "reference ERR-" in data["response"]
    for leaked in ("gsk_", "litellm", "AuthenticationError", "Incorrect API key"):
        assert leaked not in data["response"]
    stored = chat_client.get(f"/chat/{cid}").json()["messages"][-1]["message"]
    assert "gsk_" not in stored and data["incident_id"] is None


# ── B04 ───────────────────────────────────────────────────────────────────────────────────────

def test_b04_new_incident_uses_ultra_tier_and_runner(chat_client, runners, monkeypatch):
    rca, _ = runners
    ultra = type(rca)([make_event(RCA_REPLY)])
    monkeypatch.setattr(router, "_tier_runners", {"ultra": ultra})
    monkeypatch.setattr("agent.tools.recall_past_incidents", lambda **kw: {"past_incidents": []})
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "why is checkout failing?").json()
    assert data["tier"] == "ultra" and data["model"] == config.NEMOTRON_ULTRA_MODEL
    assert len(ultra.calls) == 1 and rca.calls == []
    events = incidents.list_events(data["incident_id"])
    assert any(e["kind"] == "diagnosis:tier" and "ultra" in e["detail"] for e in events)


def test_b04_super_tier_uses_default_runner(chat_client, runners, monkeypatch):
    """A precedent whose context differs routes to super, which is the default `runner` when AGENT_MODEL=super."""
    rca, _ = runners
    rca.events = [make_event(RCA_REPLY)]
    monkeypatch.setattr(router, "select_rca_tier", lambda *a, **k: "super")
    monkeypatch.setattr("agent.tools.recall_past_incidents", lambda **kw: {"past_incidents": [{"incident_id": "INC-1"}]})
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "why is checkout failing?").json()
    assert data["tier"] == "super" and len(rca.calls) == 1


def test_b04_tier_is_sticky_for_a_chat(chat_client, runners, monkeypatch):
    rca, _ = runners
    rca.events = [make_event(RCA_REPLY)]
    seq = iter(["super", "nano"])
    monkeypatch.setattr(router, "select_rca_tier", lambda *a, **k: next(seq))
    monkeypatch.setattr("agent.tools.recall_past_incidents", lambda **kw: {"past_incidents": []})
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    cid = new_chat(chat_client)
    first = ask(chat_client, cid, "why is checkout failing?").json()
    second = ask(chat_client, cid, "why is it still failing?").json()
    assert first["tier"] == second["tier"] == "super"


def test_b04_explicit_model_override_serves_every_tier(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "groq/llama-3.3-70b-versatile")
    assert router._rca_runner_for("ultra") is router.runner
    assert router._rca_runner_for("nano") is router.runner


def test_b04_query_intent_has_no_tier(chat_client, runners):
    _, query = runners
    query.events = [make_event("here are the logs")]
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "show me the last 10 logs").json()
    assert data["tier"] is None and data["model"] is None


PAST = {"incident_id": "INC-0001", "summary": "POST /checkout 500", "root_cause_summary": "payment_failure fault active"}


def _seed_precedent(monkeypatch, evidence, rca_text, context=""):
    monkeypatch.setattr(router.incidents, "get", lambda iid: {
        "id": iid, "evidence": evidence, "rca_text": rca_text, "context_sample": context})


def test_b04_same_symptoms_pick_nano(monkeypatch):
    _seed_precedent(monkeypatch, ["POST /checkout 500"], "payment_failure fault declines POST /checkout with 500")
    monkeypatch.setattr(router, "_live_error_context", lambda svc: ("", ["POST /checkout -> 500 (9.9ms)"]))
    assert router.select_rca_tier("why is checkout failing?", [PAST], "checkout") == "nano"


def test_b04_different_symptoms_pick_super(monkeypatch):
    _seed_precedent(monkeypatch, ["POST /checkout 500"], "payment_failure fault declines POST /checkout with 500")
    monkeypatch.setattr(router, "_live_error_context", lambda svc: ("", ["GET /products -> 503 DatabaseConnectionTimeout"]))
    assert router.select_rca_tier("why is catalog slow?", [PAST], "catalog") == "super"


def test_b04_no_precedent_picks_ultra():
    assert router.select_rca_tier("why?", [], "checkout") == "ultra"


# ── B01 ───────────────────────────────────────────────────────────────────────────────────────

class _StatusMemory(FakeMemory):
    def __init__(self, status):
        super().__init__()
        self.status = status

    def operation_status(self, operation_id):
        return self.status


def _incident_with_op():
    inc = incidents.create("checkout", [{"fp": "fp-1", "kind": "chat_rca", "route": "chat", "sig": "s"}],
                           "critical", ["POST /checkout 500"], 1)
    incidents.update_fields(inc["id"], memory_status="retained", memory_op="op-1")
    return inc["id"]


@pytest.mark.parametrize("op_status,expected", [("completed", "indexed"), ("failed", "failed"),
                                                ("pending", "retained"), ("", "retained")])
def test_b01_retained_status_follows_hindsight_operation(monkeypatch, dash_client, op_status, expected):
    monkeypatch.setattr(cycle, "_memory", _StatusMemory(op_status))
    iid = _incident_with_op()
    assert dash_client.get(f"/api/incidents/{iid}").json()["memory_status"] == expected
    assert incidents.get(iid)["memory_status"] == expected


def test_b01_status_check_failure_does_not_break_the_dossier(monkeypatch, dash_client):
    class Boom(_StatusMemory):
        def operation_status(self, operation_id):
            raise ConnectionError("hindsight down")
    monkeypatch.setattr(cycle, "_memory", Boom(""))
    iid = _incident_with_op()
    r = dash_client.get(f"/api/incidents/{iid}")
    assert r.status_code == 200 and r.json()["memory_status"] == "retained"


def test_b01_feedback_message_does_not_claim_indexing(monkeypatch, dash_client):
    monkeypatch.setattr(cycle, "_memory", FakeMemory())
    iid = incidents.create("checkout", [{"fp": "fp-2", "kind": "chat_rca", "route": "chat", "sig": "s"}],
                           "critical", ["POST /checkout 500"], 1)["id"]
    body = dash_client.post(f"/api/incidents/{iid}/feedback", json={"action": "flush_pool", "worked": True}).json()
    assert "queued" in body["message"] and "retained in Hindsight" not in body["message"]


def test_b01_feedback_message_when_memory_is_off(dash_client):
    iid = incidents.create("checkout", [{"fp": "fp-3", "kind": "chat_rca", "route": "chat", "sig": "s"}],
                           "critical", ["POST /checkout 500"], 1)["id"]
    body = dash_client.post(f"/api/incidents/{iid}/feedback", json={"action": "flush_pool", "worked": True}).json()
    assert body["memory_retained"] is False and "Memory is off" in body["message"]


def test_b01_compose_caps_hindsight_retain_tokens():
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[3] / "docker-compose.yml").read_text(encoding="utf8")
    assert "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS" in text and "16384" in text


# ── chat must not hang (B21, added because it blocked B01-B05 verification) ───────────────────────────

def test_chat_times_out_instead_of_hanging(chat_client, runners, monkeypatch):
    import asyncio

    rca, _ = runners

    async def never(*a, **k):
        await asyncio.sleep(30)
        yield None
    rca.run_async = never
    monkeypatch.setattr(config, "AGENT_TIMEOUT_S", 0.2)
    monkeypatch.setattr("agent.tools.recall_past_incidents", lambda **kw: {"past_incidents": []})
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "why is checkout failing?").json()
    assert data["response"].startswith("Agent error:") and "reference ERR-" in data["response"]
    assert data["incident_id"] is None


def test_slow_memory_recall_does_not_block_the_chat(chat_client, runners, monkeypatch, caplog):
    """The recall gives up after CHAT_RECALL_TIMEOUT_S and the chat goes on without memory. (Wall time is not asserted:
    the test client waits for the sleeping worker thread when it closes its loop; uvicorn does not.)"""
    import time

    rca, _ = runners
    rca.events = [make_event(RCA_REPLY)]
    monkeypatch.setattr(config, "CHAT_RECALL_TIMEOUT_S", 0.2)
    monkeypatch.setattr("agent.tools.recall_past_incidents", lambda **kw: time.sleep(1.5) or {
        "past_incidents": [{"incident_id": "INC-9", "summary": "x"}]})
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "why is checkout failing?").json()
    assert data["incident_id"] and data["tier"] == "ultra"          # no precedent was used
    assert rca.calls[0]["text"] == "why is checkout failing?"        # no memory preamble
    assert "Pre-recall past incidents for chat failed" in caplog.text


def test_llm_calls_have_a_request_timeout(monkeypatch):
    """Without a timeout a stalled NVIDIA call blocks for LiteLLM's 10-minute default and never reaches the fallback."""
    monkeypatch.delenv("AGENT_LLM_TIMEOUT_S", raising=False)
    assert llm.litellm_kwargs()["timeout"] == 45
    monkeypatch.setenv("AGENT_LLM_TIMEOUT_S", "12.5")
    assert llm.litellm_kwargs()["timeout"] == 12.5
    monkeypatch.setenv("AGENT_LLM_TIMEOUT_S", "junk")
    assert llm.litellm_kwargs()["timeout"] == 45
