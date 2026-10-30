"""Model selection for the ADK agent.

``AGENT_MODEL`` is either a plain Gemini model name (handled natively by ADK, e.g. ``gemini-3.5-flash``) or a
LiteLLM-style ``provider/model`` string, which is routed through LiteLLM (e.g. ``groq/openai/gpt-oss-120b``).
Provider API keys are read by LiteLLM from the environment (``GROQ_API_KEY`` for ``groq/...``).

Reasoning models (such as gpt-oss) return their chain of thought as ``reasoning_content``. ADK stores it as thought
parts and sends it back in the conversation history on the next turn, which Groq rejects with
``'reasoning_content' is unsupported`` (found by the O2 gate). ``strip_thought_parts`` removes those parts from the
request first; the model still sees every message, tool call and tool result.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from agent import config

log = logging.getLogger("copilot.llm")

_CLASS_CACHE: dict[str, Any] = {}



_SECRET_PATTERNS = (
    re.compile(r"\b(?:gsk|nvapi|sk|xai|hf)[-_][A-Za-z0-9*._-]{4,}"),
    re.compile(r"(?i)\b(?:bearer|api[_-]?key|authorization)\b\s*[:=]?\s*[A-Za-z0-9*._-]{8,}"),
)


def scrub_secrets(text: str) -> str:
    """Mask API-key-looking tokens (also partially masked ones) before text is logged or shown."""
    out = text or ""
    for pat in _SECRET_PATTERNS:
        out = pat.sub("<redacted>", out)
    return out


def clean_agent_output(text: str) -> str:
    """Sanitize agent output by stripping reasoning blocks, thinking tags, and monologue preambles."""
    if not text:
        return ""

    cleaned = text

    # 1. Strip explicit thought and reasoning XML-style tags
    cleaned = re.sub(r"<(thought|think|reasoning)>.*?</\1>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    # Strip unclosed thought tags if output was cut off
    cleaned = re.sub(r"<(thought|think|reasoning)>.*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    # 1b. A diagnosis starts at its "Symptom" heading: anything the model wrote before it is scratch reasoning
    heading = re.search(r"(?im)^[ \t]*(?:#+\s*)?(?:\*\*)?(?:\d+\.\s*)?symptom\b", cleaned)
    if heading and heading.start() > 0 and cleaned[:heading.start()].strip():
        cleaned = cleaned[heading.start():]

    # 2. Fix glued sentences where sentence end is immediately followed by capital letter (e.g. "observed.There" -> "observed.\n\nThere"),
    #    but never inside dotted identifiers such as psycopg2.errors.UndefinedTable or sqlalchemy.exc.ProgrammingError
    def _split_glued(m: "re.Match[str]") -> str:
        before, after = m.group(1), m.group(2)
        if "." in before or re.search(r"[A-Z]", after[1:]):
            return m.group(0)
        return f"{before}.\n\n{after}"

    cleaned = re.sub(r"([A-Za-z0-9_.\)\]]*[a-z0-9\)\]])\.([A-Z][A-Za-z0-9_]*)", _split_glued, cleaned)

    # 3. Strip sentences that are tool narration or operational planning
    # (anchored to the start of a line: "Health checking of the gateway ..." is content, not narration)
    narration_patterns = [
        r"^[ \t]*Let me (also )?(check|query|inspect|run|look|examine)[^\.\n]*[\.\n]?",
        r"^[ \t]*Checking [^\.\n]*[\.\n]?",
        r"^[ \t]*I will (now )?(check|query|inspect|run|look|examine)[^\.\n]*[\.\n]?",
    ]
    for pat in narration_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE | re.MULTILINE)

    # 4. Split into paragraphs and remove monologue / meta-commentary preambles
    monologue_starters = (
        "the user is asking",
        "the user wants",
        "the user is inquiring",
        "i need to",
        "i have tried",
        "i've tried",
        "i should report",
        "i will report",
        "i have checked",
        "this indicates that there's no telemetry data currently available",
        "this indicates that there's no telemetry data",
    )

    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    substantive_paragraphs = []

    found_first_substantive = False
    for p in paragraphs:
        p_lower = p.lower()
        if not found_first_substantive:
            is_monologue = any(p_lower.startswith(prefix) for prefix in monologue_starters)
            if not is_monologue and "i should report what i found honestly" in p_lower:
                is_monologue = True

            if is_monologue:
                continue

            found_first_substantive = True
            substantive_paragraphs.append(p)
        else:
            substantive_paragraphs.append(p)

    if substantive_paragraphs:
        cleaned = "\n\n".join(substantive_paragraphs)
    else:
        cleaned = paragraphs[-1] if paragraphs else ""

    return cleaned.strip()


def strip_thought_parts(llm_request: Any) -> int:
    """Remove thought parts from ``llm_request.contents`` in place. Returns how many parts were removed.

    A content that held only thoughts is dropped. Contents that hold a tool call, a tool result or text are kept
    untouched apart from their thought parts.
    """
    removed = 0
    kept = []
    for content in getattr(llm_request, "contents", None) or []:
        parts = list(getattr(content, "parts", None) or [])
        remaining = [p for p in parts if not getattr(p, "thought", False)]
        removed += len(parts) - len(remaining)
        if remaining or not parts:
            content.parts = remaining
            kept.append(content)
    llm_request.contents = kept
    return removed


def _provider_safe_litellm_class() -> Any:
    if "cls" not in _CLASS_CACHE:
        try:
            # Imported lazily so the Gemini-only path (and the unit tests) do not need litellm.
            from google.adk.models.lite_llm import LiteLlm

            class ProviderSafeLiteLlm(LiteLlm):
                def __init__(self, model: str, fallback_model: Any = None, **kwargs: Any) -> None:
                    super().__init__(model=model, **kwargs)
                    self._fallback_model = fallback_model

                async def generate_content_async(self, llm_request, stream: bool = False):
                    strip_thought_parts(llm_request)
                    try:
                        async for response in super().generate_content_async(llm_request, stream=stream):
                            yield response
                    except Exception as exc:
                        if self._fallback_model is not None:
                            fallback_name = getattr(self._fallback_model, "model", str(self._fallback_model))
                            log.warning("Primary model '%s' failed (%s). Falling back to Groq model '%s'.",
                                        self.model, exc, fallback_name)
                            # ADK prefers llm_request.model over the instance's own model, so without this the Groq
                            # call would carry the primary's model name (and hit the wrong endpoint with the Groq key).
                            fallback_request = llm_request.model_copy(update={"model": fallback_name})                                 if hasattr(llm_request, "model_copy") else llm_request
                            async for response in self._fallback_model.generate_content_async(fallback_request, stream=stream):
                                yield response
                        else:
                            raise

            _CLASS_CACHE["cls"] = ProviderSafeLiteLlm
        except ImportError:
            class DummyLiteLlm:
                def __init__(self, *args, fallback_model: Any = None, **kwargs):
                    self.args = args
                    self.kwargs = kwargs
                    self.fallback_model = fallback_model
                    self.model = kwargs.get("model", args[0] if args else "")

                async def generate_content_async(self, llm_request, stream: bool = False):
                    strip_thought_parts(llm_request)
                    if self.kwargs.get("_simulate_failure"):
                        if self.fallback_model is not None:
                            fallback_name = getattr(self.fallback_model, "model", str(self.fallback_model))
                            log.warning("Dummy primary model '%s' failed. Falling back to '%s'.", self.model, fallback_name)
                            async for response in self.fallback_model.generate_content_async(llm_request, stream=stream):
                                yield response
                            return
                        raise ConnectionError(f"Model {self.model} failed and no fallback configured.")
                    yield "dummy-response"

            _CLASS_CACHE["cls"] = DummyLiteLlm
    return _CLASS_CACHE["cls"]


def litellm_kwargs() -> dict:
    """Extra arguments for LiteLLM calls, all from the environment.

    * ``num_retries``: short in-call retries for transient errors (long waits are handled by the diagnosis queue).
    * ``reasoning_effort``: reasoning models (gpt-oss) spend most tokens thinking; ``low`` cuts a diagnosis to a
      fraction of the tokens, which matters on per-minute token quotas. ``none`` leaves the provider default.
    * ``max_tokens``: caps each reply (and what the provider reserves against the quota).
    * ``drop_params``: a model that does not support a parameter ignores it instead of failing.
    * ``timeout``: seconds one provider call may take before it counts as failed (``AGENT_LLM_TIMEOUT_S``, default 45).
    """
    kwargs: dict = {"num_retries": int(os.getenv("AGENT_LLM_RETRIES", "3")), "drop_params": True}
    # A stalled provider call must fail (and fall back to Groq) well inside AGENT_TIMEOUT_S; LiteLLM's own default is 10 min.
    try:
        timeout = float(os.getenv("AGENT_LLM_TIMEOUT_S", "45"))
    except ValueError:
        timeout = 45.0
    if timeout > 0:
        kwargs["timeout"] = timeout
    effort = (os.getenv("AGENT_REASONING_EFFORT", "low") or "").strip().lower()
    if effort and effort != "none":
        kwargs["reasoning_effort"] = effort
    try:
        max_tokens = int(os.getenv("AGENT_MAX_TOKENS", "1200"))
    except ValueError:
        max_tokens = 1200
    if max_tokens > 0:
        kwargs["max_tokens"] = max_tokens
    return kwargs


def get_groq_fallback_model() -> str:
    """Return the normalized Groq model identifier."""
    model = (getattr(config, "GROQ_MODEL", "") or "groq/llama-3.3-70b-versatile").strip()
    if not model.startswith("groq/") and not model.startswith("openai/"):
        return f"groq/{model}"
    return model


def build_groq_fallback_model(**extra_kwargs) -> Any:
    """Build a LiteLLM model instance configured for the Groq fallback."""
    groq_model = get_groq_fallback_model()
    kwargs = litellm_kwargs()
    if getattr(config, "GROQ_API_KEY", ""):
        kwargs["api_key"] = config.GROQ_API_KEY
    kwargs.update(extra_kwargs)
    cls = _provider_safe_litellm_class()
    return cls(model=groq_model, fallback_model=None, **kwargs)


_PROVIDERS = ("nebius", "nvidia")


def _tier_model(tier: str, provider: str) -> str:
    """The model id configured for a tier on one provider ('' when that provider does not serve the tier)."""
    t = (tier or "super").strip().lower()
    if provider == "nebius":
        return {"nano": config.NEBIUS_NANO_MODEL, "ultra": config.NEBIUS_ULTRA_MODEL}.get(t, config.NEBIUS_SUPER_MODEL)
    return {"nano": config.NEMOTRON_NANO_MODEL, "ultra": config.NEMOTRON_ULTRA_MODEL}.get(t, config.NEMOTRON_SUPER_MODEL)


def _provider_ready(provider: str, tier: str) -> bool:
    key = config.NEBIUS_API_KEY if provider == "nebius" else config.NVIDIA_API_KEY
    return bool(key) and bool(_tier_model(tier, provider))


def provider_chain(tier: str = "super") -> list[str]:
    """Providers to try for a tier, in order: INFERENCE_PROVIDER first, then the other, then 'groq'.

    Each link is optional: a provider is included only when its API key is set (and, for Nebius, a model id exists for the
    tier), so the system runs with just NEBIUS_API_KEY.
    """
    first = config.INFERENCE_PROVIDER if config.INFERENCE_PROVIDER in _PROVIDERS else "nebius"
    order = [first] + [p for p in _PROVIDERS if p != first]
    chain = [p for p in order if _provider_ready(p, tier)]
    if getattr(config, "GROQ_API_KEY", ""):
        chain.append("groq")
    return chain


def get_nemotron_model_name(tier: str = "super") -> str:
    """Return the model identifier the primary provider uses for the tier (the NVIDIA ids when no provider is ready)."""
    for provider in provider_chain(tier):
        if provider in _PROVIDERS:
            return _tier_model(tier, provider)
    return _tier_model(tier, "nvidia")


def _link_kwargs(provider: str, tier: str) -> tuple[str, dict]:
    """(LiteLLM model string, connection kwargs) for one provider's tier model."""
    model_name = _tier_model(tier, provider)
    if provider == "nebius":
        conn = {"api_key": config.NEBIUS_API_KEY, "api_base": config.NEBIUS_BASE_URL}
    else:
        conn = {"api_key": config.NVIDIA_API_KEY, "api_base": config.NVIDIA_BASE_URL}
    target = model_name if model_name.startswith(("nvidia_nim/", "openai/")) else f"openai/{model_name}"
    return target, conn


