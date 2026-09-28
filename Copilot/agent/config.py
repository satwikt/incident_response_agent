"""Centralised runtime configuration for the Incident Response Copilot.

All values are read from environment variables (loaded via .env).
Import this module instead of calling os.getenv() directly in tools or agents.
"""

import os
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# ── Target application ────────────────────────────────────────────────────────
# The service whose events the agent reasons about. Ingest keys are bound to a service name.
SERVICE_NAME: str = os.getenv("APP_SERVICE_NAME", "todo-app")


def parse_ingest_keys(raw: str) -> Dict[str, str]:
    """Parse ``service:key,service2:key2`` into ``{key: service}``. Malformed pairs are ignored."""
    out: Dict[str, str] = {}
    for pair in (raw or "").split(","):
        service, sep, key = pair.strip().partition(":")
        if sep and service.strip() and key.strip():
            out[key.strip()] = service.strip()
    return out


# ── Ingest API ────────────────────────────────────────────────────────────────
# key -> service. Empty means the ingest endpoint is disabled (fails closed).
INGEST_KEYS: Dict[str, str] = parse_ingest_keys(os.getenv("INGEST_KEYS", ""))
INGEST_MAX_BODY_BYTES: int = _int("INGEST_MAX_BODY_BYTES", 1_048_576)
INGEST_MAX_BATCH: int = _int("INGEST_MAX_BATCH", 500)
INGEST_RATE_PER_SEC: float = _float("INGEST_RATE_PER_SEC", 20.0)   # sustained requests/s per key
INGEST_RATE_BURST: int = _int("INGEST_RATE_BURST", 40)

# Field limits (values longer than these are truncated, or rejected where noted in api/ingest.py).
MAX_MESSAGE_CHARS: int = _int("EVENT_MAX_MESSAGE_CHARS", 4096)
MAX_EXCEPTION_CHARS: int = _int("EVENT_MAX_EXCEPTION_CHARS", 8192)
MAX_ROUTE_CHARS: int = 200

# ── Event storage ─────────────────────────────────────────────────────────────
COPILOT_DB_PATH: str = os.getenv("COPILOT_DB_PATH", str(_PROJECT_ROOT / "data" / "copilot.db"))
EVENTS_RETENTION_HOURS: float = _float("EVENTS_RETENTION_HOURS", 24.0)
EVENTS_MAX_ROWS: int = _int("EVENTS_MAX_ROWS", 200_000)
# A route with fewer requests than this in the window is "not enough data", never "unhealthy".
MIN_REQUESTS: int = _int("MIN_REQUESTS", 20)

# ── Notifications ─────────────────────────────────────────────────────────────
# Discord incoming webhook URL. Leave blank to disable Discord notifications.
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")
