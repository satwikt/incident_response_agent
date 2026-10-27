"""api/router.py: chat CRUD, /ask routing to the RCA / query runners (faked), and chat incident registration."""

from types import SimpleNamespace as NS

import pytest

from agent import config
from api import router as router_mod
from api.router import (
    _detect_service,
    _extract_evidence_and_severity,
    _is_rca_result,
    register_or_update_chat_incident,
)
from db import incidents
from db.database import get_db_connection
from tests.generated_opus.conftest import make_event

RCA_REPLY = ("**Symptom**: checkout failing.\n"
             "**Evidence**:\n- 503 DatabaseConnectionTimeout on POST /checkout\n- pool 20/20 in use\n"
             "**Root Cause**: connection pool exhausted.\n"
             "Proposed action: flush_pool")


def new_chat(client):
    r = client.post("/chat/new")
    assert r.status_code == 200
    return r.json()["id"]


def ask(client, chat_id, msg):
    return client.post(f"/ask/{chat_id}", json={"message": msg})


# ── chat CRUD ────────────────────────────────────────────────────────────────────────────────

def test_chat_new_appears_in_list_titled_new_chat(chat_client):
    """REQ: POST /chat/new creates a chat listed by GET /chats with the default 'New Chat' title (router.new_chat)."""
    cid = new_chat(chat_client)
    chats = chat_client.get("/chats").json()
    assert [c["id"] for c in chats] == [cid] and chats[0]["title"] == "New Chat" and chats[0]["created_at"]


def test_chat_list_newest_first(chat_client):
    """REQ: chats are listed newest first (router.get_all_chats ORDER BY id DESC) for the sidebar."""
    ids = [new_chat(chat_client) for _ in range(3)]
    assert [c["id"] for c in chat_client.get("/chats").json()] == list(reversed(ids))


def test_chat_get_new_chat_has_no_messages(chat_client):
    """REQ: GET /chat/{id} returns the chat with its (empty) message history (ChatResponse schema)."""
    cid = new_chat(chat_client)
    data = chat_client.get(f"/chat/{cid}").json()
    assert data["id"] == cid and data["title"] == "New Chat" and data["messages"] == []


@pytest.mark.parametrize("method,path", [("get", "/chat/999"), ("delete", "/chat/999")])
def test_chat_unknown_id_is_404(chat_client, method, path):
    """REQ: unknown chat -> 404 'Chat not found' (router.get_chat / delete_chat)."""
    r = getattr(chat_client, method)(path)
    assert r.status_code == 404 and r.json()["detail"] == "Chat not found"


def test_chat_rename_unknown_is_404(chat_client):
    """REQ: renaming an unknown chat -> 404 (router.rename_chat)."""
    assert chat_client.put("/chat/999/rename", json={"title": "x"}).status_code == 404


def test_chat_rename_strips_and_supports_unicode(chat_client):
    """REQ: rename sets the stripped title (router.rename_chat)."""
    cid = new_chat(chat_client)
    assert chat_client.put(f"/chat/{cid}/rename", json={"title": "  Checkout 503s 🚨 ü  "}).json() == {"success": True}
    assert chat_client.get(f"/chat/{cid}").json()["title"] == "Checkout 503s 🚨 ü"


def test_chat_rename_blank_title_falls_back_to_new_chat(chat_client):
    """REQ: a blank title becomes 'New Chat' (router.rename_chat)."""
    cid = new_chat(chat_client)
    chat_client.put(f"/chat/{cid}/rename", json={"title": "x"})
    chat_client.put(f"/chat/{cid}/rename", json={"title": "   "})
    assert chat_client.get(f"/chat/{cid}").json()["title"] == "New Chat"


def test_chat_rename_missing_title_is_422(chat_client):
    """REQ: RenameChatRequest requires 'title' (api/schema.py)."""
    cid = new_chat(chat_client)
    assert chat_client.put(f"/chat/{cid}/rename", json={}).status_code == 422


def test_chat_delete_removes_chat_and_messages_then_404(chat_client, runners):
    """REQ: DELETE /chat/{id} removes the chat and its messages; deleting again -> 404 (router.delete_chat)."""
    _, query = runners
    query.events = [make_event("ok")]
    cid = new_chat(chat_client)
    ask(chat_client, cid, "show logs")
    assert chat_client.delete(f"/chat/{cid}").json() == {"success": True}
    conn = get_db_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (cid,)).fetchone()[0] == 0
    finally:
        conn.close()
    assert chat_client.get(f"/chat/{cid}").status_code == 404
    assert chat_client.delete(f"/chat/{cid}").status_code == 404


