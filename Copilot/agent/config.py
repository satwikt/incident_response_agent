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

# ── Incident lifecycle ────────────────────────────────────────────────────────
# Consecutive healthy watcher windows (each with enough traffic) before an incident auto-resolves.
RECOVERY_WINDOWS: int = _int("RECOVERY_WINDOWS", 3)
# A route counts as healthy only when its metrics are below CLOSE_RATIO x the alert thresholds (hysteresis).
CLOSE_RATIO: float = _float("CLOSE_RATIO", 0.5)
# A breach that matches an incident resolved within this window opens a NEW incident linked as a recurrence.
RECURRENCE_WINDOW_HOURS: float = _float("RECURRENCE_WINDOW_HOURS", 24.0)
# An unacknowledged open incident is re-notified at most this often.
RENOTIFY_MINUTES: float = _float("RENOTIFY_MINUTES", 30.0)
# Cap on simultaneously active incidents per service (a flood of distinct fingerprints cannot open unlimited ones).
MAX_OPEN_INCIDENTS: int = _int("MAX_OPEN_INCIDENTS", 5)

# ── Diagnosis (LLM) budget ────────────────────────────────────────────────────
AGENT_MAX_RUNS_PER_HOUR: int = _int("AGENT_MAX_RUNS_PER_HOUR", 12)
# Hard cap on model calls per diagnosis (ADK RunConfig.max_llm_calls). A normal run needs 2 (tool request, answer);
# the cap stops a looping agent from burning the budget.
AGENT_MAX_LLM_CALLS: int = _int("AGENT_MAX_LLM_CALLS", 6)
# Stop calling the model once about this many tokens were spent on diagnoses in the last 24 h (0 = no limit). Free tiers
# have daily caps (200,000 tokens for one Groq model); a noisy afternoon must not use the whole day's quota.
AGENT_DAILY_TOKEN_BUDGET: int = _int("AGENT_DAILY_TOKEN_BUDGET", 0)
AGENT_TIMEOUT_S: float = _float("AGENT_TIMEOUT_S", 120.0)
AGENT_RATE_LIMIT_RETRIES: int = _int("AGENT_RATE_LIMIT_RETRIES", 3)
AGENT_RATE_LIMIT_WAIT_S: float = _float("AGENT_RATE_LIMIT_WAIT_S", 45.0)
BREAKER_FAILURES: int = _int("BREAKER_FAILURES", 3)          # consecutive failures that open the circuit breaker
BREAKER_PAUSE_S: float = _float("BREAKER_PAUSE_S", 300.0)    # how long the breaker stays open

# ── Outbox (notifications) ────────────────────────────────────────────────────
OUTBOX_MAX_ATTEMPTS: int = _int("OUTBOX_MAX_ATTEMPTS", 6)
OUTBOX_LEASE_S: float = _float("OUTBOX_LEASE_S", 60.0)
PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# ── Memory (Hindsight) ────────────────────────────────────────────────────────
# MEMORY_MODE=on uses Hindsight at HINDSIGHT_URL; off disables recall and retain (the benchmark's baseline).
MEMORY_MODE: str = os.getenv("MEMORY_MODE", "off").strip().lower()
HINDSIGHT_URL: str = os.getenv("HINDSIGHT_URL", "")
HINDSIGHT_BANK: str = os.getenv("HINDSIGHT_BANK", "incident-response")
MEMORY_MIN_SCORE: float = _float("MEMORY_MIN_SCORE", 0.05)   # on the FINAL recall score (semantic alone is not discriminating)

# ── Remediation (allow-list) and the action journal ───────────────────────────
# Only these actions can ever be proposed; anything else in a model answer is ignored.
ALLOWED_ACTIONS = tuple(a.strip() for a in os.getenv(
    "ALLOWED_ACTIONS", "flush_pool,restart_worker,rollback_config,rollback_release,enable_fallback").split(",") if a.strip())
APP_OPS_URL: str = os.getenv("APP_OPS_URL", "").rstrip("/")
OPS_KEY: str = os.getenv("OPS_KEY", "")
