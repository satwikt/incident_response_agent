"""Alert threshold configuration for the Copilot watcher.

All values are loaded from environment variables so they can be tuned per deployment without code changes.
"""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class WatcherThresholds:
    # How often the watcher evaluates recent events (minutes; fractions allowed, e.g. 0.25 = 15 s).
    interval_minutes: float = float(os.getenv("WATCHER_INTERVAL_MINUTES", "5"))

    # Alert if ANY route (with enough data) has an error percentage at or above this value.
    error_rate_pct: float = float(os.getenv("ALERT_ERROR_RATE_THRESHOLD", "10.0"))

    # Alert if ANY route (with enough data) has p95 latency (ms) at or above this value.
    latency_p95_ms: float = float(os.getenv("ALERT_LATENCY_P95_MS", "500"))

    # Alert if at least this many requests took longer than slow_request_min_ms in the window.
    slow_request_count: int = int(os.getenv("ALERT_SLOW_REQUEST_COUNT", "3"))
    slow_request_min_ms: int = int(os.getenv("ALERT_SLOW_REQUEST_MIN_MS", "500"))

    # Comma-separated keywords; a match in recent events counts as a breach.
    log_keywords: List[str] = field(default_factory=lambda: [
        kw.strip()
        for kw in os.getenv("ALERT_LOG_KEYWORDS", "CRITICAL,Traceback").split(",")
        if kw.strip()
    ])

    # Maximum event lines fetched per keyword scan.
    log_scan_limit: int = int(os.getenv("ALERT_LOG_SCAN_LIMIT", "50"))

    @property
    def log_scan_minutes(self) -> float:
        return self.interval_minutes


thresholds = WatcherThresholds()
