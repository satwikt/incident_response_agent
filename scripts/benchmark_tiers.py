"""Measure what the Nemotron tiers actually do on the live stack, and write docs/benchmark.md + docs/benchmark.json.

One replicate = a clean reset, then three incidents in a row on the same running stack:

  1. new_incident      payment_failure, empty memory                      expected tier: ultra
  2. context_shift     db_pool_exhaustion (503), memory holds incident 1  expected tier: super
  3. repeat_after_fix  payment_failure again, memory holds 1 and 2        expected tier: nano

Each incident is resolved with "Fix Worked: disable_fault" and the script waits for Hindsight to index it before the next
fault. Numbers are medians over --runs replicates. Nothing is estimated: every value comes from the stack's own API
(incident timestamps, the chat reply's tier/model/llm_calls/token counts) or the script's wall clock.

    python scripts/benchmark_tiers.py                       # full run, 3 replicates (about 1 hour; costs LLM calls)
    python scripts/benchmark_tiers.py --runs 1              # quick check
    python scripts/benchmark_tiers.py --render              # rebuild benchmark.md from an existing benchmark.json

Needs the stack up with MEMORY_MODE=on and a working inference key. If CHAOS_KEY is set in the environment (or in the repo's .env) it is sent as the
X-Chaos-Key header. The reset between replicates wipes copilot and Hindsight data (scripts/reset_demo.ps1 or .sh).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWN_FIX = "disable_fault"

SCENARIOS = [
    # name, fault, chat question, expected tier
    ("new_incident", "payment_failure", "why is checkout failing?", "ultra"),
    ("context_shift", "db_pool_exhaustion",
     "checkout is returning 503 errors, what is the root cause and how do we fix it?", "super"),
    ("repeat_after_fix", "payment_failure", "why is checkout failing?", "nano"),
]
TITLES = {"new_incident": "New incident (empty memory)", "context_shift": "Context shift (same service, new cause)",
          "repeat_after_fix": "Repeat after Fix Worked"}


# ── pure helpers (unit-tested) ────────────────────────────────────────────────────────────────

def parse_action(text: str) -> str | None:
    m = re.search(r"Proposed action:\s*[*`_]*([a-z][a-z0-9_]*)", text or "", re.IGNORECASE)
    return m.group(1).lower() if m else None


def median(values: list) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(nums), 2) if nums else None


def summarize(rows: list[dict]) -> list[dict]:
    """One summary per scenario: tier hit-rate, the models seen, and the median of every numeric field."""
    out = []
    for name, _fault, _q, expected in SCENARIOS:
        rs = [r for r in rows if r.get("scenario") == name]
        if not rs:
            continue
        out.append({
            "scenario": name, "expected_tier": expected, "runs": len(rs),
            "tier_hits": sum(1 for r in rs if r.get("tier") == expected),
            "tiers_seen": sorted({str(r.get("tier")) for r in rs}),
            "models": sorted({str(r.get("model")) for r in rs if r.get("model")}),
            "action_hits": sum(1 for r in rs if r.get("action") == KNOWN_FIX),
            **{k: median([r.get(k) for r in rs]) for k in (
                "detection_s", "auto_diag_s", "auto_calls", "auto_tokens", "chat_s", "chat_calls",
                "prompt_tokens", "completion_tokens")},
        })
    return out


def _fmt(v, unit: str = "") -> str:
    return "n/a" if v is None else (f"{v:g}{unit}")


def render_markdown(summary: list[dict], meta: dict) -> str:
    lines = [
        "# Tier benchmark",
        "",
        f"Measured on {meta.get('date', '?')} against `{meta.get('copilot', '?')}` (commit `{meta.get('commit', '?')}`), "
        f"{meta.get('runs', '?')} replicate(s); every number is a median of the runs, taken from the stack's own API and clock. "
        "Raw runs: [`benchmark.json`](benchmark.json). Reproduce: `python scripts/benchmark_tiers.py`.",
        "",
        "| Scenario | Expected tier | Tier chosen (hits) | Model | Detection (s) | Chat RCA (s) | LLM calls | Prompt tok | Completion tok "
        "| Auto-diagnosis (s) / mode | Known-good fix proposed |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summary:
        lines.append(
            f"| {TITLES.get(s['scenario'], s['scenario'])} | {s['expected_tier']} "
            f"| {'/'.join(s['tiers_seen'])} ({s['tier_hits']}/{s['runs']}) | {', '.join(f'`{m}`' for m in s['models']) or 'n/a'} "
            f"| {_fmt(s['detection_s'])} | {_fmt(s['chat_s'])} | {_fmt(s['chat_calls'])} | {_fmt(s['prompt_tokens'])} "
            f"| {_fmt(s['completion_tokens'])} | {_fmt(s['auto_diag_s'])} / {meta.get('auto_modes', {}).get(s['scenario'], 'n/a')} "
            f"| {s['action_hits']}/{s['runs']} |")
    by = {s["scenario"]: s for s in summary}
    new, rep = by.get("new_incident"), by.get("repeat_after_fix")
    lines += ["", "## What the numbers say", ""]
    if new and rep and new.get("chat_s") and rep.get("chat_s"):
        faster = (new["chat_s"] - rep["chat_s"]) / new["chat_s"] * 100
        verdict = (f"The repeat incident's chat diagnosis took {_fmt(rep['chat_s'])} s against {_fmt(new['chat_s'])} s for the new "
                   f"one ({faster:+.0f}% {'faster' if faster > 0 else 'slower'}).")
        tok_new = (new.get("prompt_tokens") or 0) + (new.get("completion_tokens") or 0)
        tok_rep = (rep.get("prompt_tokens") or 0) + (rep.get("completion_tokens") or 0)
        if tok_new and tok_rep:
            verdict += f" Tokens: {tok_rep:g} against {tok_new:g} ({(tok_new - tok_rep) / tok_new * 100:+.0f}% fewer)."
        if faster <= 0:
            verdict += " **The repeat was not faster in this run; the claim is not supported by these measurements.**"
        lines.append(verdict)
    else:
        lines.append("Not enough data to compare the new and repeat incidents.")
    lines += ["", "Method notes:", "",
              "- The three scenarios run back to back on one stack per replicate, so the repeat incident has two precedents in memory.",
              "- *Detection* = fault switched on -> incident opened by the watcher (the stack's 1-minute window). *Chat RCA* = the "
              "operator's question -> answer, including memory recall. *Auto-diagnosis* = incident opened -> the watcher's own "
              "diagnosis stored (`full` = tool-using RCA, `verify` = light memory-first check).",
              "- A settling pause after each Fix Worked (longer than `RESOLVE_GRACE_S`) is excluded from every number.",
              f"- Tier models come from the chat reply (`model`). Fix proposed = the reply ends with `Proposed action: {KNOWN_FIX}`."]
    return "\n".join(lines) + "\n"


# ── HTTP / stack helpers ──────────────────────────────────────────────────────────────────────────

class Bench:
    def __init__(self, copilot: str, shop: str, chaos_key: str, settle_s: float):
        self.copilot, self.shop, self.chaos_key, self.settle_s = copilot.rstrip("/"), shop.rstrip("/"), chaos_key, settle_s

    def call(self, method: str, url: str, body: dict | None = None, timeout: float = 30.0, headers: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def chaos_headers(self) -> dict:
        return {"X-Chaos-Key": self.chaos_key} if self.chaos_key else {}

    def fault(self, fault_type: str, enabled: bool = True) -> None:
        status, _ = self.call("POST", f"{self.shop}/api/faults", {"fault_type": fault_type, "enabled": enabled, "params": {}},
                              headers=self.chaos_headers())
        if status != 200:
            raise RuntimeError(f"could not set fault {fault_type} (HTTP {status}); is CHAOS_KEY needed?")

    def faults_off(self) -> None:
        self.call("POST", f"{self.shop}/api/faults/reset", headers=self.chaos_headers())

    def wait(self, what: str, fn, timeout: float, every: float = 3.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                got = fn()
            except Exception:  # noqa: BLE001 - a poll may fail while containers restart
                got = None
            if got:
                return got
            time.sleep(every)
        raise TimeoutError(f"timed out after {timeout:.0f}s waiting for {what}")

    def ready(self, timeout: float = 420.0) -> None:
        def ok():
            s1, _ = self.call("GET", f"{self.copilot}/api/incidents")
            s2, mem = self.call("GET", f"{self.copilot}/api/memory/past-incidents")
            return s1 == 200 and s2 == 200 and isinstance(mem, dict) and mem.get("enabled")
        self.wait("copilot and memory to be ready (MEMORY_MODE must be on)", ok, timeout, 5.0)

    def memory_total(self) -> int:
        _, mem = self.call("GET", f"{self.copilot}/api/memory/past-incidents")
        return int((mem or {}).get("total") or 0)

    def new_incident(self, known: set, timeout: float = 240.0) -> dict:
        def find():
            _, lst = self.call("GET", f"{self.copilot}/api/incidents?limit=50")
            fresh = [i for i in (lst or []) if i["id"] not in known and i["status"] != "RESOLVED"]
            return sorted(fresh, key=lambda i: i["opened_at"])[0] if fresh else None
        return self.wait("the watcher to open an incident", find, timeout)

    def detail(self, incident_id: str) -> dict:
        _, d = self.call("GET", f"{self.copilot}/api/incidents/{incident_id}")
        return d or {}

    def ask(self, question: str, timeout: float = 300.0) -> dict:
        _, chat = self.call("POST", f"{self.copilot}/chat/new")
        t0 = time.time()
        status, reply = self.call("POST", f"{self.copilot}/ask/{chat['id']}", {"message": question}, timeout=timeout)
        return {"status": status, "reply": reply or {}, "wall_s": round(time.time() - t0, 2)}

    def reset(self, cmd: str | None) -> None:
        if cmd is None:
            cmd = ('powershell -NoProfile -ExecutionPolicy Bypass -File "scripts/reset_demo.ps1"'
                   if platform.system() == "Windows" else "bash scripts/reset_demo.sh")
        print(f"  reset: {cmd}", flush=True)
        subprocess.run(cmd, shell=True, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        self.ready()

    def one_scenario(self, rep: int, name: str, fault: str, question: str, expected: str, known: set) -> dict:
        t_fault = time.time()
        self.fault(fault, True)
        inc = self.new_incident(known)
        known.add(inc["id"])
        detection_s = round(inc["opened_at"] / 1000.0 - t_fault, 2)
        det = self.wait("the automatic diagnosis", lambda: (lambda d: d if d.get("rca_status") not in (None, "pending") else None)(
            self.detail(inc["id"])), 300.0)
        diag_ok = next((e for e in det.get("timeline", []) if e["kind"] == "diagnosis:ok"), None)
        asked = self.ask(question)
        reply = asked["reply"]
        after = self.detail(inc["id"])
        return {
            "replicate": rep, "scenario": name, "expected_tier": expected, "fault": fault, "incident_id": inc["id"],
            "tier": reply.get("tier"), "model": reply.get("model"), "http_status": asked["status"],
            "detection_s": detection_s,
            "auto_diag_s": round((diag_ok["ts"] - inc["opened_at"]) / 1000.0, 2) if diag_ok else None,
            "auto_mode": det.get("diagnosis_mode"), "auto_calls": det.get("llm_calls"), "auto_tokens": det.get("llm_tokens"),
            "chat_s": asked["wall_s"], "chat_calls": reply.get("llm_calls"),
            "prompt_tokens": reply.get("prompt_tokens"), "completion_tokens": reply.get("completion_tokens"),
            "action": parse_action(reply.get("response", "")), "joined_incident": reply.get("incident_id") == inc["id"],
            "recalled": [r.get("incident_id") for r in after.get("recalled", [])],
            "question": question, "reply_text": reply.get("response", ""),
        }

    def resolve_and_index(self, incident_id: str, memory_target: int) -> None:
        self.faults_off()
        _, _ = self.call("POST", f"{self.copilot}/api/incidents/{incident_id}/feedback",
                         {"action": KNOWN_FIX, "worked": True, "notes": "benchmark"})
        t0 = time.time()
        self.wait(f"Hindsight to index {memory_target} incident(s)", lambda: self.memory_total() >= memory_target, 600.0, 5.0)
        left = self.settle_s - (time.time() - t0)
        if left > 0:                      # let the post-resolve grace window pass; excluded from all measurements
            time.sleep(left)


def build_replay(rows: list[dict], meta: dict) -> dict | None:
    """A recorded real run for the chat's demo-budget fallback: one reply per scenario, preferring a run whose tier was as expected."""
    replies: dict = {}
    for name, _fault, _q, expected in SCENARIOS:
        cands = [r for r in rows if r.get("scenario") == name and r.get("http_status") == 200 and (r.get("reply_text") or "").strip()]
        cands.sort(key=lambda r: (r.get("tier") != expected, r.get("replicate", 0)))
        if cands:
            c = cands[0]
            replies[name] = {"question": c["question"], "response": c["reply_text"], "tier": c.get("tier"), "model": c.get("model")}
    if not replies:
        return None
    return {"meta": {"recorded": meta.get("date", "?"), "commit": meta.get("commit", "?"), "source": "scripts/benchmark_tiers.py"},
            "replies": replies}


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def build_meta(args, rows: list[dict]) -> dict:
    modes: dict = {}
    for s, _f, _q, _e in SCENARIOS:
        seen = sorted({str(r.get("auto_mode")) for r in rows if r.get("scenario") == s and r.get("auto_mode")})
        modes[s] = "/".join(seen) if seen else "n/a"
    return {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "copilot": args.copilot, "commit": git_commit(),
            "runs": args.runs, "auto_modes": modes, "python": sys.version.split()[0], "platform": platform.platform()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--copilot", default="http://localhost:9911")
    ap.add_argument("--shop", default="http://localhost:9922")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--settle", type=float, default=95.0, help="seconds to wait after a resolve before the next fault (> RESOLVE_GRACE_S)")
    ap.add_argument("--reset-cmd", default=None, help="command that wipes copilot + Hindsight data (default: scripts/reset_demo.*)")
    ap.add_argument("--out-dir", default=str(ROOT / "docs"))
    ap.add_argument("--replay-out", default=str(ROOT / "Copilot" / "data" / "replay.json"),
                    help="also record the replies as the chat's demo-budget replay (empty string to skip)")
    ap.add_argument("--render", action="store_true", help="rebuild benchmark.md from the existing benchmark.json and exit")
    args = ap.parse_args(argv)
    out = Path(args.out_dir)

    if args.render:
        data = json.loads((out / "benchmark.json").read_text(encoding="utf-8"))
        (out / "benchmark.md").write_text(render_markdown(summarize(data["runs"]), data["meta"]), encoding="utf-8")
        print(f"rewrote {out / 'benchmark.md'}")
        return 0

    b = Bench(args.copilot, args.shop, os.getenv("CHAOS_KEY", ""), args.settle)
    rows: list[dict] = []
    try:
        for rep in range(1, args.runs + 1):
            print(f"replicate {rep}/{args.runs}", flush=True)
            b.faults_off()
            b.reset(args.reset_cmd)
            known: set = set()
            for idx, (name, fault, question, expected) in enumerate(SCENARIOS, start=1):
                row = b.one_scenario(rep, name, fault, question, expected, known)
                rows.append(row)
                print(f"  {name}: tier={row['tier']} (expected {expected}) detect={row['detection_s']}s chat={row['chat_s']}s "
                      f"calls={row['chat_calls']} action={row['action']}", flush=True)
                b.resolve_and_index(row["incident_id"], idx)
    finally:
        b.faults_off()
        if rows:
            out.mkdir(parents=True, exist_ok=True)
            meta = build_meta(args, rows)
            (out / "benchmark.json").write_text(json.dumps({"meta": meta, "runs": rows}, indent=2), encoding="utf-8")
            (out / "benchmark.md").write_text(render_markdown(summarize(rows), meta), encoding="utf-8")
            print(f"wrote {out / 'benchmark.md'} and benchmark.json ({len(rows)} runs)")
            replay = build_replay(rows, meta) if args.replay_out else None
            if replay:
                Path(args.replay_out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.replay_out).write_text(json.dumps(replay, indent=2), encoding="utf-8")
                print(f"wrote {args.replay_out} (replay of {len(replay['replies'])} scenario(s))")
    return 0


def _load_dotenv() -> None:
    """Fill missing variables (CHAOS_KEY) from the repo's .env; script use only, a variable already set wins."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


if __name__ == "__main__":
    _load_dotenv()
    raise SystemExit(main())
