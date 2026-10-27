"""List the models Nebius Token Factory serves for your key, and highlight the Nemotron / NVIDIA ones.

    python scripts/list_models.py                                  # print
    python scripts/list_models.py --out docs/token_factory_models.txt

Reads NEBIUS_API_KEY (and optionally NEBIUS_BASE_URL) from the environment only; the key is never printed or written.
Use the ids it prints for NEBIUS_NANO_MODEL / NEBIUS_SUPER_MODEL / NEBIUS_ULTRA_MODEL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
HIGHLIGHT = ("nemotron", "nvidia")


def fetch_models(base_url: str, api_key: str, timeout: float = 20.0) -> list[dict]:
    """GET {base_url}/models and return the model entries. Raises RuntimeError with a key-free message on failure."""
    req = urllib.request.Request(f"{base_url.rstrip('/')}/models",
                                 headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {base_url}/models (check the key and the base URL)") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"could not read {base_url}/models: {type(exc).__name__}") from None
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise RuntimeError("unexpected response shape (no 'data' list)")
    return [m for m in data if isinstance(m, dict) and m.get("id")]


def render(models: list[dict], base_url: str) -> str:
    ids = sorted(str(m["id"]) for m in models)
    marked = [i for i in ids if any(h in i.lower() for h in HIGHLIGHT)]
    lines = [f"Nebius Token Factory models ({base_url}): {len(ids)} total, {len(marked)} Nemotron/NVIDIA (marked *)", ""]
    lines += [f"{'*' if i in marked else ' '} {i}" for i in ids]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="also write the listing to this file")
    args = ap.parse_args(argv)

    key = os.getenv("NEBIUS_API_KEY", "").strip()
    base_url = (os.getenv("NEBIUS_BASE_URL", "") or DEFAULT_BASE_URL).strip()
    if not key:
        print("NEBIUS_API_KEY is not set in the environment.", file=sys.stderr)
        return 2
    try:
        text = render(fetch_models(base_url, key), base_url.rstrip("/"))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(text, end="")
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
