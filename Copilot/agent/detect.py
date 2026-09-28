"""Pure breach detection: turns tool results into a list of human-readable breaches.

No I/O and no LLM, so it can be unit tested exhaustively. Routes without enough data in the window
are never judged (one failed request out of one is not a 100% outage).
"""

from typing import List


def evaluate(health: dict, error_data: dict, latency_data: dict, slow_data: dict, logs_data: dict, t) -> List[str]:
    """Return breach descriptions (empty list means all clear). ``t`` is a WatcherThresholds-like object."""
    breaches: List[str] = []
    w = f" [window {t.log_scan_minutes:g}m]"

    if health.get("status") == "down":
        breaches.append("Service health is 'down'")

    for r in error_data.get("by_route", []) or []:
        if r.get("enough_data") and r.get("error_percent", 0.0) >= t.error_rate_pct:
            breaches.append(
                f"Error rate on '{r['route']}' is {r['error_percent']:.1f}% "
                f"({r['error_count']}/{r['request_count']} requests, threshold {t.error_rate_pct}%){w}"
            )

    for r in latency_data.get("by_route", []) or []:
        ms = r.get("p95_latency_ms")
        if r.get("enough_data") and ms is not None and ms >= t.latency_p95_ms:
            breaches.append(f"p95 latency on '{r['route']}' is {ms:.0f}ms (threshold {t.latency_p95_ms}ms){w}")

    for r in slow_data.get("by_route", []) or []:
        if r.get("enough_data") and r.get("slow_count", 0) >= t.slow_request_count:
            breaches.append(
                f"{r['slow_count']} of {r['request_count']} requests on '{r['route']}' took at least "
                f"{t.slow_request_min_ms}ms (threshold {t.slow_request_count} slow requests){w}"
            )

    matches = logs_data.get("match_count", 0)
    if matches > 0:
        breaches.append(
            f"{matches} event(s) matched keywords '{logs_data.get('keyword', '')}' in the last {t.log_scan_minutes:g}m"
        )
    return breaches


def build_summary(health: dict, error_data: dict, latency_data: dict, slow_data: dict, logs_data: dict) -> str:
    """Concise plain-text telemetry snapshot for the agent prompt (log text is quoted, not interpreted)."""
    lines = [f"Health: {health.get('status', 'unknown')}",
             f"Requests: {error_data.get('total_request_count', 'N/A')}, 5xx: {error_data.get('total_error_count', 'N/A')}"]
    for r in (error_data.get("by_route", []) or [])[:5]:
        lines.append(f"  Route {r['route']}: {r['error_percent']}% errors ({r['request_count']} requests)")
    for r in (latency_data.get("by_route", []) or [])[:5]:
        lines.append(f"  Route {r['route']}: p95={r.get('p95_latency_ms')}ms")
    lines.append(f"Slow requests: {slow_data.get('slow_count', 0)}")
    lines.append(f"Keyword matches: {logs_data.get('match_count', 0)} (keyword: {logs_data.get('keyword', '')})")
    for s in (logs_data.get("sample_lines", []) or [])[:3]:
        lines.append(f"  Event (untrusted text): {s[:160]}")
    return "\n".join(lines)
