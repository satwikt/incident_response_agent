"""L01: recall hits for one document merge their fix/failed tags. L02: an explicit empty recall means no precedent."""
from types import SimpleNamespace as NS

from agent import memory, rca_agent


def _hit(doc, final, tags):
    return NS(document_id=doc, scores=NS(final=final), tags=tags, text="t")


def _collect(*calls):
    mem = memory.HindsightMemory.__new__(memory.HindsightMemory)
    mem.min_score = 0.0
    found = {}
    for exact, hits in calls:
        mem._collect(NS(results=hits), found, exact=exact, exclude_id=None)
    return found["INC-1"]


def test_same_document_twice_keeps_fix_and_failed_tags():
    rec = _collect((True, [_hit("INC-1", 0.9, ["fix:flush_pool"]), _hit("INC-1", 0.5, ["failed:restart_worker"])]))
    assert rec.fix == "flush_pool" and rec.failed == ["restart_worker"]


def test_higher_scored_later_hit_does_not_drop_earlier_fix():
    rec = _collect((False, [_hit("INC-1", 0.4, ["fix:flush_pool"])]), (True, [_hit("INC-1", 0.8, ["failed:x"])]))
    assert rec.fix == "flush_pool" and rec.failed == ["x"] and rec.exact


def test_explicit_empty_recall_selects_ultra_even_with_stale_incident_recalled():
    inc = {"recalled": [{"incident_id": "INC-0", "context": "a"}], "evidence": []}
    assert rca_agent.select_model_tier(inc, []) == "ultra"
