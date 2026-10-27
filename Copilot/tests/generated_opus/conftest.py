"""Shared fixtures for the generated reactive-path tests.

The parent ``tests/conftest.py`` already points the app at a temporary SQLite DB and wipes events/incidents/outbox
before every test (``clean_state``). Here we additionally:
  * wipe chats/messages,
  * force memory to NullMemory (the real .env may say MEMORY_MODE=on) so nothing reaches Hindsight,
  * give every test a fresh heuristic Laya classifier,
  * stub the dashboard's synchronous LLM call so no network is ever touched.
"""

from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import cycle
from agent import laya_classifier as laya_mod
from agent.memory import NullMemory, RecallResult, RetainOutcome
from db.database import get_db_connection


@pytest.fixture(autouse=True)
def reactive_isolation(monkeypatch):
    conn = get_db_connection()
    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM chats")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cycle, "_memory", NullMemory())
    monkeypatch.setattr(laya_mod, "_laya_classifier", laya_mod.LayaClassifier())
    import api.dashboard as dashboard
    monkeypatch.setattr(dashboard, "_llm_suggest_next_action", lambda rca_text, failed_actions, service: None)
    yield


# ── fake ADK runner ──────────────────────────────────────────────────────────────────────────

def make_event(text=None, final=True, role="model", partial=False, parts=None):
    if parts is None:
        parts = [NS(text=text, thought=None)]
    return NS(content=NS(role=role, parts=parts), partial=partial, is_final_response=lambda: final)


class FakeRunner:
    def __init__(self, events=None, exc=None):
        self.calls = []
        self.events = list(events or [])
        self.exc = exc

    async def run_async(self, user_id, session_id, new_message):
        self.calls.append({"user_id": user_id, "session_id": session_id,
                           "text": new_message.parts[0].text})
        for e in self.events:
            yield e
        if self.exc is not None:
            raise self.exc


@pytest.fixture
def runners(monkeypatch):
    """Replace both ADK runners with fakes. Returns (rca_runner, query_runner)."""
    import api.router as router
    rca, query = FakeRunner(), FakeRunner()
    monkeypatch.setattr(router, "runner", rca)
    monkeypatch.setattr(router, "query_runner", query)
    # tier routing: every Nemotron tier is served by the same fake unless a test installs its own
    monkeypatch.setattr(router, "_tier_runners", {"nano": rca, "ultra": rca})
    monkeypatch.setattr(router, "_chat_tiers", {})
    return rca, query


@pytest.fixture
def chat_client():
    from api.router import router
    app = FastAPI(redirect_slashes=False)
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def dash_client():
    from api.dashboard import router
    app = FastAPI(redirect_slashes=False)
    app.include_router(router)
    return TestClient(app)


# ── fake memory store (MemoryStore protocol) ─────────────────────────────────────────────────

class FakeMemory:
    enabled = True

    def __init__(self, matches=None, recall_exc=None, retain_status="retained"):
        self.matches = list(matches or [])
        self.recall_exc = recall_exc
        self.retain_status = retain_status
        self.retained = []
        self.recalls = []

    def recall(self, incident, query, exclude_id=None):
        self.recalls.append((incident, query, exclude_id))
        if self.recall_exc:
            raise self.recall_exc
        return RecallResult(matches=list(self.matches))

    def retain(self, incident, narrative, actions, fix, failed, source="live"):
        self.retained.append({"incident": incident, "narrative": narrative, "actions": actions, "fix": fix,
                              "failed": list(failed), "source": source})
        return RetainOutcome(self.retain_status, operation_id="op-1" if self.retain_status == "retained" else None)

    def operation_status(self, operation_id):
        return "completed"


class FakeHindsightClient:
    """Minimal hindsight_client.Hindsight surface used by HindsightMemory."""

    def __init__(self, results=None, fail_create=0, fail=False):
        self.results = results          # callable(tags, tags_match) -> list | None
        self.fail_create = fail_create
        self.fail = fail
        self.created = 0
        self.calls = []
        self.docs = {}

    def create_bank(self, **kw):
        self.calls.append(("create_bank", kw))
        if self.fail_create > 0:
            self.fail_create -= 1
            raise ConnectionError("starting")
        self.created += 1

    def recall(self, bank_id, query, tags=None, tags_match="any", **kw):
        self.calls.append(("recall", {"query": query, "tags": tags, "tags_match": tags_match}))
        if self.fail:
            raise ConnectionError("down")
        out = self.results(tags, tags_match) if self.results else []
        return NS(results=out)

    def retain(self, bank_id, content, document_id=None, update_mode=None, tags=None, metadata=None,
               retain_async=False, operation_id=None, **kw):
        self.calls.append(("retain", {"content": content, "document_id": document_id, "tags": list(tags or []),
                                      "metadata": metadata, "update_mode": update_mode,
                                      "retain_async": retain_async, "operation_id": operation_id}))
        if self.fail:
            raise ConnectionError("down")
        self.docs[document_id] = {"content": content, "tags": list(tags or [])}
        return NS(operation_id=operation_id)


def hit(doc, final, text="t", tags=None):
    return NS(document_id=doc, text=text, tags=list(tags or []), scores=NS(final=final))