# ── /ask ─────────────────────────────────────────────────────────────────────────────────────

def test_ask_unknown_chat_is_404_and_no_agent_runs(chat_client, runners):
    """REQ: /ask on a missing chat -> 404 (router.ask_chat) and no model call."""
    rca, query = runners
    assert ask(chat_client, 4242, "why is checkout down").status_code == 404
    assert rca.calls == [] and query.calls == []


def test_ask_missing_message_is_422(chat_client, runners):
    """REQ: AskChatRequest requires 'message' (api/schema.py)."""
    cid = new_chat(chat_client)
    assert chat_client.post(f"/ask/{cid}", json={}).status_code == 422


def test_ask_first_message_auto_titles_chat_with_30_chars(chat_client, runners):
    """REQ: a fresh 'New Chat' is auto-renamed to the first 30 chars of its first message (router.ask_chat)."""
    _, query = runners
    query.events = [make_event("ok")]
    cid = new_chat(chat_client)
    msg = "show the p95 latency for every route in checkout please"
    ask(chat_client, cid, msg)
    ask(chat_client, cid, "show logs now")
    assert chat_client.get(f"/chat/{cid}").json()["title"] == msg[:30]


def test_ask_does_not_retitle_manually_renamed_chat(chat_client, runners):
    """REQ: only a fresh 'New Chat' is auto-titled (router.ask_chat condition)."""
    _, query = runners
    query.events = [make_event("ok")]
    cid = new_chat(chat_client)
    chat_client.put(f"/chat/{cid}/rename", json={"title": "My investigation"})
    ask(chat_client, cid, "show logs")
    assert chat_client.get(f"/chat/{cid}").json()["title"] == "My investigation"


def test_ask_persists_user_and_agent_messages_in_order(chat_client, runners):
    """REQ: chat history is persisted in SQLite (router module docstring) - user then agent, with llm_calls."""
    _, query = runners
    query.events = [make_event("Error rate is 0%.")]
    cid = new_chat(chat_client)
    ask(chat_client, cid, "show error rate")
    msgs = chat_client.get(f"/chat/{cid}").json()["messages"]
    assert [(m["role"], m["message"]) for m in msgs] == [("user", "show error rate"), ("agent", "Error rate is 0%.")]
    assert msgs[1]["llm_calls"] == 1


def test_ask_query_intent_uses_query_runner_without_memory_or_incident(chat_client, runners, monkeypatch):
    """REQ: 'query' intent -> Reactive Query Agent (ARCHITECTURE.md §5.2); query path is read-only and never mutates
    incident state (ARCHITECTURE.md §2.3)."""
    rca, query = runners
    query.events = [make_event("```log\n[12:00:00] INFO | GET /cart | 200\n```")]
    recalls = []
    monkeypatch.setattr("agent.tools.recall_past_incidents", lambda **kw: recalls.append(kw) or {"past_incidents": []})
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "fetch logs for the cart").json()
    assert data["intent"] == "query" and data["agent"] == "query_agent" and data["incident_id"] is None
    assert len(query.calls) == 1 and rca.calls == [] and recalls == []
    assert query.calls[0]["text"] == "fetch logs for the cart"
    assert incidents.list_all() == []


def test_ask_query_intent_never_registers_incident_even_if_reply_mentions_root_cause(chat_client, runners):
    """REQ: the Query Agent 'never mutates incident states' (ARCHITECTURE.md §2.3); a query answer that happens to contain
    'Root cause:' or 'Proposed action:' text must not open/modify an incident."""
    _, query = runners
    query.events = [make_event("Docs excerpt - Root cause: pool leak. Proposed action: flush_pool")]
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "search docs for pool runbook").json()
    assert data["intent"] == "query"
    assert data["incident_id"] is None
    assert incidents.list_all() == []


