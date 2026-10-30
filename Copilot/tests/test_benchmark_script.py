import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_tiers.py"
spec = importlib.util.spec_from_file_location("benchmark_tiers", SCRIPT)
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def row(scenario, tier, chat_s, prompt=1000, completion=100, action="disable_fault", rep=1):
    return {"replicate": rep, "scenario": scenario, "tier": tier, "model": f"m-{tier}", "detection_s": 40.0, "auto_diag_s": 30.0,
            "auto_calls": 4, "auto_tokens": 9000, "chat_s": chat_s, "chat_calls": 3, "prompt_tokens": prompt,
            "completion_tokens": completion, "action": action}


def test_parse_action_handles_markdown_and_case():
    assert bt.parse_action("...\nProposed action: disable_fault") == "disable_fault"
    assert bt.parse_action("Proposed action: **Disable_Fault**") == "disable_fault"
    assert bt.parse_action("Proposed action: `restart_worker`.") == "restart_worker"
    assert bt.parse_action("no action line") is None and bt.parse_action("") is None


def test_median_ignores_missing_values():
    assert bt.median([10, None, 30, 20]) == 20 and bt.median([None, None]) is None and bt.median([]) is None


def test_summarize_reports_tier_hits_and_medians_per_scenario():
    rows = [row("new_incident", "ultra", 40), row("new_incident", "ultra", 50), row("new_incident", "super", 60),
            row("repeat_after_fix", "nano", 20), row("repeat_after_fix", "nano", 25, action="restart_worker")]
    s = {x["scenario"]: x for x in bt.summarize(rows)}
    assert s["new_incident"]["tier_hits"] == 2 and s["new_incident"]["runs"] == 3 and s["new_incident"]["chat_s"] == 50
    assert s["new_incident"]["tiers_seen"] == ["super", "ultra"]
    assert s["repeat_after_fix"]["tier_hits"] == 2 and s["repeat_after_fix"]["action_hits"] == 1
    assert "context_shift" not in s


def test_markdown_states_the_speedup_from_the_data():
    rows = [row("new_incident", "ultra", 40, 4000, 400), row("repeat_after_fix", "nano", 10, 1000, 100)]
    md = bt.render_markdown(bt.summarize(rows), {"date": "d", "copilot": "c", "commit": "abc", "runs": 1, "auto_modes": {}})
    assert "75% faster" in md and "75% fewer" in md and "ultra (1/1)" in md and "nano (1/1)" in md


def test_markdown_flags_when_the_repeat_was_not_faster():
    rows = [row("new_incident", "ultra", 20), row("repeat_after_fix", "nano", 30)]
    md = bt.render_markdown(bt.summarize(rows), {"runs": 1})
    assert "slower" in md and "not supported by these measurements" in md


def test_markdown_without_comparable_scenarios_says_so():
    md = bt.render_markdown(bt.summarize([row("new_incident", "ultra", 20)]), {"runs": 1})
    assert "Not enough data" in md