def build_nemotron_model(tier: str = "super", **extra_kwargs) -> Any:
    """Build an ADK-compatible model instance for a Nemotron tier (nano, super, ultra).

    The instance is the head of a fallback chain (see provider_chain): Nebius Token Factory, then NVIDIA NIM, then Groq,
    each only when configured. ProviderSafeLiteLlm hands a failed request to the next link, and a link's own fallback
    continues the chain. LiteLLM's `fallbacks=` kwarg is NOT used: with this entry shape it re-calls the primary model, so
    the fallback never reached Groq (bug B02).
    """
    chain = provider_chain(tier)
    if not chain:
        # No provider configured (unit tests, or a placeholder run): keep the bare name so nothing tries to connect.
        return get_nemotron_model_name(tier)

    fallback_inst = None
    for provider in reversed(chain):
        if provider == "groq":
            fallback_inst = build_groq_fallback_model(**extra_kwargs)
            continue
        target, conn = _link_kwargs(provider, tier)
        kwargs = {**litellm_kwargs(), **conn, **extra_kwargs}
        log.info("LLM tier=%s provider=%s model=%s base=%s%s", tier, provider, target, conn["api_base"],
                 f" fallback={getattr(fallback_inst, 'model', None)}" if fallback_inst is not None else "")
        fallback_inst = _provider_safe_litellm_class()(model=target, fallback_model=fallback_inst, **kwargs)
    return fallback_inst


def build_nano_model(**extra_kwargs) -> Any:
    """Convenience helper for Nemotron Nano tier."""
    return build_nemotron_model("nano", **extra_kwargs)


def build_super_model(**extra_kwargs) -> Any:
    """Convenience helper for Nemotron Super tier."""
    return build_nemotron_model("super", **extra_kwargs)


def build_ultra_model(**extra_kwargs) -> Any:
    """Convenience helper for Nemotron Ultra tier."""
    return build_nemotron_model("ultra", **extra_kwargs)


def build_model(name: str | None = None) -> Any:
    raw = (name or os.getenv("AGENT_MODEL") or "super").strip()
    if raw.lower() in ("nano", "super", "ultra"):
        return build_nemotron_model(raw.lower())
    if "/" in raw:
        kwargs = litellm_kwargs()
        fallback_inst = None
        if getattr(config, "GROQ_API_KEY", "") and not raw.startswith("groq/"):
            fallback_inst = build_groq_fallback_model()
        return _provider_safe_litellm_class()(model=raw, fallback_model=fallback_inst, **kwargs)
    return raw