def test_ask_rca_intent_uses_rca_runner_and_registers_incident(chat_client, runners):
    """REQ: 'rca' intent -> Reactive RCA Agent; RCA results become an incident dossier tracked on the dashboard
    (ARCHITECTURE.md §2.2 step 4; router.ask_chat)."""
    rca, query = runners
    rca.events = [make_event(RCA_REPLY)]
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "why is checkout failing?").json()
    assert data["intent"] == "rca" and data["agent"] == "rca_agent" and query.calls == []
    inc_id = data["incident_id"]
    assert inc_id and inc_id.startswith("INC-") and inc_id in data["response"]
    inc = incidents.get(inc_id)
    assert inc["rca_status"] == "ok" and inc["proposed_action"] == "flush_pool" and inc["status"] == "OPEN"
    assert inc["llm_calls"] == 1
    stored = chat_client.get(f"/chat/{cid}").json()["messages"][-1]["message"]
    assert inc_id in stored


def test_ask_rca_injects_recalled_past_incidents_into_prompt(chat_client, runners, monkeypatch):
    """REQ: RCA 'Queries Hindsight Memory for historical incidents' first (ARCHITECTURE.md §2.2 step 1); recalled
    precedent with its verified fix and failed actions is given to the agent."""
    rca, _ = runners
    rca.events = [make_event(RCA_REPLY)]
    past = {"past_incidents": [{"incident_id": "INC-0007", "service": "checkout", "summary": "pool exhausted",
                                "root_cause_summary": "leak", "working_fix": "flush_pool",
                                "failed_actions": ["restart_worker"]}]}
    monkeypatch.setattr("agent.tools.recall_past_incidents", lambda **kw: past)
    cid = new_chat(chat_client)
    ask(chat_client, cid, "why is checkout failing?")
    sent = rca.calls[0]["text"]
    assert "INC-0007" in sent and "flush_pool" in sent and "restart_worker" in sent
    assert sent.endswith("why is checkout failing?")


def test_ask_rca_recall_failure_is_fail_open(chat_client, runners, monkeypatch):
    """REQ: memory fails open - the pipeline still works without memory (memory.py module docstring)."""
    rca, _ = runners
    rca.events = [make_event(RCA_REPLY)]

    def boom(**kw):
        raise ConnectionError("hindsight down")
    monkeypatch.setattr("agent.tools.recall_past_incidents", boom)
    cid = new_chat(chat_client)
    r = ask(chat_client, cid, "why is checkout failing?")
    assert r.status_code == 200 and rca.calls[0]["text"] == "why is checkout failing?"


def test_ask_runner_exception_returns_agent_error_without_incident(chat_client, runners):
    """REQ: agent failures are reported to the operator and never registered as an RCA result (router._is_rca_result)."""
    rca, _ = runners
    rca.exc = RuntimeError("NIM 503")
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "why is checkout failing?").json()
    assert data["response"].startswith("Agent error:") and "NIM 503" not in data["response"]  # B03: no raw provider text
    assert data["incident_id"] is None and incidents.list_all() == []
    assert chat_client.get(f"/chat/{cid}").json()["messages"][-1]["role"] == "agent"


def test_ask_no_output_reports_placeholder_without_incident(chat_client, runners):
    """REQ: an empty agent answer becomes 'Agent produced no output.' and is not an RCA result (router.ask_chat)."""
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "why is checkout failing?").json()
    assert data["response"] == "Agent produced no output." and data["incident_id"] is None and data["llm_calls"] == 0


def test_ask_reply_excludes_thought_parts_and_reasoning_tags(chat_client, runners):
    """REQ: zero internal thought leakage in chat answers (readme §1 Query Agent; ARCHITECTURE.md §2.3; anti-leak rule)."""
    _, query = runners
    parts = [NS(text="secret chain of thought", thought=True), NS(text="<think>plan</think>Error rate is 2%.", thought=None)]
    query.events = [make_event(parts=parts)]
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "show error rate").json()
    assert data["response"] == "Error rate is 2%."


def test_ask_counts_only_final_model_turns_as_llm_calls(chat_client, runners):
    """REQ: llm_calls reports model calls (AskChatResponse.llm_calls); partial stream chunks and tool events are not calls."""
    _, query = runners
    query.events = [make_event("tool call", final=False), make_event("chunk", final=False, partial=True),
                    make_event("tool result", final=False, role="user"), make_event("Final answer.")]
    cid = new_chat(chat_client)
    data = ask(chat_client, cid, "show logs").json()
    assert data["llm_calls"] == 2 and data["response"] == "Final answer."


def test_ask_uses_one_session_per_chat(chat_client, runners):
    """REQ: multi-turn conversational assistant (readme §5.4): turns of a chat share one agent session; chats are isolated."""
    _, query = runners
    query.events = [make_event("ok")]
    a, b = new_chat(chat_client), new_chat(chat_client)
    ask(chat_client, a, "show logs")
    ask(chat_client, a, "show traces")
    ask(chat_client, b, "show logs")
    sessions = [c["session_id"] for c in query.calls]
    assert sessions[0] == sessions[1] != sessions[2]


