from types import SimpleNamespace as NS

import pytest

from agent import config, cycle, notifier
from agent import memory as mm
from agent.memory import HindsightMemory, NullMemory, RecallResult, build_narrative, context_of, parse_proposed_action, split_actions
from agent.notifier import Delivery, render
from db import events as store
from db import incidents, outbox
from tests.test_cycle_notifier import Submitted, seed, wipe_events

ALLOWED = set(config.ALLOWED_ACTIONS)


# ── a fake Hindsight client with the surface we use ─────────────────────────────────────────

class FakeClient:
    def __init__(self):
        self.docs = {}            # document_id -> dict(content, tags, metadata)
        self.calls = []
        self.fail = False
        self.min_final = 0.0
        self.created = []

    def create_bank(self, **kw):
        self.created.append(kw)

    def retain(self, bank_id, content, document_id=None, update_mode=None, tags=None, metadata=None,
               retain_async=False, operation_id=None, **kw):
        self.calls.append(("retain", dict(bank_id=bank_id, document_id=document_id, update_mode=update_mode, tags=tags,
                                          metadata=metadata, retain_async=retain_async, operation_id=operation_id)))
        if self.fail:
            raise ConnectionError("hindsight down")
        self.docs[document_id] = {"content": content, "tags": list(tags or []), "metadata": metadata}
        return NS(operation_id=operation_id)

    def recall(self, bank_id, query, tags=None, tags_match="any", **kw):
        self.calls.append(("recall", dict(query=query, tags=tags, tags_match=tags_match)))
        if self.fail:
            raise ConnectionError("hindsight down")
        out = []
        for doc_id, d in self.docs.items():
            dt = set(d["tags"])
            want = set(tags or [])
            if tags_match == "any_strict" and not (dt & want):
                continue
            if tags_match == "all_strict" and not want <= dt:
                continue
            final = 0.9 if (tags_match == "any_strict") else 0.5
            out.append(NS(document_id=doc_id, text=d["content"][:200], tags=d["tags"], scores=NS(final=max(final, self.min_final))))
        return NS(results=out)


def hs(client=None, **kw):
    client = client or FakeClient()
    return HindsightMemory(client, "bank", status_fn=lambda op: "completed", **kw), client


@pytest.fixture(autouse=True)
def reset_memory():
    yield
    cycle.set_memory(NullMemory())


INC = {"id": "INC-0007", "service": "todo-app", "fingerprints": [{"fp": "fp-aaa", "kind": "error_rate", "route": "POST /todos",
                                                                    "sig": "SimulatedDatabaseStateError"}],
       "evidence": ["Error rate on 'POST /todos' is 30%"], "rca_text": "Root cause: bad deploy."}


# ── pure helpers ───────────────────────────────────────────────────────────────────────────

def test_context_is_the_suffix_after_the_last_pipe():
    assert context_of("POST /todos -> 500 X: y | release=v2.4.0 config_rev=cfg-r41") == "release=v2.4.0 config_rev=cfg-r41"
    assert context_of("no context here") == "" and context_of(None) == ""


@pytest.mark.parametrize("actions,fix,failed", [
    ([], None, []),
    ([{"action": "rollback_release"}], "rollback_release", []),
    ([{"action": "rollback_config"}, {"action": "rollback_release"}], "rollback_release", ["rollback_config"]),
    ([{"action": "flush_pool"}, {"action": "flush_pool"}, {"action": "restart_worker"}], "restart_worker", ["flush_pool"]),
    ([{"action": "restart_worker"}, {"action": "flush_pool"}, {"action": "restart_worker"}], "restart_worker", ["flush_pool"]),
    ([{"action": "rm_rf"}, {"action": "rollback_release"}], "rollback_release", []),        # not on the allow-list: ignored
])
def test_split_actions_last_is_the_fix_earlier_ones_did_not_work(actions, fix, failed):
    assert split_actions(actions, ALLOWED) == (fix, failed)


