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
# After an incident is resolved, a breach of the same kind within this many seconds is treated as the tail of the old
# incident (the watcher window still holds pre-fix requests), not a new one. Keep it a little above the watcher window.
RESOLVE_GRACE_S: float = _float("RESOLVE_GRACE_S", 90.0)
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
# Upper bound for the memory pre-recall of a chat RCA (Hindsight may be starting or slow); the chat goes on without it.
CHAT_RECALL_TIMEOUT_S: float = _float("CHAT_RECALL_TIMEOUT_S", 8.0)
AGENT_RATE_LIMIT_RETRIES: int = _int("AGENT_RATE_LIMIT_RETRIES", 3)
AGENT_RATE_LIMIT_WAIT_S: float = _float("AGENT_RATE_LIMIT_WAIT_S", 45.0)
BREAKER_FAILURES: int = _int("BREAKER_FAILURES", 3)          # consecutive failures that open the circuit breaker
BREAKER_PAUSE_S: float = _float("BREAKER_PAUSE_S", 300.0)    # how long the breaker stays open

# ── Inference provider ────────────────────────────────────────────────────────
# Primary provider for the Nemotron tiers: "nebius" (Nebius Token Factory, OpenAI-compatible) or "nvidia" (NVIDIA NIM).
# A provider without its API key is skipped, so the stack runs with only NEBIUS_API_KEY, only NVIDIA_API_KEY, or both;
# the others (then Groq, if GROQ_API_KEY is set) form the fallback chain in that order.
INFERENCE_PROVIDER: str = os.getenv("INFERENCE_PROVIDER", "nebius").strip().lower()
NEBIUS_API_KEY: str = os.getenv("NEBIUS_API_KEY", "").strip()
NEBIUS_BASE_URL: str = os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1").strip().rstrip("/")
# Token Factory model ids per tier (list them with scripts/list_models.py). A tier without an id is not served by Nebius.
NEBIUS_NANO_MODEL: str = os.getenv("NEBIUS_NANO_MODEL", "").strip()
NEBIUS_SUPER_MODEL: str = os.getenv("NEBIUS_SUPER_MODEL", "").strip()
NEBIUS_ULTRA_MODEL: str = os.getenv("NEBIUS_ULTRA_MODEL", "").strip()

# ── NVIDIA Nemotron Model Tiers ───────────────────────────────────────────────
NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL: str = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
# Nano: minimal reasoning for same-context recurring incidents (fast adjustments)
NEMOTRON_NANO_MODEL: str = os.getenv("NEMOTRON_NANO_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
# Super: mid reasoning for similar incidents with different context (guided RCA)
NEMOTRON_SUPER_MODEL: str = os.getenv("NEMOTRON_SUPER_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
# Ultra: deep multi-step reasoning for completely new incidents (scratch RCA)
NEMOTRON_ULTRA_MODEL: str = os.getenv("NEMOTRON_ULTRA_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")

# ── Groq Fallback Model ───────────────────────────────────────────────────────
GROQ_API_KEY: str = (os.getenv("GROQ_API_KEY", "") or os.getenv("HINDSIGHT_LLM_API_KEY", "")).strip()
_raw_groq_model = (os.getenv("GROQ_MODEL", "") or os.getenv("GROQ_FALLBACK_MODEL", "groq/llama-3.3-70b-versatile")).strip()
if _raw_groq_model and not _raw_groq_model.startswith("groq/") and not _raw_groq_model.startswith("openai/"):
    GROQ_MODEL: str = f"groq/{_raw_groq_model}"
else:
    GROQ_MODEL: str = _raw_groq_model or "groq/llama-3.3-70b-versatile"


# ── Outbox (notifications) ────────────────────────────────────────────────────
OUTBOX_MAX_ATTEMPTS: int = _int("OUTBOX_MAX_ATTEMPTS", 6)
OUTBOX_LEASE_S: float = _float("OUTBOX_LEASE_S", 60.0)
PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# ── Memory (Hindsight) ────────────────────────────────────────────────────────
# MEMORY_MODE=on uses Hindsight at HINDSIGHT_URL; off disables recall and retain (the benchmark's baseline).
MEMORY_MODE: str = os.getenv("MEMORY_MODE", "off").strip().lower()
HINDSIGHT_URL: str = os.getenv("HINDSIGHT_URL", "")
HINDSIGHT_BANK: str = os.getenv("HINDSIGHT_BANK", "incident-response")
# OPERATIONS_RUNBOOK s4: "Fix Failed" writes failed:<action> into Hindsight memory. Each retain costs one memory-LLM call, and
# Groq's free tier allows only ~2 per minute (Hindsight then defers and retries after the quota resets). Set to "off" for a
# quota-tight demo: failed actions are then stored with the fix once the incident resolves.
MEMORY_RETAIN_FAILED_ATTEMPTS: bool = os.getenv("MEMORY_RETAIN_FAILED_ATTEMPTS", "on").strip().lower() in ("on", "true", "1")
MEMORY_MIN_SCORE: float = _float("MEMORY_MIN_SCORE", 0.05)   # on the FINAL recall score (semantic alone is not discriminating)

# ── Remediation (allow-list) and the action journal ───────────────────────────
# Only these actions can ever be proposed; anything else in a model answer is ignored.
ALLOWED_ACTIONS = tuple(a.strip() for a in os.getenv(
    "ALLOWED_ACTIONS",
    "flush_pool,restart_worker,rollback_config,rollback_release,enable_fallback,create_missing_table,run_db_migration,apply_schema_migration,disable_fault,hotfix_query,patch_code"
).split(",") if a.strip())
APP_OPS_URL: str = os.getenv("APP_OPS_URL", "").rstrip("/")
OPS_KEY: str = os.getenv("OPS_KEY", "")

# ── External Observability & Telemetry APIs ──────────────────────────────────
PROMETHEUS_URL: str = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
LOKI_URL: str = os.getenv("LOKI_URL", "http://loki:3100").rstrip("/")
TEMPO_URL: str = os.getenv("TEMPO_URL", "http://tempo:3200").rstrip("/")
JAEGER_URL: str = os.getenv("JAEGER_URL", "http://jaeger:16686").rstrip("/")
FLAGD_URL: str = os.getenv("FLAGD_URL", "http://flagd:8080").rstrip("/")
DOCS_INDEX_PATH: str = os.getenv("DOCS_INDEX_PATH", "data/docs_index.json")
CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", "Copilot/data/chroma_db")
CHROMA_COLLECTION: str = os.getenv("CHROMA_COLLECTION", "astronomy_shop_docs")
