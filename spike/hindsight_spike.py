"""M0 spike: answer the open Hindsight questions against a real local instance.

Run (from the repo root, with the spike container up):

    docker run --rm --network host -v "$PWD/spike:/w" -w /w python:3.13-slim \
        sh -c "pip install -q hindsight-client && python hindsight_spike.py"

Each experiment prints measured facts, not opinions. Nothing here is imported by the app.
The bank it creates is deleted at the end.

Questions answered (numbers match docs/DESIGN.md section 3):
  Q1  retain -> recall round trip works, and how long each takes
  Q2  tags + tags_match filter recall (exact fingerprint lookups)
  Q3  document_id upsert: retaining the same id twice replaces, not duplicates
  Q4  async retain: operation status can be polled, and how long until recallable
  Q5  min_scores / result scores are exposed for a relevance threshold
  Q6  documents.delete_document removes a memory (quarantine)
  Q7  directives can hold operator rules and reflect obeys them
  Q8  reflect returns a usable synthesis with tags filter
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

from hindsight_client import Hindsight

BASE = os.getenv("HINDSIGHT_URL", "http://127.0.0.1:8888")
BANK = f"spike-{uuid.uuid4().hex[:8]}"
client = Hindsight(base_url=BASE)
results: dict[str, object] = {}


def timed(label, fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    dt = time.perf_counter() - t0
    print(f"  {label}: {dt:.2f}s")
    return out, dt


def paced(fn, *a, **kw):
    """Call fn, waiting out provider quota errors (Groq free tier: ~8000 tokens/min, ~4000 per retain)."""
    for attempt in range(6):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001 - report and retry only on quota-shaped errors
            msg = str(e)
            if attempt < 5 and ("quota" in msg or "rate limit" in msg.lower() or "429" in msg):
                print(f"    [quota wait 35s, attempt {attempt + 1}]")
                time.sleep(35)
                continue
            raise


def texts(resp) -> list[str]:
    """Best-effort extraction of result texts from a recall response."""
    items = getattr(resp, "results", None) or []
    return [getattr(i, "text", str(i)) for i in items]


def show(title):
    print(f"\n== {title}")


INC1 = (
    "[INC-0001 fp=fp-bad-deploy-post-todos source=live outcome=resolved] "
    "POST /todos returned 500 for ~30% of requests. Cause: bad_deploy, release v2.4.0. "
    "Fix that worked: rollback_release. Tried rollback_config first, no effect. MTTR 6 minutes."
)
INC2 = (
    "[INC-0002 fp=fp-pool-exhausted source=live outcome=resolved] "
    "All routes returned 503 connection pool exhausted. Cause: connection leak on POST /todos. "
    "Fix that worked: flush_pool. restart_worker only helped for one minute. MTTR 4 minutes."
)

try:
    show("Q1 retain -> recall")
    client.create_bank(bank_id=BANK, reflect_mission="Incident response memory for an on-call SRE.")
    _, t_retain = timed(
        "retain INC-0001 (sync, incl. any quota waits)",
        paced, client.retain, bank_id=BANK, content=INC1, document_id="INC-0001",
        tags=["fp:fp-bad-deploy-post-todos", "source:live"], metadata={"incident_id": "INC-0001"},
    )
    r, t_recall = timed("recall 'POST /todos 500 errors after deploy'", client.recall, bank_id=BANK,
                        query="POST /todos returning 500 errors after a deploy")
    hits = texts(r)
    print(f"  results={len(hits)} first={hits[0][:110] if hits else None!r}")
    results["Q1"] = {"retain_s": round(t_retain, 2), "recall_s": round(t_recall, 2), "hits": len(hits)}

    show("Q2 tag filtering")
    paced(client.retain, bank_id=BANK, content=INC2, document_id="INC-0002",
                  tags=["fp:fp-pool-exhausted", "source:seed"], metadata={"incident_id": "INC-0002"})
    all_items = client.recall(bank_id=BANK, query="which fix worked").results or []
    fp_items = client.recall(bank_id=BANK, query="which fix worked",
                             tags=["fp:fp-pool-exhausted"], tags_match="all_strict").results or []
    print(f"  unfiltered={len(all_items)} filtered(fp-pool-exhausted)={len(fp_items)}")
    for it in fp_items:
        print(f"    tags={it.tags} doc={it.document_id} text={it.text[:70]!r}")
    isolates = bool(fp_items) and all("fp:fp-pool-exhausted" in (it.tags or []) for it in fp_items)
    other = [it for it in all_items if "fp:fp-pool-exhausted" not in (it.tags or [])]
    print(f"  every filtered item carries the tag: {isolates}; unfiltered items WITHOUT the tag: {len(other)}")
    results["Q2"] = {"unfiltered": len(all_items), "filtered": len(fp_items), "filter_isolates": isolates,
                     "unfiltered_without_tag": len(other)}

    show("Q3 document_id upsert")
    before = client.list_memories(bank_id=BANK, limit=100)
    n_before = len(getattr(before, "items", None) or getattr(before, "memories", None) or [])
    paced(client.retain, bank_id=BANK, content=INC1.replace("MTTR 6 minutes", "MTTR 5 minutes (corrected)"),
                  document_id="INC-0001", update_mode="replace",
                  tags=["fp:fp-bad-deploy-post-todos", "source:live"])
    after = client.list_memories(bank_id=BANK, limit=100)
    n_after = len(getattr(after, "items", None) or getattr(after, "memories", None) or [])
    rec = " ".join(texts(client.recall(bank_id=BANK, query="INC-0001 MTTR",
                                        tags=["fp:fp-bad-deploy-post-todos"], tags_match="all_strict")))
    print(f"  memories before={n_before} after={n_after}; corrected text present={'corrected' in rec}; "
          f"old text still present={'MTTR 6 minutes' in rec}")
    results["Q3"] = {"before": n_before, "after": n_after, "corrected": "corrected" in rec,
                     "stale_present": "MTTR 6 minutes" in rec}

    show("Q4 async retain + operation status")
    t0 = time.perf_counter()
    resp = paced(client.retain, bank_id=BANK, retain_async=True, operation_id=str(uuid.uuid4()),
                         document_id="INC-0003", tags=["fp:fp-slow-downstream", "source:live"],
                         content="[INC-0003 fp=fp-slow-downstream source=live outcome=resolved] GET /todos p95 "
                                 "latency 3s. Cause: slow_downstream. Fix that worked: enable_fallback. MTTR 3 minutes.")
    op_id = getattr(resp, "operation_id", None)
    print(f"  retain(async) returned in {time.perf_counter() - t0:.2f}s, operation_id={op_id}")
    seen, waited = False, 0.0
    while waited < 60 and not seen:
        got = texts(client.recall(bank_id=BANK, query="slow downstream fallback",
                                  tags=["fp:fp-slow-downstream"], tags_match="all_strict"))
        seen = bool(got)
        if not seen:
            time.sleep(1); waited += 1
    print(f"  recallable after ~{time.perf_counter() - t0:.1f}s (seen={seen})")
    status = None
    if op_id:
        try:
            status = client.operations.get_operation_status(bank_id=BANK, operation_id=op_id)
            print("  operation status:", getattr(status, "status", status))
        except Exception as e:  # signature may differ; report it, do not guess
            print("  get_operation_status failed:", type(e).__name__, str(e)[:120])
    results["Q4"] = {"recallable_after_s": round(time.perf_counter() - t0, 1), "seen": seen, "operation_id": bool(op_id)}

    show("Q5 scores and min_scores")
    r = client.recall(bank_id=BANK, query="completely unrelated: how to bake sourdough bread")
    items = getattr(r, "results", None) or []
    print(f"  unrelated query returned {len(items)} items")
    for it in items[:3]:
        print(f"    scores={getattr(it, 'scores', None)} tags={getattr(it, 'tags', None)} "
              f"document_id={getattr(it, 'document_id', None)}")
    good = client.recall(bank_id=BANK, query="POST /todos 500 errors after a deploy",
                         tags=["fp:fp-bad-deploy-post-todos"], tags_match="all_strict")
    for it in (getattr(good, "results", None) or [])[:3]:
        print(f"    relevant: scores={getattr(it, 'scores', None)} metadata={getattr(it, 'metadata', None)}")
    results["Q5"] = {"unrelated_hits": len(items),
                     "relevant_has_scores": bool(getattr(good, "results", None)) and
                     getattr(good.results[0], "scores", None) is not None}

    show("Q6 delete a document (quarantine)")
    client.documents.delete_document(bank_id=BANK, document_id="INC-0002")
    after_del = texts(client.recall(bank_id=BANK, query="connection pool exhausted flush_pool",
                                    tags=["fp:fp-pool-exhausted"], tags_match="all_strict"))
    print(f"  recall for deleted INC-0002 now returns {len(after_del)} items")
    results["Q6"] = {"after_delete_hits": len(after_del)}

    show("Q7 directives + reflect")
    client.create_directive(bank_id=BANK, name="no-restarts-at-peak", priority=10,
                            content="Never recommend restart_worker between 09:00 and 18:00 local time; "
                                    "prefer a less disruptive action.")
    ans, t_ref = timed("reflect", paced, client.reflect, bank_id=BANK,
                       query="Memory leak on POST /todos at 11:00. What should we do first?",
                       tags=["source:live"], tags_match="any")
    text = getattr(ans, "text", None) or str(ans)
    print("  answer:", text[:300].replace("\n", " "))
    results["Q7_8"] = {"reflect_s": round(t_ref, 2), "mentions_restart": "restart" in text.lower()}

    print("\n== SUMMARY (paste into docs/DESIGN.md section 11.1)")
    print(json.dumps(results, indent=2))
finally:
    try:
        client.delete_bank(bank_id=BANK)
        print(f"\nbank {BANK} deleted")
    except Exception as e:
        print("cleanup failed:", e, file=sys.stderr)