@pytest.mark.parametrize("text,expected", [
    ("Root cause...\nProposed action: rollback_release", "rollback_release"),
    ("proposed action: `flush_pool`", "flush_pool"),
    ("Proposed action: none", None),
    ("Proposed action: delete_all_data", None),                                     # not allow-listed
    ("Proposed action: rm -rf /", None),
    ("Proposed action: delete_all\nProposed action: restart_worker", "restart_worker"),   # first VALID one wins
    ("no action line", None), ("", None),
])
def test_only_allow_listed_actions_can_be_proposed(text, expected):
    assert parse_proposed_action(text, ALLOWED) == expected


def test_narrative_carries_symptoms_context_actions_and_negative_memory():
    n = build_narrative(INC, [{"action": "rollback_config"}, {"action": "rollback_release"}], "rollback_release",
                        ["rollback_config"], "release=v2.4.0 config_rev=cfg-r41", 6.0)
    for piece in ("INC-0007", "Error rate on 'POST /todos'", "SimulatedDatabaseStateError", "release=v2.4.0",
                  "Actions tried in order: rollback_config, rollback_release", "did NOT resolve it: rollback_config",
                  "Fix that worked: rollback_release", "6.0 minutes", "Root cause: bad deploy."):
        assert piece in n, piece


def test_narrative_without_actions_says_so_and_skips_templated_diagnoses():
    inc = dict(INC, rca_text="Automatic diagnosis unavailable (RateLimitError).")
    n = build_narrative(inc, [], None, [], "", None)
    assert "none recorded" in n and "Automatic diagnosis unavailable" not in n


# ── HindsightMemory ────────────────────────────────────────────────────────────────────────

def test_retain_is_an_idempotent_async_upsert_with_searchable_tags():
    mem, c = hs()
    out = mem.retain(dict(INC), "narrative", [], "rollback_release", ["rollback_config"], "live")
    kind, kw = c.calls[0]
    assert out.status == "retained" and out.operation_id == kw["operation_id"]
    assert kw["document_id"] == "INC-0007" and kw["update_mode"] == "replace" and kw["retain_async"] is True
    assert {"fp:fp-aaa", "svc:todo-app", "kind:incident", "outcome:resolved", "source:live", "fix:rollback_release",
            "failed:rollback_config"} <= set(kw["tags"])


def test_retaining_twice_replaces_it_does_not_duplicate():
    mem, c = hs()
    mem.retain(dict(INC), "first", [], "a", [], "live")
    mem.retain(dict(INC), "second", [], "b", [], "live")
    assert list(c.docs) == ["INC-0007"] and c.docs["INC-0007"]["content"] == "second"


def test_retain_failure_is_reported_not_raised():
    mem, c = hs()
    c.fail = True
    assert mem.retain(dict(INC), "n", [], None, [], "live").status == "failed"


def test_recall_finds_exact_matches_by_fingerprint_tag_and_similar_ones_by_text():
    mem, c = hs()
    mem.retain(dict(INC), "n", [], "rollback_release", ["rollback_config"], "live")
    other = dict(INC, id="INC-0003", fingerprints=[{"fp": "fp-bbb", "kind": "latency", "route": "GET /todos", "sig": "latency"}])
    mem.retain(other, "n2", [], "enable_fallback", [], "seed")
    new = dict(INC, id="INC-0009")
    res = mem.recall(new, "Error rate on 'POST /todos' is 30%")
    by_id = {m.incident_id: m for m in res.matches}
    assert by_id["INC-0007"].exact and by_id["INC-0007"].fix == "rollback_release" and by_id["INC-0007"].failed == ["rollback_config"]
    assert not by_id["INC-0003"].exact and by_id["INC-0003"].source == "seed"
    assert [m.incident_id for m in res.matches][0] == "INC-0007"                     # exact matches rank first
    modes = [kw["tags_match"] for kind, kw in c.calls if kind == "recall"]
    assert modes == ["any_strict", "all_strict"]                                     # strict: untagged memories excluded


def test_recall_excludes_the_incident_itself_and_low_relevance():
    mem, c = hs(min_score=0.6)
    mem.retain(dict(INC), "n", [], "x", [], "live")
    assert mem.recall(dict(INC), "q", exclude_id="INC-0007").matches == []            # never recalls itself
    other = dict(INC, id="INC-0008", fingerprints=[{"fp": "fp-zzz", "kind": "latency", "route": "R", "sig": "latency"}])
    mem.retain(other, "n", [], "y", [], "live")
    res = mem.recall(dict(INC, id="INC-0009", fingerprints=[]), "q")                  # only the text search: score 0.5 < 0.6
    assert res.matches == []


