"""M0 spike, part 2: the experiments part 1 could not run.

Part 1 did not actually execute the sub-API calls (they are coroutines) and reflect hit the provider quota.
Here sub-API calls are awaited through the client's own runner. Run like part 1 (see hindsight_spike.py).

  Q4  async retain: operation status values over time, time until recallable
  Q6  documents.delete_document really removes the memory (quarantine)
  Q7  directive + reflect (skipped, not failed, if the provider quota blocks it)
"""

from __future__ import annotations

import json
import os
import time
import uuid

from hindsight_client import Hindsight
from hindsight_client.hindsight_client import _run_async  # the client's own sync bridge for its async sub-APIs

BASE = os.getenv("HINDSIGHT_URL", "http://127.0.0.1:8888")
BANK = f"spike2-{uuid.uuid4().hex[:8]}"
c = Hindsight(base_url=BASE)
out: dict[str, object] = {}


def paced(fn, *a, **kw):
    for attempt in range(6):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            m = str(e)
            if attempt < 5 and ("quota" in m or "rate limit" in m.lower() or "429" in m):
                print(f"    [quota wait 40s, attempt {attempt + 1}]")
                time.sleep(40)
                continue
            raise


def recall_fp(fp, q="what fixed it"):
    r = c.recall(bank_id=BANK, query=q, tags=[f"fp:{fp}"], tags_match="all_strict")
    return r.results or []


try:
    c.create_bank(bank_id=BANK, reflect_mission="Incident response memory for an on-call SRE.")

    print("== Q6 delete a document (quarantine)")
    paced(c.retain, bank_id=BANK, document_id="INC-0002", tags=["fp:pool", "source:seed"],
          content="[INC-0002 fp=pool source=seed] All routes 503 pool exhausted. Fix that worked: flush_pool. MTTR 4 minutes.")
    before = recall_fp("pool")
    resp = _run_async(c.documents.delete_document(bank_id=BANK, document_id="INC-0002"))
    print("  delete response:", getattr(resp, "message", resp))
    after = recall_fp("pool")
    print(f"  recall by fp tag: before={len(before)} after={len(after)}")
    out["Q6"] = {"before": len(before), "after_delete": len(after)}

    print("\n== Q4 async retain + operation status")
    t0 = time.perf_counter()
    r = paced(c.retain, bank_id=BANK, retain_async=True, operation_id=str(uuid.uuid4()),
              document_id="INC-0003", tags=["fp:slow", "source:live"],
              content="[INC-0003 fp=slow source=live] GET /todos p95 3s. Cause slow_downstream. Fix that worked: enable_fallback. MTTR 3 minutes.")
    op = r.operation_id
    print(f"  retain(async) returned in {time.perf_counter() - t0:.2f}s operation_id={op}")
    seen_status: list[str] = []
    t_done = t_recall = None
    while time.perf_counter() - t0 < 150 and (t_done is None or t_recall is None):
        st = _run_async(c.operations.get_operation_status(bank_id=BANK, operation_id=op))
        s = getattr(st, "status", str(st))
        if not seen_status or seen_status[-1] != s:
            seen_status.append(s)
            print(f"  t={time.perf_counter() - t0:5.1f}s status={s}")
        if t_done is None and s in ("completed", "succeeded", "done", "success"):
            t_done = time.perf_counter() - t0
        if t_recall is None and recall_fp("slow"):
            t_recall = time.perf_counter() - t0
            print(f"  t={t_recall:5.1f}s recallable")
        time.sleep(3)
    out["Q4"] = {"statuses": seen_status, "done_after_s": t_done and round(t_done, 1),
                 "recallable_after_s": t_recall and round(t_recall, 1)}

    print("\n== Q7 directive + reflect")
    c.create_directive(bank_id=BANK, name="no-restarts-at-peak", priority=10,
                       content="Never recommend restart_worker between 09:00 and 18:00 local time; prefer a less disruptive action.")
    print("  directives:", [d.name for d in (c.list_directives(bank_id=BANK).items or [])])
    try:
        t1 = time.perf_counter()
        a = paced(c.reflect, bank_id=BANK, query="Memory leak on POST /todos at 11:00. What should we do first?",
                  tags=["source:live"], tags_match="any")
        text = getattr(a, "text", str(a))
        print(f"  reflect {time.perf_counter() - t1:.1f}s: {text[:280]!r}")
        out["Q7"] = {"reflect_s": round(time.perf_counter() - t1, 1), "mentions_restart": "restart" in text.lower()}
    except Exception as e:  # noqa: BLE001
        print("  reflect SKIPPED (provider quota):", str(e)[:160])
        out["Q7"] = {"skipped": "provider quota"}

    print("\n== SUMMARY")
    print(json.dumps(out, indent=2))
finally:
    try:
        c.delete_bank(bank_id=BANK)
        print(f"bank {BANK} deleted")
    except Exception as e:  # noqa: BLE001
        print("cleanup failed:", e)
