"""agent/llm.py: output sanitizer, model tier mapping, NVIDIA -> Groq fallback, LiteLLM kwargs."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from agent import config
from agent import llm
from agent.llm import (
    build_model,
    build_nemotron_model,
    clean_agent_output,
    get_groq_fallback_model,
    litellm_kwargs,
)


@pytest.fixture
def fake_cls(monkeypatch):
    cls = MagicMock(name="LiteLlmClass")
    monkeypatch.setattr(llm, "_provider_safe_litellm_class", lambda: cls)
    return cls


@pytest.fixture
def tiers(monkeypatch):
    monkeypatch.setattr(config, "NEMOTRON_NANO_MODEL", "nvidia/nano-x")
    monkeypatch.setattr(config, "NEMOTRON_SUPER_MODEL", "nvidia/super-x")
    monkeypatch.setattr(config, "NEMOTRON_ULTRA_MODEL", "nvidia/ultra-x")
    monkeypatch.setattr(config, "NVIDIA_BASE_URL", "https://nim.example/v1")


# ── clean_agent_output ───────────────────────────────────────────────────────────────────────

def test_clean_output_preserves_dotted_exception_names():
    """REQ: the sanitizer only strips reasoning blocks / thinking tags / monologue (clean_agent_output docstring); the RCA
    answer must quote actual exception types verbatim (SYSTEM_INSTRUCTION: 'Name the exception type', 'Do not paraphrase')."""
    text = "Root cause: psycopg2.errors.UndefinedTable raised by sqlalchemy.exc.ProgrammingError on POST /checkout."
    assert clean_agent_output(text) == text


def test_clean_output_preserves_mid_sentence_checking():
    """REQ: only tool narration / operational planning sentences are stripped (clean_agent_output step 3 comment);
    ordinary sentences that contain the word 'checking' are content, not narration."""
    text = "Health checking of the payment gateway shows 20/20 connections in use."
    assert clean_agent_output(text) == text


@pytest.mark.parametrize("tag", ["thought", "think", "reasoning", "THINK", "Reasoning"])
def test_clean_output_strips_reasoning_tags_any_case(tag):
    """REQ: strip explicit thought and reasoning XML-style tags (clean_agent_output step 1)."""
    raw = f"<{tag}>secret plan\nmore</{tag}>Checkout error rate is 30%."
    assert clean_agent_output(raw) == "Checkout error rate is 30%."


def test_clean_output_strips_multiple_thought_blocks():
    """REQ: every reasoning block is stripped (docstring)."""
    raw = "<think>a</think>Line one.\n\n<thought>b</thought>Line two."
    out = clean_agent_output(raw)
    assert "a" not in out.split() and "<" not in out and "Line one." in out and "Line two." in out


def test_clean_output_strips_unclosed_thought_tag():
    """REQ: unclosed thought tags (cut-off output) are stripped (clean_agent_output step 1 comment)."""
    assert clean_agent_output("Error rate 5%.\n\n<think>I should keep going and") == "Error rate 5%."


def test_clean_output_splits_glued_sentences():
    """REQ: 'observed.There' becomes 'observed.\\n\\nThere' (clean_agent_output step 2 comment)."""
    assert clean_agent_output("No errors observed.There is no outage.") == "No errors observed.\n\nThere is no outage."


@pytest.mark.parametrize("narration", [
    "Let me check the error rate. ",
    "Let me also query the logs. ",
    "I will now inspect the traces. ",
])
def test_clean_output_strips_tool_narration(narration):
    """REQ: tool narration sentences are removed (clean_agent_output step 3)."""
    out = clean_agent_output(narration + "Checkout p95 latency is 3.2s.")
    assert out == "Checkout p95 latency is 3.2s."


def test_clean_output_strips_monologue_preamble_only():
    """REQ: monologue/meta-commentary PREAMBLES are removed (clean_agent_output docstring and step 4); substantive
    paragraphs that come after the first real paragraph are kept."""
    raw = ("The user wants the root cause.\n\nI need to check memory first.\n\n"
           "**Root Cause**: pool exhaustion.\n\nI need to stress this is a hypothesis.")
    out = clean_agent_output(raw)
    assert out.startswith("**Root Cause**: pool exhaustion.")
    assert "The user wants" not in out
    assert out.endswith("I need to stress this is a hypothesis.")


def test_clean_output_keeps_proposed_action_line():
    """REQ: the final 'Proposed action: <name>' line must survive sanitising (SYSTEM_INSTRUCTION response format;
    router relies on parse_proposed_action)."""
    raw = "<think>hmm</think>**Root Cause**: bad release v2.4.0.\n\nProposed action: rollback_release"
    out = clean_agent_output(raw)
    assert out.endswith("Proposed action: rollback_release") and "v2.4.0" in out


def test_clean_output_unicode_preserved():
    """REQ: sanitizer must not damage non-ASCII content (docstring: strips only reasoning/monologue)."""
    text = "Le service « paiement » renvoie 503 — 日本語 🚨"
    assert clean_agent_output(text) == text


def test_clean_output_is_idempotent():
    """REQ: sanitised output is final user-facing text (docstring); sanitising it again must not change it."""
    raw = ("<think>x</think>The user is asking about errors.\n\n**Symptom**: 30% 5xx on POST /checkout.\n\n"
           "**Evidence**:\n- 503 pool exhausted\n\nProposed action: flush_pool")
    once = clean_agent_output(raw)
    assert clean_agent_output(once) == once


def test_clean_output_whitespace_only_is_empty():
    """REQ: empty/whitespace output sanitises to empty so the router can report 'Agent produced no output.'
    (router.ask_chat)."""
    assert clean_agent_output("   \n\n  ") == ""


def test_clean_output_very_long_text():
    """REQ: sanitizer handles very long replies without truncating substantive content (docstring)."""
    body = "Error rate on POST /checkout is 30%. " * 5000
    assert clean_agent_output(body).startswith("Error rate on POST /checkout is 30%.")
    assert len(clean_agent_output(body)) >= len(body.strip()) - 10


@pytest.mark.skip(reason="AMBIGUOUS: when EVERY paragraph is monologue the code returns the last (monologue) paragraph "
                         "instead of '' - docstring says monologue is stripped but returning '' would surface "
                         "'Agent produced no output.'. Needs a product decision.")
def test_clean_output_all_monologue():
    """REQ: unclear."""


# ── tier -> model name, NVIDIA routing, Groq fallback ────────────────────────────────────────

@pytest.mark.parametrize("tier,model", [("nano", "nvidia/nano-x"), ("super", "nvidia/super-x"), ("ultra", "nvidia/ultra-x"),
                                        ("  ULTRA ", "nvidia/ultra-x"), (None, "nvidia/super-x"), ("bogus", "nvidia/super-x")])
def test_llm_tier_maps_to_configured_model_via_nim(monkeypatch, fake_cls, tiers, tier, model):
    """REQ: each Nemotron tier is served via NVIDIA NIM with its configured model (build_nemotron_model docstring;
    readme §3 tier table; config NEMOTRON_*_MODEL). Unknown tier -> super."""
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nv-key")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_MODEL", "")
    build_nemotron_model(tier)
    kw = fake_cls.call_args.kwargs
    assert kw["model"] == f"openai/{model}"
    assert kw["api_key"] == "nv-key" and kw["api_base"] == "https://nim.example/v1"
    assert kw["fallback_model"] is None and "fallbacks" not in kw


@pytest.mark.parametrize("name", ["openai/already-prefixed", "nvidia_nim/meta/llama"])
def test_llm_nim_does_not_double_prefix(monkeypatch, fake_cls, tiers, name):
    """REQ: models already routed through an OpenAI-compatible / nvidia_nim provider are used as-is
    (build_nemotron_model)."""
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nv-key")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_MODEL", "")
    monkeypatch.setattr(config, "NEMOTRON_SUPER_MODEL", name)
    build_nemotron_model("super")
    assert fake_cls.call_args.kwargs["model"] == name


def test_llm_nim_with_groq_configures_fallback_instance(monkeypatch, fake_cls, tiers):
    """REQ: 'Falls back to Groq model if configured and NVIDIA NIM fails' (build_nemotron_model docstring)."""
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nv-key")
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk")
    monkeypatch.setattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile")
    build_nemotron_model("nano")
    calls = [c.kwargs for c in fake_cls.call_args_list]
    groq = [c for c in calls if c["model"] == "groq/llama-3.3-70b-versatile"]
    primary = [c for c in calls if c["model"] == "openai/nvidia/nano-x"]
    assert groq and groq[0]["api_key"] == "gsk" and groq[0]["fallback_model"] is None
    assert primary and primary[0]["fallback_model"] is not None
    assert "fallbacks" not in primary[0]  # B02: the explicit fallback_model instance is the fallback


def test_llm_no_nvidia_key_routes_directly_to_groq(monkeypatch, fake_cls, tiers):
    """REQ: 'Falls back to Groq ... if NVIDIA NIM ... is unconfigured' (build_nemotron_model docstring)."""
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk")
    monkeypatch.setattr(config, "GROQ_MODEL", "groq/llama-x")
    build_nemotron_model("ultra")
    kw = fake_cls.call_args.kwargs
    assert kw["model"] == "groq/llama-x" and kw["api_key"] == "gsk" and "api_base" not in kw


def test_llm_extra_kwargs_are_forwarded(monkeypatch, fake_cls, tiers):
    """REQ: build_nemotron_model(**extra_kwargs) forwards extra arguments to the model (signature)."""
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nv-key")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_MODEL", "")
    build_nemotron_model("super", temperature=0.1)
    assert fake_cls.call_args.kwargs["temperature"] == 0.1


def test_llm_no_keys_plain_model_name_returned_as_string(monkeypatch, fake_cls):
    """REQ: without provider routing a plain model name is returned as a string (build_nemotron_model comment)."""
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_MODEL", "")
    monkeypatch.setattr(config, "NEMOTRON_NANO_MODEL", "local-nano")
    assert build_nemotron_model("nano") == "local-nano"
    assert not fake_cls.called


@pytest.mark.parametrize("raw,expected", [
    ("llama-3.3-70b-versatile", "groq/llama-3.3-70b-versatile"),
    ("  llama-x  ", "groq/llama-x"),
    ("openai/gpt-oss-120b", "openai/gpt-oss-120b"),
    ("groq/qwen/qwen3", "groq/qwen/qwen3"),
    ("", "groq/llama-3.3-70b-versatile"),
    (None, "groq/llama-3.3-70b-versatile"),
])
def test_llm_groq_fallback_name_normalized(monkeypatch, raw, expected):
    """REQ: get_groq_fallback_model returns the normalized Groq identifier, defaulting to llama-3.3-70b-versatile
    (docstring; config GROQ_MODEL default)."""
    monkeypatch.setattr(config, "GROQ_MODEL", raw)
    assert get_groq_fallback_model() == expected


def test_llm_build_model_groq_name_has_no_self_fallback(monkeypatch, fake_cls):
    """REQ: a groq/ model is not given Groq as its own fallback (build_model)."""
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk")
    monkeypatch.setattr(config, "GROQ_MODEL", "groq/llama-x")
    build_model("groq/openai/gpt-oss-120b")
    kw = fake_cls.call_args.kwargs
    assert kw["model"] == "groq/openai/gpt-oss-120b" and kw["fallback_model"] is None and "fallbacks" not in kw


def test_llm_build_model_provider_string_gets_groq_fallback(monkeypatch, fake_cls):
    """REQ: provider/model strings are routed through LiteLLM (llm module docstring) with Groq fallback when configured."""
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk")
    monkeypatch.setattr(config, "GROQ_MODEL", "groq/llama-x")
    build_model("openai/gpt-4o")
    primary = fake_cls.call_args_list[-1].kwargs
    assert primary["model"] == "openai/gpt-4o" and primary["fallback_model"] is not None
    assert "fallbacks" not in primary  # B02: the explicit fallback_model instance is the fallback


@pytest.mark.parametrize("name,tier", [("Nano", "nano"), ("  ultra  ", "ultra"), ("SUPER", "super")])
def test_llm_build_model_tier_names_case_and_space_insensitive(monkeypatch, name, tier):
    """REQ: AGENT_MODEL may name a Nemotron tier (nano/super/ultra) (build_model; .env AGENT_MODEL=super)."""
    seen = []
    monkeypatch.setattr(llm, "build_nemotron_model", lambda t: seen.append(t) or "m")
    build_model(name)
    assert seen == [tier]


def test_llm_build_model_env_default(monkeypatch):
    """REQ: build_model() with no name reads AGENT_MODEL from the environment (llm module docstring)."""
    monkeypatch.setenv("AGENT_MODEL", "gemini-2.0-flash")
    assert build_model() == "gemini-2.0-flash"


# ── litellm_kwargs ───────────────────────────────────────────────────────────────────────────

def test_llm_kwargs_retries_from_env(monkeypatch):
    """REQ: num_retries comes from AGENT_LLM_RETRIES (litellm_kwargs docstring)."""
    monkeypatch.setenv("AGENT_LLM_RETRIES", "5")
    assert litellm_kwargs()["num_retries"] == 5


def test_llm_kwargs_negative_token_cap_omitted(monkeypatch):
    """REQ: max_tokens caps each reply; a non-positive cap means no cap (litellm_kwargs)."""
    monkeypatch.setenv("AGENT_MAX_TOKENS", "-5")
    assert "max_tokens" not in litellm_kwargs()


def test_llm_kwargs_never_include_api_keys(monkeypatch):
    """REQ: litellm_kwargs only carries call tuning (retries, effort, tokens, drop_params, timeout) (docstring)."""
    assert set(litellm_kwargs()) <= {"num_retries", "drop_params", "reasoning_effort", "max_tokens", "timeout"}


@pytest.mark.skip(reason="AMBIGUOUS: AGENT_LLM_RETRIES='abc' raises ValueError (AGENT_MAX_TOKENS is guarded, retries is "
                         "not). No doc says what a bad value should do.")
def test_llm_kwargs_bad_retries():
    """REQ: unclear."""


# ── ProviderSafeLiteLlm runtime fallback ─────────────────────────────────────────────────────

def _collect(agen):
    async def run():
        return [r async for r in agen]
    return asyncio.run(run())


class _FB:
    model = "groq/llama-x"

    def __init__(self):
        self.requests = []

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request)
        yield "from-fallback"


@pytest.fixture
def failing_primary(monkeypatch):
    from google.adk.models.lite_llm import LiteLlm

    async def boom(self, llm_request, stream=False):
        raise ConnectionError("nim down")
        yield  # pragma: no cover

    monkeypatch.setattr(LiteLlm, "generate_content_async", boom)
    llm._CLASS_CACHE.pop("cls", None)
    yield llm._provider_safe_litellm_class()
    llm._CLASS_CACHE.pop("cls", None)


def test_llm_runtime_failure_uses_groq_fallback(failing_primary):
    """REQ: when the primary NVIDIA model fails at call time the Groq fallback answers (build_nemotron_model docstring)."""
    fb = _FB()
    m = failing_primary(model="openai/nvidia/super-x", fallback_model=fb)
    assert _collect(m.generate_content_async(NS(contents=[]))) == ["from-fallback"]
    assert len(fb.requests) == 1


def test_llm_runtime_failure_without_fallback_raises(failing_primary):
    """REQ: with no fallback configured the provider error surfaces (ProviderSafeLiteLlm: re-raise)."""
    m = failing_primary(model="openai/nvidia/super-x", fallback_model=None)
    with pytest.raises(ConnectionError):
        _collect(m.generate_content_async(NS(contents=[])))


def test_llm_runtime_fallback_receives_request_without_thoughts(failing_primary):
    """REQ: thought parts are stripped before any provider sees the request (llm module docstring: Groq rejects
    reasoning_content)."""
    fb = _FB()
    req = NS(contents=[NS(role="model", parts=[NS(text="t", thought=True), NS(text="answer", thought=False)])])
    m = failing_primary(model="openai/x", fallback_model=fb)
    _collect(m.generate_content_async(req))
    sent = fb.requests[0]
    assert [p.text for c in sent.contents for p in c.parts] == ["answer"]