def test_ask_empty_message_is_handled(chat_client, runners):
    """REQ: /ask never crashes on an empty message; intent is one of rca/query (classify_chat_intent docstring)."""
    _, query = runners
    query.events = [make_event("Please describe the problem.")]
    rca, _ = runners
    rca.events = [make_event("Please describe the problem.")]
    cid = new_chat(chat_client)
    r = ask(chat_client, cid, "")
    assert r.status_code == 422                      # B26: a blank message is rejected before any LLM call
    assert ask(chat_client, cid, "   ").status_code == 422


# ── helpers ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prompt,reply,svc", [
    ("why is catalog slow", "", "catalog"),
    ("frontend errors", "", "frontend"),
    ("", "Root cause in checkout service", "checkout"),
])
def test_router_detect_service_from_text(prompt, reply, svc):
    """REQ: chat incidents are filed against the service named in the conversation (router._detect_service; readme
    services checkout/catalog/frontend)."""
    assert _detect_service(prompt, reply) == svc


def test_router_detect_service_defaults_to_monitored_service():
    """REQ: when no service is named, the incident belongs to the monitored service config.SERVICE_NAME ('The service whose
    events the agent reasons about', config.py; APP_SERVICE_NAME env). The code reads a non-existent
    config.APP_SERVICE_NAME attribute."""
    assert _detect_service("why are errors spiking", "Root cause: pool leak") == config.SERVICE_NAME


@pytest.mark.parametrize("intent,reply,expected", [
    ("rca", "Root cause: x", True),
    ("rca", "Agent error: boom", False),
    ("rca", "Agent produced no output.", False),
    ("rca", "", False),
])
def test_router_is_rca_result(intent, reply, expected):
    """REQ: only genuine RCA answers are registered; agent errors/empty output are not (router._is_rca_result)."""
    assert _is_rca_result(intent, reply) is expected


def test_router_evidence_extracted_from_bullets_until_root_cause():
    """REQ: incident dossier evidence comes from the answer's Evidence section (RESPONSE FORMAT §2 in SYSTEM_INSTRUCTION)."""
    ev, _ = _extract_evidence_and_severity("p", RCA_REPLY)
    assert ev == ["503 DatabaseConnectionTimeout on POST /checkout", "pool 20/20 in use"]


def test_router_evidence_json_bullet_unpacked():
    """REQ: evidence items are clean text; JSON log lines are unpacked to message (exception) (router._clean_evidence_item)."""
    reply = '**Evidence**:\n- {"message": "pool exhausted", "exception": "TimeoutError", "level": "ERROR"}\n**Root Cause**: x'
    ev, _ = _extract_evidence_and_severity("p", reply)
    assert ev == ["[ERROR] pool exhausted (TimeoutError)"]


def test_router_evidence_capped_at_five():
    """REQ: dossier evidence is a short list (router keeps at most 5 items)."""
    reply = "Evidence:\n" + "\n".join(f"- line {i}" for i in range(10))
    assert len(_extract_evidence_and_severity("p", reply)[0]) == 5


def test_router_evidence_fallback_quotes_prompt():
    """REQ: without an Evidence section the operator's report is used as evidence (router)."""
    ev, sev = _extract_evidence_and_severity("x" * 300, "nothing structured")
    assert ev == ["Reported in chat: " + "x" * 120] and sev == "warning"


@pytest.mark.parametrize("reply", ["5xx spike", "CRITICAL outage", "process crash", "raised an exception"])
def test_router_severity_critical_keywords(reply):
    """REQ: 5xx/crash/outage/exception answers are critical, otherwise warning (router._extract_evidence_and_severity)."""
    assert _extract_evidence_and_severity("p", reply)[1] == "critical"


# ── register_or_update_chat_incident ─────────────────────────────────────────────────────────

FP = [{"fp": "fp-x", "kind": "error_rate", "route": "POST /checkout", "sig": "5xx"}]


