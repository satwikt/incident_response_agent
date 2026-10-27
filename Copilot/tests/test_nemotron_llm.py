from unittest.mock import AsyncMock, MagicMock
import pytest

from agent import config
from agent.llm import (
    build_groq_fallback_model,
    build_model,
    build_nano_model,
    build_nemotron_model,
    build_super_model,
    build_ultra_model,
    get_groq_fallback_model,
    get_nemotron_model_name,
)


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch):
    """Each test starts with no provider configured, whatever the developer's environment holds."""
    for name in ("NEBIUS_API_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setattr(config, name, "")
    monkeypatch.setattr(config, "INFERENCE_PROVIDER", "nebius")
    for name in ("NEBIUS_NANO_MODEL", "NEBIUS_SUPER_MODEL", "NEBIUS_ULTRA_MODEL"):
        monkeypatch.setattr(config, name, "")


def test_nemotron_tier_model_names(monkeypatch):
    monkeypatch.setattr(config, "NEMOTRON_NANO_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
    monkeypatch.setattr(config, "NEMOTRON_SUPER_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    monkeypatch.setattr(config, "NEMOTRON_ULTRA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

    assert get_nemotron_model_name("nano") == "nvidia/nemotron-3-nano-30b-a3b"
    assert get_nemotron_model_name("super") == "nvidia/nemotron-3-super-120b-a12b"
    assert get_nemotron_model_name("ultra") == "nvidia/nemotron-3-ultra-550b-a55b"
    # Case insensitivity
    assert get_nemotron_model_name("NANO") == "nvidia/nemotron-3-nano-30b-a3b"
    # Default to super
    assert get_nemotron_model_name("unknown") == "nvidia/nemotron-3-super-120b-a12b"


def test_nemotron_tier_model_names_env_override(monkeypatch):
    monkeypatch.setattr(config, "NEMOTRON_NANO_MODEL", "custom/nano-model")
    assert get_nemotron_model_name("nano") == "custom/nano-model"


def test_build_nemotron_model_parameters(monkeypatch):
    mock_litellm_cls = MagicMock()
    monkeypatch.setattr("agent.llm._provider_safe_litellm_class", lambda: mock_litellm_cls)

    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nvapi-testkey-1234")
    monkeypatch.setattr(config, "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setattr(config, "NEMOTRON_SUPER_MODEL", "nvidia/nemotron-3-super-120b-a12b")

    build_nemotron_model("super")

    assert mock_litellm_cls.called
    call_kwargs = mock_litellm_cls.call_args[1]
    assert call_kwargs["model"] == "openai/nvidia/nemotron-3-super-120b-a12b"
    assert call_kwargs["api_key"] == "nvapi-testkey-1234"
    assert call_kwargs["api_base"] == "https://integrate.api.nvidia.com/v1"
    assert call_kwargs["drop_params"] is True


def test_build_model_routes_nemotron_tiers(monkeypatch):
    mock_build_nemotron = MagicMock(return_value="mock-nemotron-instance")
    monkeypatch.setattr("agent.llm.build_nemotron_model", mock_build_nemotron)

    res_nano = build_model("nano")
    mock_build_nemotron.assert_called_with("nano")
    assert res_nano == "mock-nemotron-instance"

    res_super = build_model("SUPER")
    mock_build_nemotron.assert_called_with("super")
    assert res_super == "mock-nemotron-instance"

    res_ultra = build_model("ultra")
    mock_build_nemotron.assert_called_with("ultra")
    assert res_ultra == "mock-nemotron-instance"


def test_convenience_tier_helpers(monkeypatch):
    mock_build_nemotron = MagicMock(return_value="mocked")
    monkeypatch.setattr("agent.llm.build_nemotron_model", mock_build_nemotron)

    build_nano_model(extra="1")
    mock_build_nemotron.assert_called_with("nano", extra="1")

    build_super_model(extra="2")
    mock_build_nemotron.assert_called_with("super", extra="2")

    build_ultra_model(extra="3")
    mock_build_nemotron.assert_called_with("ultra", extra="3")


def test_groq_fallback_model_normalization(monkeypatch):
    monkeypatch.setattr(config, "GROQ_MODEL", "llama-3.3-70b-versatile")
    assert get_groq_fallback_model() == "groq/llama-3.3-70b-versatile"

    monkeypatch.setattr(config, "GROQ_MODEL", "groq/llama-3.1-8b-instant")
    assert get_groq_fallback_model() == "groq/llama-3.1-8b-instant"


def test_build_nemotron_model_routes_to_groq_when_nvidia_key_absent(monkeypatch):
    mock_litellm_cls = MagicMock()
    monkeypatch.setattr("agent.llm._provider_safe_litellm_class", lambda: mock_litellm_cls)
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk-testkey-1234")
    monkeypatch.setattr(config, "GROQ_MODEL", "groq/llama-3.3-70b-versatile")

    build_nemotron_model("super")

    assert mock_litellm_cls.called
    call_kwargs = mock_litellm_cls.call_args[1]
    assert call_kwargs["model"] == "groq/llama-3.3-70b-versatile"
    assert call_kwargs["api_key"] == "gsk-testkey-1234"


def test_build_nemotron_model_sets_groq_fallback_when_nvidia_key_present(monkeypatch):
    mock_litellm_cls = MagicMock()
    monkeypatch.setattr("agent.llm._provider_safe_litellm_class", lambda: mock_litellm_cls)
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nvapi-testkey-1234")
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk-testkey-1234")
    monkeypatch.setattr(config, "GROQ_MODEL", "groq/llama-3.3-70b-versatile")

    build_nemotron_model("super")

    assert mock_litellm_cls.called
    call_kwargs = mock_litellm_cls.call_args[1]
    assert call_kwargs["model"] == "openai/nvidia/nemotron-3.5-super-vl-120b-a12b"
    assert call_kwargs["api_key"] == "nvapi-testkey-1234"
    # B02: LiteLLM's `fallbacks=` kwarg re-called the primary instead of Groq; the explicit fallback_model is used.
    assert "fallbacks" not in call_kwargs
    groq_call = [c[1] for c in mock_litellm_cls.call_args_list if c[1]["model"] == "groq/llama-3.3-70b-versatile"]
    assert groq_call and groq_call[0]["api_key"] == "gsk-testkey-1234"
    assert "fallback_model" in call_kwargs
    assert call_kwargs["fallback_model"] is not None


@pytest.mark.asyncio
async def test_provider_safe_litellm_failover():
    from agent.llm import _provider_safe_litellm_class

    cls = _provider_safe_litellm_class()

    # Create a mock fallback model that yields a success response
    mock_fallback = MagicMock()
    async def mock_fallback_gen(req, stream=False):
        yield "fallback-success-response"
    mock_fallback.generate_content_async = mock_fallback_gen
    mock_fallback.model = "groq/llama-3.3-70b-versatile"

    instance = cls(model="openai/primary-model", fallback_model=mock_fallback, _simulate_failure=True)

    results = []
    async for resp in instance.generate_content_async(MagicMock()):
        results.append(resp)

    assert results == ["fallback-success-response"]


# ── provider selection and fallback chain (Nebius Token Factory / NVIDIA NIM / Groq) ─────────────

class _Rec:
    """Stands in for ProviderSafeLiteLlm and records how each link of the chain was built."""
    def __init__(self, model, fallback_model=None, **kwargs):
        self.model, self.fallback_model, self.kwargs = model, fallback_model, kwargs


def _chain(model):
    out = []
    while model is not None:
        out.append(model)
        model = model.fallback_model
    return out


@pytest.fixture
def rec(monkeypatch):
    monkeypatch.setattr("agent.llm._provider_safe_litellm_class", lambda: _Rec)
    monkeypatch.setattr(config, "NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
    monkeypatch.setattr(config, "NEBIUS_NANO_MODEL", "nvidia/nano-on-nebius")
    monkeypatch.setattr(config, "NEBIUS_SUPER_MODEL", "nvidia/super-on-nebius")
    monkeypatch.setattr(config, "NEBIUS_ULTRA_MODEL", "nvidia/ultra-on-nebius")
    monkeypatch.setattr(config, "NEMOTRON_SUPER_MODEL", "nvidia/super-on-nim")
    monkeypatch.setattr(config, "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    return _Rec


def test_only_nebius_key_builds_a_single_token_factory_link(monkeypatch, rec):
    monkeypatch.setattr(config, "NEBIUS_API_KEY", "nb-test-key")
    head = build_nemotron_model("ultra")
    assert _chain(head) == [head]                         # no NVIDIA or Groq link without their keys
    assert head.model == "openai/nvidia/ultra-on-nebius"
    assert head.kwargs["api_base"] == "https://api.tokenfactory.nebius.com/v1"
    assert head.kwargs["api_key"] == "nb-test-key"
    assert head.kwargs["drop_params"] is True


def test_each_tier_uses_its_own_nebius_model(monkeypatch, rec):
    monkeypatch.setattr(config, "NEBIUS_API_KEY", "nb-test-key")
    assert build_nemotron_model("nano").model == "openai/nvidia/nano-on-nebius"
    assert build_nemotron_model("super").model == "openai/nvidia/super-on-nebius"
    assert build_nemotron_model("ultra").model == "openai/nvidia/ultra-on-nebius"
    assert get_nemotron_model_name("nano") == "nvidia/nano-on-nebius"


def test_fallback_order_is_nebius_then_nvidia_then_groq(monkeypatch, rec):
    monkeypatch.setattr(config, "NEBIUS_API_KEY", "nb-test-key")
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr(config, "GROQ_MODEL", "groq/llama-3.3-70b-versatile")
    models = [m.model for m in _chain(build_nemotron_model("super"))]
    assert models == ["openai/nvidia/super-on-nebius", "openai/nvidia/super-on-nim", "groq/llama-3.3-70b-versatile"]
    head = build_nemotron_model("super")
    assert head.kwargs["api_base"] == "https://api.tokenfactory.nebius.com/v1"
    assert head.fallback_model.kwargs["api_base"] == "https://integrate.api.nvidia.com/v1"


def test_inference_provider_nvidia_puts_nim_first(monkeypatch, rec):
    monkeypatch.setattr(config, "INFERENCE_PROVIDER", "nvidia")
    monkeypatch.setattr(config, "NEBIUS_API_KEY", "nb-test-key")
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nvapi-test")
    assert [m.model for m in _chain(build_nemotron_model("super"))] == [
        "openai/nvidia/super-on-nim", "openai/nvidia/super-on-nebius"]
    assert get_nemotron_model_name("super") == "nvidia/super-on-nim"


def test_nebius_selected_but_key_missing_falls_back_to_nvidia(monkeypatch, rec):
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nvapi-test")
    head = build_nemotron_model("super")
    assert head.model == "openai/nvidia/super-on-nim" and head.kwargs["api_key"] == "nvapi-test"


def test_tier_without_a_nebius_model_skips_nebius_for_that_tier(monkeypatch, rec):
    monkeypatch.setattr(config, "NEBIUS_API_KEY", "nb-test-key")
    monkeypatch.setattr(config, "NEBIUS_NANO_MODEL", "")
    monkeypatch.setattr(config, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(config, "NEMOTRON_NANO_MODEL", "nvidia/nano-on-nim")
    assert build_nemotron_model("nano").model == "openai/nvidia/nano-on-nim"
    assert build_nemotron_model("super").model == "openai/nvidia/super-on-nebius"


def test_groq_is_a_link_only_when_its_key_is_set(monkeypatch, rec):
    monkeypatch.setattr(config, "NEBIUS_API_KEY", "nb-test-key")
    monkeypatch.setattr(config, "GROQ_MODEL", "groq/llama-3.3-70b-versatile")      # a default model alone is not a key
    assert len(_chain(build_nemotron_model("super"))) == 1
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk-test")
    assert [m.model for m in _chain(build_nemotron_model("super"))][-1] == "groq/llama-3.3-70b-versatile"


def test_no_provider_configured_returns_the_bare_model_name(rec):
    assert build_nemotron_model("super") == "nvidia/super-on-nim"
