from types import SimpleNamespace as NS

import pytest

from agent.llm import build_model, litellm_kwargs, strip_thought_parts


def part(text=None, thought=False, call=None, response=None):
    return NS(text=text, thought=thought, function_call=call, function_response=response)


def content(role, *parts):
    return NS(role=role, parts=list(parts))


def req(*contents):
    return NS(contents=list(contents))


def test_thought_parts_are_removed_and_everything_else_is_kept():
    call = part(call={"name": "get_error_rate"})
    r = req(
        content("user", part("alert")),
        content("model", part("thinking...", thought=True), call),
        content("user", part(response={"total": 3})),
        content("model", part("final", thought=False)),
    )
    removed = strip_thought_parts(r)
    assert removed == 1
    assert [c.role for c in r.contents] == ["user", "model", "user", "model"]
    assert r.contents[1].parts == [call]                      # the tool call survives
    assert r.contents[2].parts[0].function_response == {"total": 3}
    assert r.contents[3].parts[0].text == "final"


def test_a_content_holding_only_thoughts_is_dropped():
    r = req(content("user", part("hi")), content("model", part("hmm", thought=True), part("more", thought=True)))
    assert strip_thought_parts(r) == 2
    assert [c.role for c in r.contents] == ["user"]


def test_empty_content_and_no_thoughts_are_left_alone():
    empty = content("user")
    plain = content("model", part("ok"))
    r = req(empty, plain)
    assert strip_thought_parts(r) == 0
    assert r.contents == [empty, plain]


def test_request_without_contents_is_fine():
    assert strip_thought_parts(NS(contents=None)) == 0
    assert strip_thought_parts(NS()) == 0


def test_plain_gemini_names_are_returned_as_strings(monkeypatch):
    assert build_model("gemini-3.5-flash") == "gemini-3.5-flash"
    monkeypatch.setenv("AGENT_MODEL", "  gemini-x  ")
    assert build_model() == "gemini-x"


def test_default_model_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert build_model() == "gemini-3.5-flash"


def test_litellm_defaults_keep_diagnoses_small_and_tolerate_unsupported_params(monkeypatch):
    for k in ("AGENT_REASONING_EFFORT", "AGENT_MAX_TOKENS", "AGENT_LLM_RETRIES"):
        monkeypatch.delenv(k, raising=False)
    kw = litellm_kwargs()
    assert kw["reasoning_effort"] == "low" and kw["max_tokens"] == 1200 and kw["drop_params"] is True
    assert kw["num_retries"] == 3


def test_litellm_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "HIGH")
    monkeypatch.setenv("AGENT_MAX_TOKENS", "500")
    assert litellm_kwargs()["reasoning_effort"] == "high" and litellm_kwargs()["max_tokens"] == 500


@pytest.mark.parametrize("effort", ["none", "", "  NONE "])
def test_reasoning_effort_can_be_left_to_the_provider(monkeypatch, effort):
    monkeypatch.setenv("AGENT_REASONING_EFFORT", effort)
    assert "reasoning_effort" not in litellm_kwargs()


def test_bad_or_zero_token_cap_is_safe(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_TOKENS", "abc")
    assert litellm_kwargs()["max_tokens"] == 1200
    monkeypatch.setenv("AGENT_MAX_TOKENS", "0")
    assert "max_tokens" not in litellm_kwargs()
