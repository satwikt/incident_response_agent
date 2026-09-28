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

import os
from typing import Any

_CLASS_CACHE: dict[str, Any] = {}


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
        # Imported lazily so the Gemini-only path (and the unit tests) do not need litellm.
        from google.adk.models.lite_llm import LiteLlm

        class ProviderSafeLiteLlm(LiteLlm):
            async def generate_content_async(self, llm_request, stream: bool = False):
                strip_thought_parts(llm_request)
                async for response in super().generate_content_async(llm_request, stream=stream):
                    yield response

        _CLASS_CACHE["cls"] = ProviderSafeLiteLlm
    return _CLASS_CACHE["cls"]


def litellm_kwargs() -> dict:
    """Extra arguments for LiteLLM calls, all from the environment.

    * ``num_retries``: short in-call retries for transient errors (long waits are handled by the diagnosis queue).
    * ``reasoning_effort``: reasoning models (gpt-oss) spend most tokens thinking; ``low`` cuts a diagnosis to a
      fraction of the tokens, which matters on per-minute token quotas. ``none`` leaves the provider default.
    * ``max_tokens``: caps each reply (and what the provider reserves against the quota).
    * ``drop_params``: a model that does not support a parameter ignores it instead of failing.
    """
    kwargs: dict = {"num_retries": int(os.getenv("AGENT_LLM_RETRIES", "3")), "drop_params": True}
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


def build_model(name: str | None = None) -> Any:
    name = (name or os.getenv("AGENT_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-3.5-flash").strip()
    if "/" in name:
        return _provider_safe_litellm_class()(model=name, **litellm_kwargs())
    return name