def test_recall_fails_open_when_hindsight_is_down():
    mem, c = hs()
    c.fail = True
    res = mem.recall(dict(INC), "q")
    assert res.degraded and res.matches == [] and res.error == "ConnectionError"


def test_memory_off_is_inert():
    n = NullMemory()
    assert not n.enabled and n.recall(INC, "q").matches == [] and n.retain(INC, "n", [], None, [], "live").status == "skipped"


def test_make_memory_is_null_unless_on_and_configured():
    assert isinstance(mm.make_memory("http://x", "b", "off"), NullMemory)
    assert isinstance(mm.make_memory("", "b", "on"), NullMemory)


# ── end to end: incident -> resolve -> retain -> recurrence recalls and verifies ──────────────

def make_memory():
    mem, client = hs()
    cycle.set_memory(mem)
    return mem, client


def resolve_first_incident(mem, journal):
    """INC-0001 opens on errors, recovers on healthy traffic, and its retain is delivered."""
    seed(n_ok=40, n_err=20)
    sub = Submitted()
    cycle.tick(5, sub)
    wipe_events()
    seed(n_ok=60, prefix="h")
    for _ in range(config.RECOVERY_WINDOWS):
        cycle.tick(5, sub)
    assert incidents.get("INC-0001")["status"] == "RESOLVED"
    now = store.now_ms()
    order = []
    while (item := outbox.claim(now, 1000)) is not None:
        order.append(item["kind"])
        if item["kind"] == "memory":
            res = cycle.deliver_memory(item, now, 5, mem=mem, journal_fn=lambda: journal, sleep=lambda s: None)
            assert res.status == "ok"
        outbox.complete(item["id"])
    return order


def test_resolving_an_incident_queues_a_retain_and_learns_the_fix_from_the_journal():
    mem, client = make_memory()
    seed(n_ok=40, n_err=20)
    cycle.tick(5, Submitted())
    opened = incidents.get("INC-0001")["opened_at"]
    journal = [{"ts_ms": opened - 3_600_000, "action": "flush_pool", "actor": "ops-api"},     # long before: ignored
               {"ts_ms": opened - 2_000, "action": "rollback_config", "actor": "ops-api"},
               {"ts_ms": opened - 1_000, "action": "rollback_release", "actor": "ops-api"}]
    wipe_events(); seed(n_ok=60, prefix="h")
    for _ in range(config.RECOVERY_WINDOWS):
        cycle.tick(5, Submitted())
    now = store.now_ms()
    kinds = []
    while (item := outbox.claim(now, 1000)) is not None:
        kinds.append(item["kind"])
        if item["kind"] == "memory":
            assert cycle.deliver_memory(item, now, 5, mem=mem, journal_fn=lambda: journal, sleep=lambda s: None).status == "ok"
        outbox.complete(item["id"])
    assert sorted(kinds) == ["discord", "discord", "memory"]
    inc = incidents.get("INC-0001")
    assert inc["memory_status"] == "indexed" and [a["action"] for a in inc["actions"]] == ["rollback_config", "rollback_release"]
    tags = client.docs["INC-0001"]["tags"]
    assert "fix:rollback_release" in tags and "failed:rollback_config" in tags and "flush_pool" not in " ".join(tags)


