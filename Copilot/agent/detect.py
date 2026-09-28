"""Pure breach detection: turns tool results into structured breaches.

No I/O and no LLM, so it can be unit tested exhaustively. Routes without enough data in the window
are never judged (one failed request out of one is not a 100% outage), in either direction: they are neither
breaching nor healthy, they are unknown.
"""

from dataclasses import dataclass
from typing import List, Optional, Set

# Highest priority first: decides which breach names an incident.
KIND_PRIORITY = {"health": 0, "error_rate": 1, "latency": 2, "slow": 3, "keyword": 4}


@dataclass(frozen=True)
class Breach:
    kind: str                # health | error_rate | latency | slow | keyword
    route: Optional[str]     # None for service-wide breaches
    text: str

    def __str__(self) -> str:
        return self.text


def find_breaches(health: dict, error_data: dict, latency_data: dict, slow_data: dict, logs_data: dict,
                  t) -> List[Breach]:
    """Return structured breaches (empty list means all clear). ``t`` is a WatcherThresholds-like object."""
    out: List[Breach] = []
    w = f" [window {t.log_scan_minutes:g}m]"

    if health.get("status") == "down":
        out.append(Breach("health", None, "Service health is 'down'"))

    for r in error_data.get("by_route", []) or []:
        if r.get("enough_data") and r.get("error_percent", 0.0) >= t.error_rate_pct:
            out.append(Breach("error_rate", r["route"],
                              f"Error rate on '{r['route']}' is {r['error_percent']:.1f}% "
                              f"({r['error_count']}/{r['request_count']} requests, threshold {t.error_rate_pct}%){w}"))

    for r in latency_data.get("by_route", []) or []:
        ms = r.get("p95_latency_ms")
        if r.get("enough_data") and ms is not None and ms >= t.latency_p95_ms:
            out.append(Breach("latency", r["route"],
                              f"p95 latency on '{r['route']}' is {ms:.0f}ms (threshold {t.latency_p95_ms}ms){w}"))

    for r in slow_data.get("by_route", []) or []:
        if r.get("enough_data") and r.get("slow_count", 0) >= t.slow_request_count:
            out.append(Breach("slow", r["route"],
                              f"{r['slow_count']} of {r['request_count']} requests on '{r['route']}' took at least "
                              f"{t.slow_request_min_ms}ms (threshold {t.slow_request_count} slow requests){w}"))

    matches = logs_data.get("match_count", 0)
    if matches > 0:
        out.append(Breach("keyword", None,
                          f"{matches} event(s) matched keywords '{logs_data.get('keyword', '')}' "
                          f"in the last {t.log_scan_minutes:g}m"))
    return out


def evaluate(health: dict, error_data: dict, latency_data: dict, slow_data: dict, logs_data: dict, t) -> List[str]:
    """String form of :func:`find_breaches` (kept for callers that only need the text)."""
    return [b.text for b in find_breaches(health, error_data, latency_data, slow_data, logs_data, t)]


def healthy_routes(error_data: dict, latency_data: dict, slow_data: dict, t, close_ratio: float) -> Set[str]:
    """Routes that are comfortably healthy this window (hysteresis).

    A route qualifies only if it has enough data AND its error rate and p95 are below ``close_ratio`` x the alert
    thresholds AND it is not producing slow requests at the alert level. A route right at the threshold is neither
    breaching-and-new nor healthy, which stops an incident flapping open and closed around the limit.
    """
    slow_by_route = {r["route"]: r for r in slow_data.get("by_route", []) or []}
    lat_by_route = {r["route"]: r for r in latency_data.get("by_route", []) or []}
    ok: Set[str] = set()
    for r in error_data.get("by_route", []) or []:
        if not r.get("enough_data"):
            continue
        if r.get("error_percent", 0.0) >= t.error_rate_pct * close_ratio:
            continue
        lat = lat_by_route.get(r["route"])
        if lat and lat.get("enough_data") and (lat.get("p95_latency_ms") or 0) >= t.latency_p95_ms * close_ratio:
            continue
        slow = slow_by_route.get(r["route"])
        if slow and slow.get("slow_count", 0) >= max(1, int(t.slow_request_count * close_ratio)):
            continue
        ok.add(r["route"])
    return ok


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
        lines.append(f"  Event (untrusted text): {s[:140]}")
    return "\n".join(lines)
