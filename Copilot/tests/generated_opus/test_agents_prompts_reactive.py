"""Reactive RCA tier selection, agent construction (RCA / query / root) and prompt builders."""

import re

import pytest

from agent import config
from agent import laya_classifier as laya_mod
from agent import rca_agent as rca_mod
from agent import query_agent as query_mod
from agent.agent import SYSTEM_INSTRUCTION, root_agent
from agent.prompts import build_alert_prompt, build_next_action_prompt, build_verify_prompt, describe_match
from agent.rca_agent import ReactiveRCAAgent, select_model_tier
from agent.tools import ALL_TOOLS


class StubClassifier:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def classify_incident_context(self, **kw):
        self.calls.append(kw)
        return self.answer


def _enriched_match(context, evidence=None, inc_id="INC-0001"):
    """Shape produced by cycle._enrich (Recalled.as_dict + local context/evidence)."""
    return {"incident_id": inc_id, "text": "n", "exact": True, "score": 0.9, "fix": "flush_pool", "failed": [],
            "source": "live", "context": context, "evidence": evidence or ["503 pool exhausted"]}


# ── select_model_tier ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("incident,recalled", [
    ({"id": "X"}, None),
    ({"id": "X", "recalled": []}, None),
    ({"id": "X", "recalled": None}, []),
])
def test_rca_tier_no_recalled_match_is_ultra(incident, recalled):
    """REQ: Completely new incident (no recalled past incidents) -> Nemotron Ultra (rca_agent module docstring;
    readme §3 'New / 0-day incident')."""
    assert select_model_tier(incident, recalled) == "ultra"


def test_rca_tier_uses_incident_recalled_when_no_explicit_matches():
    """REQ: tier routing is 'based on Hindsight memory' recalled for the incident (rca_agent docstring); the incident's
    stored recalled matches (cycle._enrich shape) are used, identical context -> nano."""
    ctx = "release=v2.4.0 config_rev=cfg-r41"
    inc = {"id": "INC-0002", "context_sample": ctx, "evidence": ["503 pool exhausted"], "recalled": [_enriched_match(ctx)]}
    assert select_model_tier(inc) == "nano"


def test_rca_tier_config_shift_is_super():
    """REQ: recalled past incident with differing runtime context (config change) -> Nemotron Super (rca_agent docstring)."""
    inc = {"id": "INC-0002", "context_sample": "release=v2.4.0 config_rev=cfg-r42"}
    assert select_model_tier(inc, [_enriched_match("release=v2.4.0 config_rev=cfg-r41")]) == "super"


def test_rca_tier_routes_same_context_answer_to_nano(monkeypatch):
    """REQ: Laya 'same_context' -> Nano (ARCHITECTURE.md §2.2 / §5)."""
    stub = StubClassifier("same_context")
    monkeypatch.setattr(laya_mod, "_laya_classifier", stub)
    assert select_model_tier({"id": "X", "context_sample": "a"}, [_enriched_match("b")]) == "nano"


def test_rca_tier_routes_different_context_answer_to_super(monkeypatch):
    """REQ: Laya 'different_context' -> Super (ARCHITECTURE.md §2.2 / §5)."""
    stub = StubClassifier("different_context")
    monkeypatch.setattr(laya_mod, "_laya_classifier", stub)
    assert select_model_tier({"id": "X", "context_sample": "a"}, [_enriched_match("a")]) == "super"


def test_rca_tier_passes_contexts_and_evidence_to_laya(monkeypatch):
    """REQ: Laya compares current runtime facts/evidence against the recalled incident (ARCHITECTURE.md §2.2 step 2)."""
    stub = StubClassifier("same_context")
    monkeypatch.setattr(laya_mod, "_laya_classifier", stub)
    inc = {"id": "X", "context_sample": "CUR", "evidence": ["cur-ev"]}
    select_model_tier(inc, [_enriched_match("PAST", ["past-ev"])])
    call = stub.calls[0]
    assert call["current_context"] == "CUR" and call["past_context"] == "PAST"
    assert call["current_evidence"] == ["cur-ev"] and call["past_evidence"] == ["past-ev"]


def test_rca_tier_non_dict_match_does_not_crash():
    """REQ: tier selection always yields one of the documented tiers (rca_agent docstring) even for malformed matches."""
    assert select_model_tier({"id": "X"}, ["garbage"]) in {"nano", "super", "ultra"}