def test_register_updates_pending_active_incident_instead_of_duplicating():
    """REQ: a chat diagnosis correlates with an active incident that still needs diagnosis (router comment step 1) so the
    watcher's incident gets the RCA instead of a duplicate dossier."""
    inc = incidents.create("checkout", FP, "critical", ["5xx"], 1_700_000_000_000)
    got = register_or_update_chat_incident(7, "why is checkout failing", RCA_REPLY, 3)
    assert got == inc["id"] and len(incidents.list_all()) == 1
    upd = incidents.get(inc["id"])
    assert upd["rca_status"] == "ok" and upd["rca_text"] == RCA_REPLY and upd["proposed_action"] == "flush_pool"
    assert upd["llm_calls"] == 3
    ev = incidents.list_events(inc["id"])[-1]
    assert ev["kind"] == "diagnosis:ok" and ev["actor"] == "chat_operator" and "Chat #7" in ev["detail"]


def test_register_attaches_to_active_incident_that_is_already_diagnosed():
    """B06: one fault = one incident. A repeated question while the service has an active incident adds a timeline entry
    to it; the existing diagnosis is kept and no duplicate dossier is created."""
    inc = incidents.create("checkout", FP, "critical", ["5xx"], 1_700_000_000_000)
    incidents.set_rca(inc["id"], "done", "ok")
    got = register_or_update_chat_incident(7, "checkout", RCA_REPLY, 1)
    assert got == inc["id"] and len(incidents.list_all()) == 1
    assert incidents.get(inc["id"])["rca_text"] == "done"
    assert incidents.list_events(inc["id"])[-1]["kind"] == "diagnosis:chat"


def test_register_ignores_resolved_incidents():
    """REQ: RESOLVED is terminal (db/incidents.py docstring) - a chat RCA never rewrites a resolved incident."""
    inc = incidents.create("checkout", FP, "critical", ["5xx"], 1_700_000_000_000)
    incidents.transition(inc["id"], "RESOLVED", None, "t", 1_700_000_001_000)
    got = register_or_update_chat_incident(7, "checkout", RCA_REPLY, 1)
    assert got != inc["id"] and incidents.get(inc["id"])["rca_text"] is None


def test_register_new_dossier_fields():
    """REQ: a chat RCA creates an OPEN incident with evidence, severity, a chat fingerprint and a diagnosis:ok event
    (router step 2)."""
    got = register_or_update_chat_incident(9, "checkout broken", RCA_REPLY, 2)
    inc = incidents.get(got)
    assert inc["status"] == "OPEN" and inc["service"] == "checkout" and inc["severity"] in {"critical", "warning"}
    assert len(inc["evidence"]) == 2 and inc["evidence"][1] == "pool 20/20 in use"
    assert inc["fingerprints"][0]["kind"] == "chat_rca" and inc["context_sample"] == "chat_session=9"
    assert [e["kind"] for e in incidents.list_events(got)] == ["opened", "diagnosis:ok"]


def test_register_non_allow_listed_proposal_is_not_stored():
    """REQ: 'Only these actions can ever be proposed; anything else in a model answer is ignored' (config.ALLOWED_ACTIONS)."""
    got = register_or_update_chat_incident(9, "checkout", "Root cause: x\nProposed action: drop_database", 1)
    assert incidents.get(got)["proposed_action"] is None


def test_register_second_chat_joins_the_open_incident_and_new_one_after_resolution():
    """B06: a second chat about the same open incident joins it; once it is resolved the next diagnosis opens a new
    dossier with its own fingerprint (router fp hash includes chat id)."""
    a = register_or_update_chat_incident(1, "checkout", RCA_REPLY, 1)
    assert register_or_update_chat_incident(2, "checkout", RCA_REPLY, 1) == a
    incidents.transition(a, "RESOLVED", None, "t", 1_700_000_001_000)
    b = register_or_update_chat_incident(3, "checkout", RCA_REPLY, 1)
    assert a != b
    assert incidents.get(a)["fingerprint"] != incidents.get(b)["fingerprint"]


@pytest.mark.skip(reason="AMBIGUOUS: context_sample is taken with memory.context_of(agent_reply), i.e. whatever follows the "
                         "last ' | ' in the markdown answer (e.g. a table cell). context_of is documented for EVENT messages; "
                         "unclear what a chat incident's context_sample should be.")
def test_register_context_sample_from_markdown_table():
    """REQ: unclear."""


@pytest.mark.skip(reason="AMBIGUOUS: inline '**Evidence**: text on the same line' is dropped (any line containing the word "
                         "'evidence' is treated as a header, e.g. '- no evidence of DB errors'). No spec for the parser.")
def test_router_evidence_inline_header():
    """REQ: unclear."""