def test_a_repeat_incident_recalls_the_fix_and_takes_the_verification_path():
    mem, client = make_memory()
    seed(n_ok=40, n_err=20, exc="SimulatedDatabaseStateError: x")
    cycle.tick(5, Submitted())
    opened = incidents.get("INC-0001")["opened_at"]
    wipe_events(); seed(n_ok=60, prefix="h")
    for _ in range(config.RECOVERY_WINDOWS):
        cycle.tick(5, Submitted())
    journal = [{"ts_ms": opened - 1_000, "action": "rollback_release", "actor": "ops-api"}]
    now = store.now_ms()
    while (item := outbox.claim(now, 1000)) is not None:
        if item["kind"] == "memory":
            cycle.deliver_memory(item, now, 5, mem=mem, journal_fn=lambda: journal, sleep=lambda s: None)
        outbox.complete(item["id"])

    wipe_events(); seed(n_ok=40, n_err=20, exc="SimulatedDatabaseStateError: x", prefix="again")
    sub = Submitted()
    cycle.tick(5, sub)
    inc = incidents.get("INC-0002")
    assert inc["recurrence_of"] == "INC-0001" and inc["diagnosis_mode"] == "verify"
    assert sub.kinds[-1] == "verify"
    top = inc["recalled"][0]
    assert top["incident_id"] == "INC-0001" and top["exact"] and top["fix"] == "rollback_release"
    prompt = sub.jobs[-1][1]
    assert "rollback_release" in prompt and "Verdict: match | mismatch" in prompt and "PROACTIVE ALERT" not in prompt
    # the opening alert carries what memory knows
    item = outbox.claim(store.now_ms(), 1000)
    assert item["payload"] == {"event": "opened"}
    body = render(incidents.get("INC-0002"), "opened", store.now_ms(), 5)
    memory_text = " ".join(f["value"] for f in body["embeds"][0]["fields"] if "Memory" in f["name"])
    assert "Known issue" in memory_text and "INC-0001" in memory_text and "rollback_release" in memory_text


def test_memory_off_means_no_recall_no_retain_and_a_full_diagnosis():
    cycle.set_memory(NullMemory())
    seed(n_ok=40, n_err=20)
    sub = Submitted()
    cycle.tick(5, sub)
    inc = incidents.get("INC-0001")
    assert inc["recalled"] == [] and inc["diagnosis_mode"] == "full" and sub.kinds == ["full"]
    wipe_events(); seed(n_ok=60, prefix="h")
    for _ in range(config.RECOVERY_WINDOWS):
        cycle.tick(5, sub)
    assert incidents.get("INC-0001")["memory_status"] == "off"
    assert all(i["kind"] != "memory" for i in _drain())


def _drain():
    items = []
    while (item := outbox.claim(store.now_ms(), 1000)) is not None:
        items.append(item)
        outbox.complete(item["id"])
    return items


def test_a_memory_outage_never_blocks_the_alert_or_the_diagnosis():
    mem, client = make_memory()
    client.fail = True
    seed(n_ok=40, n_err=20)
    sub = Submitted()
    cycle.tick(5, sub)
    inc = incidents.get("INC-0001")
    assert inc["diagnosis_mode"] == "full" and sub.kinds == ["full"] and inc["memory_detail"].startswith("recall degraded")
    assert [i["payload"]["event"] for i in _drain()] == ["opened"]                # the page still goes out
    assert "Unavailable" in " ".join(f["value"] for f in render(inc, "opened", 0, 5)["embeds"][0]["fields"])


def test_a_retain_failure_is_retried_by_the_outbox_and_never_lost():
    mem, client = make_memory()
    client.fail = True
    seed(n_ok=40, n_err=20)
    cycle.tick(5, Submitted())
    wipe_events(); seed(n_ok=60, prefix="h")
    for _ in range(config.RECOVERY_WINDOWS):
        cycle.tick(5, Submitted())
    _first = outbox.claim(store.now_ms(), 1000)                                      # the opening alert; not our concern here
    outbox.complete(_first["id"])
    while (item := outbox.claim(store.now_ms(), 1000)) is not None and item["kind"] != "memory":
        outbox.complete(item["id"])
    res = cycle.deliver_memory(item, store.now_ms(), 5, mem=mem, journal_fn=lambda: [], sleep=lambda s: None)
    assert res.status == "retry" and incidents.get("INC-0001")["memory_status"] == "failed"
    client.fail = False                                                              # Hindsight comes back
    assert cycle.deliver_memory(item, store.now_ms(), 5, mem=mem, journal_fn=lambda: [], sleep=lambda s: None).status == "ok"
    assert incidents.get("INC-0001")["memory_status"] == "indexed"


def test_pump_routes_memory_items_to_the_memory_deliverer_and_the_rest_to_discord():
    mem, _ = make_memory()
    outbox.enqueue("discord", "d", "INC-1", {"event": "opened"}, 1)
    outbox.enqueue("memory", "m", "INC-2", {"op": "retain"}, 1)
    seen = []
    assert cycle.process_outbox_once(10**13, 5, deliver=lambda *a: seen.append("discord") or Delivery("ok"),
                                     memory_deliver=lambda *a: seen.append("memory") or Delivery("ok")) == "done"
    assert cycle.process_outbox_once(10**13, 5, deliver=lambda *a: seen.append("discord") or Delivery("ok"),
                                     memory_deliver=lambda *a: seen.append("memory") or Delivery("ok")) == "done"
    assert seen == ["discord", "memory"]