# ── ReactiveRCAAgent ─────────────────────────────────────────────────────────────────────────

def test_rca_service_caches_agent_per_tier(monkeypatch):
    """REQ: ReactiveRCAAgent manages one agent per tier (class docstring 'managing tier selection'); repeat calls reuse it."""
    built = []
    monkeypatch.setattr(rca_mod, "build_rca_agent", lambda t: built.append(t) or object())
    svc = ReactiveRCAAgent()
    a1 = svc.get_agent_for_tier("nano")
    a2 = svc.get_agent_for_tier("NANO")
    svc.get_agent_for_tier("ultra")
    assert a1 is a2 and built == ["nano", "ultra"]


def test_rca_service_none_tier_defaults_to_super(monkeypatch):
    """REQ: build_rca_agent default tier is 'super' (signature default) - a missing tier must map to super."""
    built = []
    monkeypatch.setattr(rca_mod, "build_rca_agent", lambda t: built.append(t) or object())
    ReactiveRCAAgent().get_agent_for_tier(None)
    assert built == ["super"]


def test_rca_service_select_tier_delegates():
    """REQ: ReactiveRCAAgent.select_tier applies the documented ultra/super/nano routing (module docstring)."""
    assert ReactiveRCAAgent().select_tier({"id": "X"}, []) == "ultra"


@pytest.mark.parametrize("tier", ["nano", "super", "ultra"])
def test_rca_build_agent_uses_tier_model_and_diagnosis_prompt(monkeypatch, tier):
    """REQ: build_rca_agent builds an RCA agent 'configured with the specified tier model', with read-only telemetry
    tools (rca_agent.build_rca_agent docstring; Agent description)."""
    seen = []
    monkeypatch.setattr(rca_mod, "build_model", lambda t: seen.append(t) or "gemini-2.0-flash")
    a = rca_mod.build_rca_agent(tier)
    assert seen == [tier]
    assert a.name == f"rca_agent_{tier}" and a.instruction == SYSTEM_INSTRUCTION
    assert [t.__name__ for t in a.tools] == [t.__name__ for t in ALL_TOOLS]


# ── query agent / root agent ─────────────────────────────────────────────────────────────────

def test_query_agent_default_model_is_fast_nano(monkeypatch):
    """REQ: build_query_agent uses 'specified model or default fast model' (docstring) -> Nemotron Nano helper."""
    monkeypatch.setattr(query_mod, "build_nano_model", lambda: "nano-model-x")
    assert query_mod.build_query_agent().model == "nano-model-x"


def test_query_agent_explicit_model_is_used(monkeypatch):
    """REQ: build_query_agent uses the specified model when given (docstring)."""
    monkeypatch.setattr(query_mod, "build_nano_model", lambda: pytest.fail("nano must not be built"))
    assert query_mod.build_query_agent("gemini-2.0-flash").model == "gemini-2.0-flash"


def test_query_agent_instruction_forbids_remediation():
    """REQ: Query Agent is strictly read-only and never proposes destructive actions (ARCHITECTURE.md §2.3; query_agent
    module docstring)."""
    instr = query_mod.QUERY_SYSTEM_INSTRUCTION
    assert "Do NOT propose operational fixes" in instr
    assert "Proposed action:" not in instr


def test_query_agent_tools_contain_no_mutating_operations():
    """REQ: Query Agent is non-mutating (query_agent docstring); none of its tools may be an ops/remediation call."""
    names = {t.__name__ for t in query_mod.build_query_agent("gemini-2.0-flash").tools}
    mutating = {"flush_pool", "restart_worker", "rollback_config", "rollback_release", "enable_fallback",
                "run_action", "execute_action", "ops_action"}
    assert not (names & mutating)
    assert all(re.match(r"^(get_|query_|search_|recall_)", n) for n in names)


def test_root_agent_instruction_requires_single_proposed_action_line():
    """REQ: diagnostic answers end with exactly one 'Proposed action: <name>' line (SYSTEM_INSTRUCTION response format)
    so parse_proposed_action can extract it."""
    assert "Proposed action: <name>" in SYSTEM_INSTRUCTION
    assert root_agent.name == "incident_copilot"
    assert len(root_agent.tools) == len(ALL_TOOLS)


