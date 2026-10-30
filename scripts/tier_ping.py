"""Send one prompt to each Nebius Token Factory tier model and print model + latency (a real call: opt-in, costs a few tokens).

    python scripts/tier_ping.py        # needs NEBIUS_API_KEY and NEBIUS_{NANO,SUPER,ULTRA}_MODEL (read from .env if present)
"""
import json, os, sys, time, urllib.request
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, ".env"))
key, base = os.getenv("NEBIUS_API_KEY", "").strip(), os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
if not key:
    sys.exit("NEBIUS_API_KEY is not set (environment or .env).")
for tier in ("nano", "super", "ultra"):
    model = os.getenv(f"NEBIUS_{tier.upper()}_MODEL", "").strip()
    if not model:
        print(f"{tier:6} NEBIUS_{tier.upper()}_MODEL not set"); continue
    body = json.dumps({"model": model, "max_tokens": 200, "messages": [{"role": "user", "content": "In one sentence: why does checkout return HTTP 500 when the payment provider is down?"}]}).encode()
    req = urllib.request.Request(f"{base}/chat/completions", body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.load(r)
        msg = out["choices"][0]["message"]
        print(f"{tier:6} {out.get('model', model):45} {time.perf_counter() - t0:6.2f}s  tokens={out.get('usage', {}).get('total_tokens')}  {((msg.get('content') or msg.get('reasoning_content') or '').strip()[:70])!r}")
    except Exception as exc:  # noqa: BLE001 - report and carry on to the next tier; never echo the key
        print(f"{tier:6} {model:45} FAILED after {time.perf_counter() - t0:.2f}s: {type(exc).__name__}: {str(exc)[:120]}")
