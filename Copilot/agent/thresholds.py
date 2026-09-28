"""Alert threshold configuration for the SRE Copilot watcher.

All values are loaded from environment variables so they can be tuned
per-deployment without code changes.
"""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class WatcherThresholds:
    # ── Polling ───────────────────────────────────────────────────────────────
    # How often the background watcher polls observability sources (minutes).
    interval_minutes: float = float(os.getenv("WATCHER_INTERVAL_MINUTES", "5"))

    # ── Error rate ────────────────────────────────────────────────────────────
    # Alert if ANY route's error percentage exceeds this value.
    error_rate_pct: float = float(os.getenv("ALERT_ERROR_RATE_THRESHOLD", "10.0"))

    # ── Latency ───────────────────────────────────────────────────────────────
    # Alert if ANY route's p95 latency (ms) exceeds this value.
    latency_p95_ms: float = float(os.getenv("ALERT_LATENCY_P95_MS", "500"))

    # ── Slow traces ───────────────────────────────────────────────────────────
    # Alert if the number of slow traces returned by Tempo exceeds this count.
    slow_trace_count: int = int(os.getenv("ALERT_SLOW_TRACE_COUNT", "3"))

    # Minimum trace duration (ms) to consider "slow" when querying Tempo.
    slow_trace_min_ms: int = int(os.getenv("ALERT_SLOW_TRACE_MIN_MS", "500"))

    # ── Log keywords ─────────────────────────────────────────────────────────
    # Comma-separated list of keywords; if any appear in recent logs, alert.
    log_keywords: List[str] = field(default_factory=lambda: [
        kw.strip()
        for kw in os.getenv(
            "ALERT_LOG_KEYWORDS", "ERROR,CRITICAL,Exception,Traceback"
        ).split(",")
        if kw.strip()
    ])

    # Maximum log lines fetched per keyword scan.
    log_scan_limit: int = int(os.getenv("ALERT_LOG_SCAN_LIMIT", "50"))


# Singleton instance used throughout the application.
thresholds = WatcherThresholds()