def test_root_agent_recommended_actions_are_allow_listed():
    """REQ: RCA 'Proposes an operational fix strictly from ALLOWED_ACTIONS' (ARCHITECTURE.md §2.2 step 3); every action
    the instruction tells the model to propose must be accepted by the server."""
    section = SYSTEM_INSTRUCTION.split("**Recommendation**", 1)[1].split("RULES:", 1)[0]
    proposed = set(re.findall(r"propose ([a-z_]+_[a-z_]+)", section))
    proposed |= set(re.findall(r"\bor ([a-z]+_[a-z_]+)", section))
    assert proposed, "instruction should name concrete actions"
    assert proposed <= set(config.ALLOWED_ACTIONS), proposed - set(config.ALLOWED_ACTIONS)


# ── prompts ──────────────────────────────────────────────────────────────────────────────────

def test_prompt_next_action_includes_diagnosis_failures_service_and_choices(monkeypatch):
    """REQ: next-action prompt shows the original diagnosis and which actions were already tried, and asks for exactly
    one 'Proposed action:' line (build_next_action_prompt docstring)."""
    monkeypatch.setattr(config, "ALLOWED_ACTIONS", ("flush_pool", "enable_fallback"))
    p = build_next_action_prompt("  Root cause: pool leak.\n  ", ["restart_worker", "flush_pool"], "checkout")
    assert "service 'checkout'" in p
    assert "Root cause: pool leak." in p
    assert "did NOT resolve the incident: restart_worker, flush_pool." in p
    assert "Available actions: [flush_pool, enable_fallback]" in p
    assert p.rstrip().endswith("Proposed action: <action_name>")


def test_prompt_next_action_without_failures_says_none():
    """REQ: when nothing was attempted the prompt states 'none' (build_next_action_prompt)."""
    p = build_next_action_prompt("diag", [], "svc")
    assert "did NOT resolve the incident: none." in p


def test_prompt_next_action_empty_rca_and_unicode_service():
    """REQ: prompt builder tolerates an empty diagnosis and unicode service names (edge case; docstring)."""
    p = build_next_action_prompt("", ["x"], "kasse-ü")
    assert "service 'kasse-ü'" in p and "Proposed action:" in p


def test_prompt_describe_match_formats_confidence_and_fingerprint():
    """REQ: describe_match gives one line per recalled incident with provenance (describe_match docstring)."""
    line = describe_match({"incident_id": "INC-1", "exact": True, "score": 0.873, "source": "seed",
                           "context": "release=v1", "fix": "flush_pool", "failed": ["restart_worker"]})
    assert line.startswith("- INC-1 (seed, same fingerprint, confidence=87%)")
    assert "runtime context then: release=v1" in line and "fix that worked: flush_pool" in line
    assert "did NOT work: restart_worker" in line and "\n" not in line


def test_prompt_describe_match_minimal_defaults():
    """REQ: describe_match works with only an incident id: default source live, 'similar', no confidence."""
    assert describe_match({"incident_id": "INC-2"}) == "- INC-2 (live, similar)"


def test_prompt_describe_match_truncates_long_evidence():
    """REQ: describe_match is a ONE line summary (docstring); evidence is truncated."""
    line = describe_match({"incident_id": "I", "evidence": ["x" * 1000]})
    assert len(line) < 400


def test_prompt_alert_without_hypotheses_has_no_memory_section():
    """REQ: recalled incidents appear 'if any' (build_alert_prompt docstring)."""
    p = build_alert_prompt(["err 30%"], "snapshot")
    assert "HYPOTHESES" not in p and "  - err 30%" in p and "Proposed action: <name>" in p


def test_prompt_alert_with_hypotheses_marks_them_as_hypotheses():
    """REQ: recalled incidents are passed as hypotheses, not facts (build_alert_prompt docstring; memory.py docstring)."""
    p = build_alert_prompt(["e"], "s", [{"incident_id": "INC-9", "exact": True}])
    assert "HYPOTHESES" in p and "INC-9" in p


def test_prompt_verify_handles_no_matches_and_missing_context():
    """REQ: verify prompt is always well-formed; missing context reads 'not available' (build_verify_prompt)."""
    p = build_verify_prompt(["e1"], "", [])
    assert "- (none)" in p and "Current runtime context: not available" in p
    assert "Verdict: match | mismatch" in p
