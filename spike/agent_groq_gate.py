"""Gate O2: does our ADK agent call tools and finish reliably on the chosen LLM?

Runs the real production pieces (agent.agent.root_agent, agent.prompts.build_alert_prompt, agent.detect) against a
scratch SQLite database seeded with five realistic incidents, N times in rotation. Nothing here is imported by the app.

    docker build -t copilot-gate ./Copilot
    docker run --rm -e GROQ_API_KEY -e AGENT_MODEL=groq/openai/gpt-oss-120b -e GATE_RUNS=20 \
        -v "$PWD/spike:/spike" copilot-gate python /spike/agent_groq_gate.py

Each run is classified:
  ok               finished, made >= 1 tool call, produced a non-empty answer
  no_tool_call     answered without calling any tool (violates "always call tools first")
  empty            finished with no text
  function_error   provider rejected/failed a tool call (the failure the event guideline warns about)
  rate_limit       provider quota; the run is retried after a wait and NOT counted against reliability
  other_error      anything else
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter

os.environ.setdefault("COPILOT_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="gate-"), "gate.db"))
os.environ.setdefault("MIN_REQUESTS", "10")
os.environ.setdefault("WATCHER_INTERVAL_MINUTES", "5")

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai.types import Content, Part  # noqa: E402

from agent import tools  # noqa: E402
from agent.agent import root_agent  # noqa: E402
from agent.detect import build_summary, evaluate  # noqa: E402
from agent.prompts import build_alert_prompt  # noqa: E402
from agent.thresholds import thresholds  # noqa: E402
from db import events as store  # noqa: E402
from db.database import get_db_connection  # noqa: E402

RUNS = int(os.getenv("GATE_RUNS", "20"))
PAUSE_S = float(os.getenv("GATE_PAUSE_S", "25"))
RATE_LIMIT_WAIT_S = float(os.getenv("GATE_RATE_WAIT_S", "50"))
SERVICE = "todo-app"
CTX_OK = "release=v2.3.1 config_rev=cfg-r41 pool=0/10 leaked_mb=0 fallback=False"


def ev(i, route, status, dur, ctx=CTX_OK, exc=None):
    level = "ERROR" if status >= 500 else "INFO"
    msg = f"{route} -> {status}" + (f" {exc}" if exc else "") + f" | {ctx}"
    return {"id": f"g{i}-{route}-{status}-{dur}", "ts": store.now_ms(), "level": level, "route": route,
            "status": status, "duration_ms": float(dur), "message": msg, "exception": exc}


def healthy_get(n=40):
    return [ev(f"gg{i}", "GET /todos", 200, 4 + i % 5) for i in range(n)]


SCENARIOS = {
    "bad_deploy": dict(
        hints=["deploy", "release", "v2.4.0", "rollback"],
        build=lambda: healthy_get() + [
            ev(f"p{i}", "POST /todos", 500 if i % 3 == 0 else 200, 9, "release=v2.4.0 config_rev=cfg-r41 pool=0/10 leaked_mb=0 fallback=False",
               "SimulatedDatabaseStateError: Simulated database state error" if i % 3 == 0 else None) for i in range(60)]),
    "bad_config": dict(
        hints=["config", "cfg-r42", "configuration"],
        build=lambda: healthy_get() + [
            ev(f"p{i}", "POST /todos", 500 if i % 3 == 0 else 200, 9, "release=v2.3.1 config_rev=cfg-r42 pool=0/10 leaked_mb=0 fallback=False",
               "SimulatedDatabaseStateError: Simulated database state error" if i % 3 == 0 else None) for i in range(60)]),
    "pool_exhausted": dict(
        hints=["pool", "connection"],
        build=lambda: [ev(f"a{i}", "GET /todos", 200, 5, "release=v2.3.1 config_rev=cfg-r41 pool=%d/10 leaked_mb=0 fallback=False" % i) for i in range(10)]
        + [ev(f"b{i}", route, 503, 2003, "release=v2.3.1 config_rev=cfg-r41 pool=10/10 leaked_mb=0 fallback=False", "PoolExhausted: connection pool exhausted")
           for i, route in enumerate(["GET /todos", "POST /todos", "PUT /todos/{todo_id}", "DELETE /todos/{todo_id}"] * 8)]),
    "slow_downstream": dict(
        hints=["slow", "latency", "downstream", "dependency", "timeout", "fallback"],
        build=lambda: healthy_get() + [ev(f"d{i}", "DELETE /todos/{todo_id}", 200, 2000 + 250 * (i % 12)) for i in range(30)]),
    "memory_leak": dict(
        hints=["memory", "leak", "restart"],
        build=lambda: healthy_get(20) + [
            ev(f"m{i}", "PUT /todos/{todo_id}", 500 if i >= 40 else 200, 30 + i * 15,
               "release=v2.3.1 config_rev=cfg-r41 pool=0/10 leaked_mb=%d fallback=False" % (i + 1),
               "WorkerOutOfMemory: worker memory limit exceeded" if i >= 40 else None) for i in range(48)]),
}


def seed(name: str) -> None:
    conn = get_db_connection()
    conn.execute("DELETE FROM events")
    conn.commit()
    conn.close()
    store.insert_events(SERVICE, SCENARIOS[name]["build"]())


def alert_prompt() -> str:
    w = thresholds.interval_minutes
    health, err = tools.get_service_health(), tools.get_error_rate(w)
    lat, slow = tools.get_latency(w), tools.get_slow_requests(thresholds.slow_request_min_ms, w)
    logs = tools.get_recent_logs(",".join(thresholds.log_keywords), thresholds.log_scan_limit, w)
    breaches = evaluate(health, err, lat, slow, logs, thresholds) or ["Service behaviour changed (no threshold detail available)"]
    return build_alert_prompt(breaches, build_summary(health, err, lat, slow, logs))


def classify_error(exc: Exception) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if "ratelimit" in text or "429" in text or "rate limit" in text or "quota" in text:
        return "rate_limit"
    if "tool_use_failed" in text or "failed to call a function" in text or "function" in text and "invalid" in text:
        return "function_error"
    return "other_error"


async def one_run(idx: int, scenario: str) -> dict:
    seed(scenario)
    prompt = alert_prompt()
    svc = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name="gate", session_service=svc)
    sid = f"gate-{idx}"
    await svc.create_session(app_name="gate", user_id="gate", session_id=sid)
    calls, rounds, answer, tokens = [], 0, "", 0
    t0 = time.perf_counter()
    async for event in runner.run_async(user_id="gate", session_id=sid,
                                        new_message=Content(role="user", parts=[Part(text=prompt)])):
        fcs = event.get_function_calls() if hasattr(event, "get_function_calls") else []
        if fcs:
            rounds += 1
            calls += [fc.name for fc in fcs]
        um = getattr(event, "usage_metadata", None)
        if um and getattr(um, "total_token_count", None):
            tokens += um.total_token_count
        if event.is_final_response() and event.content and event.content.parts:
            answer += "".join(p.text or "" for p in event.content.parts)
    dur = time.perf_counter() - t0
    low = answer.lower()
    status = "ok" if (calls and answer.strip()) else ("no_tool_call" if not calls else "empty")
    return {"status": status, "tool_calls": calls, "rounds": rounds, "seconds": round(dur, 1), "tokens": tokens,
            "answer_chars": len(answer), "cause_hint": any(h in low for h in SCENARIOS[scenario]["hints"]),
            "answer": answer.strip().replace("\n", " ")[:260]}


async def main() -> int:
    names = list(SCENARIOS)
    print(f"gate: model={os.getenv('AGENT_MODEL')} runs={RUNS} pause={PAUSE_S}s", flush=True)
    results = []
    for i in range(RUNS):
        scenario = names[i % len(names)]
        attempt, rate_waits, res = 0, 0, None
        while attempt < 4:
            attempt += 1
            try:
                res = await one_run(i, scenario)
                break
            except Exception as exc:  # noqa: BLE001 - classify and report every failure mode
                kind = classify_error(exc)
                if kind == "rate_limit" and attempt < 4:
                    rate_waits += 1
                    print(f"  run {i:2d} [{scenario}] rate limited, waiting {RATE_LIMIT_WAIT_S:.0f}s", flush=True)
                    await asyncio.sleep(RATE_LIMIT_WAIT_S)
                    continue
                res = {"status": kind, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                break
        res.update(run=i, scenario=scenario, rate_limit_waits=rate_waits)
        results.append(res)
        print(f"  run {i:2d} [{scenario:15s}] {res['status']:14s} calls={res.get('tool_calls', [])} "
              f"{res.get('seconds', '-')}s tokens={res.get('tokens', '-')} hint={res.get('cause_hint', '-')}", flush=True)
        if res["status"] not in ("ok",):
            print(f"      detail: {res.get('error') or res.get('answer')}", flush=True)
        await asyncio.sleep(PAUSE_S)

    counts = Counter(r["status"] for r in results)
    ok = counts["ok"]
    print("\n== SUMMARY", flush=True)
    print(json.dumps({
        "model": os.getenv("AGENT_MODEL"), "runs": len(results), "status_counts": dict(counts),
        "completion_rate": round(ok / len(results), 2),
        "cause_hint_rate_among_ok": round(sum(1 for r in results if r["status"] == "ok" and r["cause_hint"]) / max(ok, 1), 2),
        "avg_tool_calls": round(sum(len(r.get("tool_calls", [])) for r in results if r["status"] == "ok") / max(ok, 1), 1),
        "avg_seconds": round(sum(r.get("seconds", 0) for r in results if r["status"] == "ok") / max(ok, 1), 1),
        "avg_tokens": round(sum(r.get("tokens", 0) for r in results if r["status"] == "ok") / max(ok, 1)),
        "rate_limit_waits_total": sum(r["rate_limit_waits"] for r in results),
        "tools_used": dict(Counter(t for r in results for t in r.get("tool_calls", []))),
        "by_scenario_ok": {n: sum(1 for r in results if r["scenario"] == n and r["status"] == "ok") for n in names},
    }, indent=2), flush=True)
    return 0 if ok / len(results) >= 0.9 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