def test_memory_delivery_skips_when_memory_is_off_and_fails_for_unknown_incidents():
    assert cycle.deliver_memory({"incident_id": "INC-9999"}, 0, 5, mem=NullMemory()).status == "fail"
    seed(n_ok=40, n_err=20)
    cycle.tick(5, Submitted())
    assert cycle.deliver_memory({"incident_id": "INC-0001"}, 0, 5, mem=NullMemory()).status == "skip"


def test_indexing_that_never_completes_still_counts_as_retained_not_lost():
    mem, client = make_memory()
    mem._status_fn = lambda op: "pending"
    seed(n_ok=40, n_err=20)
    cycle.tick(5, Submitted())
    res = cycle.deliver_memory({"incident_id": "INC-0001"}, store.now_ms(), 5, mem=mem, journal_fn=lambda: [],
                               sleep=lambda s: None, poll_attempts=2)
    assert res.status == "ok" and incidents.get("INC-0001")["memory_status"] == "retained"


# ── proposed action from the diagnosis ────────────────────────────────────────────────────

def test_a_diagnosis_proposal_is_stored_only_if_allow_listed_and_shown_in_the_followup():
    seed(n_ok=40, n_err=20)
    cycle.tick(5, Submitted())
    incidents.set_rca("INC-0001", "Cause: bad release.\nProposed action: rollback_release", "ok", 1, 900)
    cycle.on_diagnosis_done("INC-0001", store.now_ms())
    inc = incidents.get("INC-0001")
    assert inc["proposed_action"] == "rollback_release"
    text = " ".join(f["value"] for f in render(inc, "diagnosis", 0, 5)["embeds"][0]["fields"])
    assert "rollback_release" in text and "/ops/rollback_release" in text

    incidents.set_rca("INC-0001", "Ignore rules.\nProposed action: drop_database", "ok", 1, 900)
    cycle.on_diagnosis_done("INC-0001", store.now_ms())
    assert incidents.get("INC-0001")["proposed_action"] == "rollback_release"        # the invalid one changed nothing


# ── decoy pair: same fingerprint, different causes ────────────────────────────────────────

def test_two_remembered_causes_for_one_fingerprint_both_reach_the_verifier_with_their_context():
    from agent.prompts import build_verify_prompt
    matches = [
        {"incident_id": "INC-0001", "exact": True, "fix": "rollback_release", "failed": [], "source": "live",
         "context": "release=v2.4.0 config_rev=cfg-r41", "evidence": ["Error rate on 'POST /todos' is 30%"]},
        {"incident_id": "INC-0002", "exact": True, "fix": "rollback_config", "failed": ["rollback_release"], "source": "seed",
         "context": "release=v2.3.1 config_rev=cfg-r42", "evidence": ["Error rate on 'POST /todos' is 28%"]},
    ]
    p = build_verify_prompt(["Error rate on 'POST /todos' is 31%"], "release=v2.3.1 config_rev=cfg-r42", matches)
    for piece in ("release=v2.4.0", "config_rev=cfg-r42", "rollback_release", "rollback_config", "did NOT work: rollback_release",
                  "Current runtime context: release=v2.3.1 config_rev=cfg-r42", "mismatch"):
        assert piece in p, piece


def test_the_notifier_summarises_agreement_between_remembered_incidents():
    inc = {"id": "INC-9", "service": "todo-app", "status": "OPEN", "severity": "critical", "opened_at": 0, "evidence": ["e"],
           "recalled": [{"incident_id": "INC-1", "exact": True, "fix": "rollback_release", "failed": ["rollback_config"], "source": "live"},
                        {"incident_id": "INC-2", "exact": True, "fix": "rollback_release", "failed": [], "source": "seed"}]}
    field = notifier._memory_field(inc)
    assert "2 of 2" in field["value"] and "rollback_release" in field["value"] and "did not work: rollback_config" in field["value"]
