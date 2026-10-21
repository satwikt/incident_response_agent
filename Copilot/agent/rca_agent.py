"""Reactive RCA Agent: performs root cause analysis on incidents with dynamic Nemotron tier routing.

Decision routing based on Hindsight memory:
- Completely new incident (no recalled past incidents):
    -> Nemotron Ultra ('ultra'): deep from-scratch reasoning across telemetry.
- Recalled past incident with differing runtime context (e.g. release / config change):
    -> Nemotron Super ('super'): mid-level reasoning guided by historical hypothesis.
- Recalled past incident with identical runtime context:
    -> Nemotron Nano ('nano'): lightweight fast reasoning to adapt known fix.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

try:
    from google.adk.agents.llm_agent import Agent
except ImportError:
    class Agent:  # type: ignore
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

from agent import config
from agent.agent import SYSTEM_INSTRUCTION
from agent.laya_classifier import get_laya_classifier
from agent.llm import build_model
from agent.tools import ALL_TOOLS

log = logging.getLogger("copilot.rca_agent")


def select_model_tier(incident: dict, recalled_matches: Optional[List[dict]] = None) -> str:
    """Select appropriate Nemotron tier (ultra, super, nano) for the incident."""
    matches = recalled_matches or incident.get("recalled") or []
    if not matches:
        log.info("No prior incident recalled from memory. Selecting Nemotron Ultra.")
        return "ultra"

    top_match = matches[0] if isinstance(matches[0], dict) else {}
    current_context = incident.get("context_sample") or incident.get("context") or ""
    past_context = top_match.get("context") or top_match.get("summary") or ""
    current_evidence = incident.get("evidence") or []
    past_evidence = top_match.get("evidence") or []

    classifier = get_laya_classifier()
    match_type = classifier.classify_incident_context(
        current_context=current_context,
        current_evidence=current_evidence,
        past_context=past_context,
        past_evidence=past_evidence,
    )

    if match_type == "same_context":
        log.info("Incident matches past context (%s). Selecting Nemotron Nano.", top_match.get("incident_id"))
        return "nano"
    else:
        log.info("Incident has shifted context from past (%s). Selecting Nemotron Super.", top_match.get("incident_id"))
        return "super"


def build_rca_agent(tier: str = "super") -> Agent:
    """Build a Reactive RCA Agent instance configured with the specified tier model."""
    model = build_model(tier)
    return Agent(
        model=model,
        name=f"rca_agent_{tier}",
        description="Incident diagnosis agent with read-only telemetry tools and Nemotron tier reasoning.",
        instruction=SYSTEM_INSTRUCTION,
        tools=list(ALL_TOOLS),
    )


class ReactiveRCAAgent:
    """Reactive RCA Agent managing tier selection and diagnostic response generation."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def get_agent_for_tier(self, tier: str) -> Agent:
        t = (tier or "super").lower()
        if t not in self._agents:
            self._agents[t] = build_rca_agent(t)
        return self._agents[t]

    def select_tier(self, incident: dict, recalled_matches: Optional[List[dict]] = None) -> str:
        return select_model_tier(incident, recalled_matches)


# Singleton instance
rca_agent_service = ReactiveRCAAgent()
