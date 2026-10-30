"""Chat cost guard (api/budget.py): durable call budget, no model call at the limit, labelled replay only from a real recording."""

import importlib.util
import json
from pathlib import Path

import pytest

from agent import config
from api import budget
from db.database import get_db_connection
from tests.generated_opus.conftest import make_event
from tests.generated_opus.test_chat_router_reactive import RCA_REPLY, ask, new_chat


def add_agent_message(chat_id, calls, created_at=None):
    conn = get_db_connection()
    try:
        if created_at:
            conn.execute("INSERT INTO messages (chat_id, role, message, llm_calls, created_at) VALUES (?, 'agent', 'x', ?, datetime('now', ?))",
                         (chat_id, calls, created_at))
        else:
            conn.execute("INSERT INTO messages (chat_id, role, message, llm_calls) VALUES (?, 'agent', 'x', ?)", (chat_id, calls))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CHAT_MAX_LLM_CALLS_PER_HOUR", 0)
    monkeypatch.setattr(config, "CHAT_MAX_LLM_CALLS_PER_DAY", 0)
    monkeypatch.setattr(config, "REPLAY_PATH", str(tmp_path / "replay.json"))
    budget._replay_cache.update(path=None, data=budget._MISSING)


def test_no_limit_means_no_pause(chat_client):
    add_agent_message(new_chat(chat_client), 500)
    assert budget.exhausted_reason() is None


def test_hourly_and_daily_limits_count_stored_agent_calls(chat_client, monkeypatch):
    cid = new_chat(chat_client)
    add_agent_message(cid, 4)
    monkeypatch.setattr(config, "CHAT_MAX_LLM_CALLS_PER_HOUR", 5)
    assert budget.exhausted_reason() is None
    add_agent_message(cid, 1)
    assert "hourly limit of 5" in budget.exhausted_reason()
    monkeypatch.setattr(config, "CHAT_MAX_LLM_CALLS_PER_HOUR", 0)
    monkeypatch.setattr(config, "CHAT_MAX_LLM_CALLS_PER_DAY", 5)
    assert "daily limit of 5" in budget.exhausted_reason()


def test_old_calls_do_not_count_against_the_hour(chat_client, monkeypatch):
    add_agent_message(new_chat(chat_client), 50, created_at="-3 hours")
    monkeypatch.setattr(config, "CHAT_MAX_LLM_CALLS_PER_HOUR", 10)
    monkeypatch.setattr(config, "CHAT_MAX_LLM_CALLS_PER_DAY", 100)
    assert budget.exhausted_reason() is None


def test_at_the_limit_the_chat_makes_no_model_call_and_says_so(chat_client, runners, monkeypatch):
    rca, query = runners
    rca.events = [make_event(RCA_REPLY)]
    cid = new_chat(chat_client)
    add_agent_message(cid, 3)
    monkeypatch.setattr(config, "CHAT_MAX_LLM_CALLS_PER_HOUR", 3)
    data = ask(chat_client, cid, "why is checkout failing?").json()
    assert data["budget_exhausted"] is True and data["llm_calls"] == 0 and data["incident_id"] is None
    assert "Demo budget reached" in data["response"] and "REPLAY" not in data["response"]       # no recording yet: no replay
    assert rca.calls == [] and query.calls == []
    assert chat_client.get(f"/chat/{cid}").json()["messages"][-1]["message"] == data["response"]


def write_replay(path):
    path.write_text(json.dumps({"meta": {"recorded": "2026-10-28 10:00 UTC"}, "replies": {
        "new_incident": {"question": "why is checkout failing?",
                         "response": "**Symptom**: payments declined.\n\n🛡️ **Incident Registered:** [`INC-0001`](/dashboard)",
                         "tier": "ultra", "model": "m-ultra"},
        "context_shift": {"question": "503?", "response": "**Symptom**: pool exhausted.", "tier": "super", "model": "m-super"},
        "repeat_after_fix": {"question": "again", "response": "**Symptom**: repeat.", "tier": "nano", "model": "m-nano"}}}),
        encoding="utf-8")


def test_a_recorded_run_is_replayed_with_a_clear_label(chat_client, runners, monkeypatch, tmp_path):
    write_replay(tmp_path / "replay.json")
    cid = new_chat(chat_client)
    add_agent_message(cid, 9)
    monkeypatch.setattr(config, "CHAT_MAX_LLM_CALLS_PER_DAY", 9)
    text = ask(chat_client, cid, "checkout returns 503, why?").json()["response"]
    assert "REPLAY, not a live answer" in text and "pool exhausted" in text and "`m-super`" in text and "Recorded 2026-10-28" in text
    text = ask(chat_client, cid, "why is checkout failing?").json()["response"]
    assert "payments declined" in text and "Incident Registered" not in text


@pytest.mark.parametrize("msg,scenario", [("checkout is returning 503", "context_shift"), ("db pool exhausted?", "context_shift"),
                                          ("it is failing again", "repeat_after_fix"), ("why is checkout failing?", "new_incident")])
def test_replay_scenario_is_chosen_from_the_question(msg, scenario):
    assert budget.pick_scenario(msg) == scenario


def test_unusable_replay_file_degrades_to_the_plain_notice(tmp_path):
    (tmp_path / "replay.json").write_text("{not json", encoding="utf-8")
    text = budget.budget_reply("why?", "limit")
    assert "REPLAY" not in text and "Demo budget reached" in text


def test_benchmark_records_a_replay_only_from_real_replies():
    spec = importlib.util.spec_from_file_location("bt", Path(__file__).resolve().parents[3] / "scripts" / "benchmark_tiers.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    rows = [{"scenario": "new_incident", "replicate": 1, "http_status": 200, "tier": "super", "model": "a", "question": "q", "reply_text": "first"},
            {"scenario": "new_incident", "replicate": 2, "http_status": 200, "tier": "ultra", "model": "b", "question": "q", "reply_text": "second"},
            {"scenario": "context_shift", "replicate": 1, "http_status": 500, "tier": "super", "model": "a", "question": "q", "reply_text": "err"},
            {"scenario": "repeat_after_fix", "replicate": 1, "http_status": 200, "tier": "nano", "model": "c", "question": "q", "reply_text": "  "}]
    replay = bt.build_replay(rows, {"date": "d", "commit": "x"})
    assert set(replay["replies"]) == {"new_incident"} and replay["replies"]["new_incident"]["response"] == "second"   # expected tier wins
    assert bt.build_replay([], {}) is None
