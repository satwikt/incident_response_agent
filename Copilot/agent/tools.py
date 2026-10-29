"""Read-only telemetry and documentation tools for the Incident Response Copilot (Google ADK).

Every tool is strictly read-only and hardened against injection:
- Ingested event store tools: read from local SQLite event store.
- External observability tools: parameterized query templates for Prometheus, Loki, Tempo, and Flagd.
- Documentation tool: token-scored RAG tool searching indexed system documentation.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from db import events as store
from . import config
from .config import SERVICE_NAME


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a numeric value to [lo, hi]."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = lo
    import math
    if math.isnan(v) or math.isinf(v):
        v = lo
    return max(lo, min(hi, v))
from .external_obs_tools import (
    _http_get_json,
    get_feature_flags,
    get_trace_by_id,
    query_service_logs,
    query_service_metrics,
    query_service_traces,
)

log = logging.getLogger("copilot.tools")


# ─── SQLite Event Store Tools ─────────────────────────────────────────────────

def get_error_rate(minutes: float = 5) -> dict:
    """Get request counts and 5xx error percentage per route for the last ``minutes`` minutes.

    Routes with too few requests are marked ``enough_data: false`` and should not be judged unhealthy.

    Args:
        minutes: Lookback window in minutes (default 5).
    """
    try:
        return store.error_rate(SERVICE_NAME, minutes)
    except Exception as exc:
        log.exception("get_error_rate failed")
        return {"error": str(exc), "source": "events"}


def get_latency(minutes: float = 5) -> dict:
    """Get 95th percentile request latency in milliseconds per route for the last ``minutes`` minutes.

    Args:
        minutes: Lookback window in minutes (default 5).
    """
    try:
        return store.latency(SERVICE_NAME, minutes)
    except Exception as exc:
        log.exception("get_latency failed")
        return {"error": str(exc), "source": "events"}


def _format_loki_entries(data: list) -> list[str]:
    """Format Loki log entries into clean log lines with timestamps."""
    lines = []
    for entry in data:
        line_text = entry.get("line") or ""
        try:
            parsed = json.loads(line_text)
            if isinstance(parsed, dict):
                lvl = parsed.get("level", entry.get("level", "INFO"))
                msg = parsed.get("message", line_text)
                exc = parsed.get("exception")
                line_text = f"{lvl} | {msg}" + (f" | {exc}" if exc else "")
        except Exception:
            pass

        ts_ns = entry.get("timestamp")
        ts_prefix = ""
        if ts_ns:
            try:
                sec = float(ts_ns) / 1e9
                ts_prefix = time.strftime("[%H:%M:%S] ", time.localtime(sec))
            except Exception:
                pass
        lines.append(f"{ts_prefix}{line_text}"[:320])
    return lines


def get_recent_logs(keyword: Optional[str] = None, limit: int = 10, minutes: float = 10) -> dict:
    """Get recent application events (newest first), optionally filtered by a literal keyword.

    Args:
        keyword: Optional literal text to look for (e.g. 'ValueError', 'pool', 'release=v2.4.0').
        limit: Max number of lines to return (default 10, at most 200).
        minutes: Lookback duration in minutes (default 10).
    """
    try:
        res = store.recent_logs(SERVICE_NAME, keyword, limit, minutes)
        if not res.get("sample_lines"):
            lvl = "ALL"
            if keyword and keyword.upper() in ("ERROR", "WARN", "WARNING", "INFO", "DEBUG"):
                lvl = keyword.upper()
            loki_res = query_service_logs(SERVICE_NAME, level=lvl, minutes=minutes, limit=limit)
            if loki_res.get("ok") and loki_res.get("data"):
                lines = _format_loki_entries(loki_res["data"])
                if keyword and lvl == "ALL":
                    lines = [ln for ln in lines if keyword.lower() in ln.lower()]
                if lines:
                    return {
                        "source": "loki",
                        "window_minutes": minutes,
                        "keyword": keyword or "(all logs)",
                        "match_count": len(lines),
                        "sample_lines": lines,
                        "note": "Logs retrieved directly from Grafana Loki telemetry service.",
                    }
        return res
    except Exception as exc:
        log.exception("get_recent_logs failed")
        return {"error": str(exc), "source": "events"}


def get_logs(minutes: float = 10, limit: int = 50) -> dict:
    """Retrieve recent log events across the monitored service."""
    return get_recent_logs(keyword=None, limit=limit, minutes=minutes)


def get_logs_for_service(service: str, minutes: float = 10, keyword: Optional[str] = None, limit: int = 50) -> dict:
    """Retrieve recent log events for a specific named service."""
    try:
        res = store.recent_logs(service, keyword, limit, minutes)
        if not res.get("sample_lines"):
            lvl = "ALL"
            if keyword and keyword.upper() in ("ERROR", "WARN", "WARNING", "INFO", "DEBUG"):
                lvl = keyword.upper()
            loki_res = query_service_logs(service, level=lvl, minutes=minutes, limit=limit)
            if loki_res.get("ok") and loki_res.get("data"):
                lines = _format_loki_entries(loki_res["data"])
                if keyword and lvl == "ALL":
                    lines = [ln for ln in lines if keyword.lower() in ln.lower()]
                if lines:
                    return {
                        "source": "loki",
                        "window_minutes": minutes,
                        "keyword": keyword or "(all logs)",
                        "match_count": len(lines),
                        "sample_lines": lines,
                        "note": "Logs retrieved directly from Grafana Loki telemetry service.",
                    }
        return res
    except Exception as exc:
        log.exception("get_logs_for_service failed")
        return {"error": str(exc), "source": "events"}


def get_slow_requests(threshold_ms: float = 500, minutes: float = 5) -> dict:
    """Get the slowest requests over a duration threshold in the last ``minutes`` minutes.

    Args:
        threshold_ms: Minimum duration in milliseconds to count as slow (default 500).
        minutes: Lookback window in minutes (default 5).
    """
    try:
        return store.slow_requests(SERVICE_NAME, threshold_ms, minutes)
    except Exception as exc:
        log.exception("get_slow_requests failed")
        return {"error": str(exc), "source": "events"}


def get_traces(minutes: float = 10, min_duration_ms: float = 0, limit: int = 20) -> dict:
    """Retrieve distributed traces ingested from Tempo/Jaeger for the monitored service.

    Args:
        minutes: Lookback window in minutes (default 10).
        min_duration_ms: Minimum duration filter in ms (default 0 = all traces).
        limit: Max number of trace lines to return (default 20, at most 200).
    """
    try:
        result = store.recent_traces(SERVICE_NAME, int(_clamp(limit, 1, 200)), minutes)
        if min_duration_ms > 0:
            # Post-filter by duration if requested
            filtered = [ln for ln in result.get("sample_lines", [])
                        if _extract_duration_ms(ln) >= min_duration_ms]
            result = dict(result)
            result["sample_lines"] = filtered
            result["match_count"] = len(filtered)
        return result
    except Exception as exc:
        log.exception("get_traces failed")
        return {"error": str(exc), "source": "events"}


def _extract_duration_ms(line: str) -> float:
    """Extract duration_ms value from a trace line for min_duration_ms filtering."""
    m = re.search(r"(\d+)ms", line)
    return float(m.group(1)) if m else 0.0


def get_traces_for_service(service: str, minutes: float = 10, min_duration_ms: float = 0, limit: int = 20) -> dict:
    """Retrieve distributed traces from Tempo/Jaeger for a specific named service.

    Args:
        service: Service name (e.g. 'checkout', 'catalog', 'frontend').
        minutes: Lookback window in minutes (default 10).
        min_duration_ms: Minimum duration filter in ms (default 0 = all traces).
        limit: Max number of trace lines to return (default 20, at most 200).
    """
    try:
        result = store.recent_traces(service, int(_clamp(limit, 1, 200)), minutes)
        if min_duration_ms > 0:
            filtered = [ln for ln in result.get("sample_lines", [])
                        if _extract_duration_ms(ln) >= min_duration_ms]
            result = dict(result)
            result["sample_lines"] = filtered
            result["match_count"] = len(filtered)
        return result
    except Exception as exc:
        log.exception("get_traces_for_service failed")
        return {"error": str(exc), "source": "events"}


def get_service_health() -> dict:
    """Get traffic-based health of the monitored service: up, idle (no recent traffic) or unknown."""
    try:
        return store.health(SERVICE_NAME)
    except Exception as exc:
        log.exception("get_service_health failed")
        return {"service": SERVICE_NAME, "status": "unknown", "error": str(exc)}


def get_error_logs(minutes: float = 5) -> dict:
    """Count of ERROR/exception log lines that are not request events, with a few samples (fails open)."""
    try:
        return store.error_logs(SERVICE_NAME, minutes)
    except Exception as exc:
        log.exception("get_error_logs failed")
        return {"count": 0, "sample_lines": [], "error": str(exc), "source": "events"}


def get_resource_usage() -> dict:
    """Resident memory (MB) of each monitored process, from Prometheus. Fails open: no data means no resource breach."""
    if not config.MEMORY_JOBS:
        return {"processes": [], "source": "prometheus"}
    import urllib.parse
    promql = f'process_resident_memory_bytes{{job=~"{config.MEMORY_JOBS}"}}'
    url = f"{config.PROMETHEUS_URL}/api/v1/query?{urllib.parse.urlencode({'query': promql})}"
    try:
        resp = _http_get_json(url, timeout=2.0)
        rows = resp.get("data", {}).get("result", []) if resp.get("status") == "success" else []
        procs = [{"name": r["metric"].get("job", "process").replace("astronomy-", ""),
                  "rss_mb": float(r["value"][1]) / (1024 * 1024)} for r in rows]
        return {"processes": procs, "source": "prometheus"}
    except Exception as exc:  # noqa: BLE001 - a Prometheus outage must never break the watcher
        log.warning("get_resource_usage failed (%s)", exc)
        return {"processes": [], "error": str(exc), "source": "prometheus"}


# ─── Documentation RAG Tool ───────────────────────────────────────────────────

_DOCS_CACHE: Optional[Dict[str, Any]] = None


def _load_docs_index() -> Optional[Dict[str, Any]]:
    global _DOCS_CACHE
    if _DOCS_CACHE is not None:
        return _DOCS_CACHE

    candidates = [
        Path(config.DOCS_INDEX_PATH),
        Path("Copilot") / config.DOCS_INDEX_PATH,
        Path("data/docs_index.json"),
        Path("Copilot/data/docs_index.json"),
    ]
    for p in candidates:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                _DOCS_CACHE = data
                return _DOCS_CACHE
            except Exception as exc:
                log.warning("Failed to load docs index from %s (%s)", p, exc)
    return None


def search_docs(query: str, limit: int = 3, service: Optional[str] = None) -> dict:
    """Search indexed documentation, runbooks, and service architectures using vector semantic search or BM25.

    Args:
        query: Search keywords or question (e.g., 'checkout error handling', 'pool exhaustion runbook').
        limit: Maximum number of relevant excerpts to return (default 3, max 10).
        service: Optional filter by service name (e.g. 'checkout', 'payment', 'shipping').
    """
    from scripts.index_docs import query_chroma, search_index

    lim = max(1, min(int(limit), 10))

    # 1. Try ChromaDB semantic vector search
    try:
        chroma_hits = query_chroma(
            query=query,
            limit=lim,
            service=service,
            chroma_path=config.CHROMA_DB_PATH,
            collection_name=config.CHROMA_COLLECTION,
        )
        if chroma_hits:
            return {
                "ok": True,
                "engine": "chromadb_vector",
                "query": query,
                "service_filter": service,
                "count": len(chroma_hits),
                "results": chroma_hits,
            }
    except Exception as exc:
        log.info("ChromaDB vector query skipped (%s); falling back to BM25 index.", exc)

    # 2. Fallback to BM25 inverted index
    index_data = _load_docs_index()
    if not index_data:
        return {
            "ok": False,
            "error": "Documentation index not found. Run 'python3 Copilot/scripts/index_docs.py' or './scripts/index_docs.sh' to generate.",
            "results": [],
        }

    try:
        matches = search_index(index_data, query, limit=lim, service=service)
        return {
            "ok": True,
            "engine": "bm25_lexical",
            "query": query,
            "service_filter": service,
            "count": len(matches),
            "results": matches,
        }
    except Exception as exc:
        log.exception("search_docs failed")
        return {"ok": False, "error": str(exc), "results": []}


def recall_past_incidents(service_name: Optional[str] = None, query: str = "", limit: int = 3) -> dict:
    """Recall past resolved incidents from Hindsight memory and the incident historical knowledge base.

    Use this tool early in an investigation to check if an identical or similar incident has occurred before,
    what its root cause was, and what remediation action resolved it (or failed).

    Args:
        service_name: Optional service name to filter by (e.g. 'checkout', 'catalog', 'frontend').
        query: Symptom, exception message, or table name (e.g. 'UndefinedTable', 'payment_transactions_v2', 'pool exhausted').
        limit: Maximum number of past incidents to return (default 3, at most 10).
    """
    lim = int(_clamp(limit, 1, 10))
    target_svc = (service_name or SERVICE_NAME or "").strip().lower()
    q_lower = (query or "").lower().strip()
    words = [w for w in re.split(r"\W+", q_lower) if len(w) > 2]

    recalled_list = []
    seen_ids = set()

    # 1. Query Hindsight memory if available
    try:
        from . import cycle
        mem = cycle.get_memory()
        if getattr(mem, "enabled", False):
            inc_dummy = {"fingerprints": [], "service": target_svc}
            res = mem.recall(inc_dummy, query or target_svc)
            for m in getattr(res, "matches", []):
                recalled_list.append({
                    "incident_id": m.incident_id,
                    "service": target_svc,
                    "summary": m.text,
                    "confidence": round(m.score, 2),
                    "working_fix": m.fix,
                    "failed_actions": m.failed or [],
                    "source": "hindsight",
                })
                seen_ids.add(m.incident_id)
    except Exception as exc:
        log.debug("Hindsight memory recall in tools skipped (%s)", exc)

    # 2. Query historical resolved incidents from database
    try:
        from db import incidents
        from .memory import split_actions
        candidates = incidents.list_all(status="RESOLVED", limit=50)
        scored = []
        for inc in candidates:
            inc_id = inc["id"]
            if inc_id in seen_ids:
                continue
            svc = (inc.get("service") or "").lower()
            if target_svc and svc and target_svc != svc:
                continue

            text_corpus = " ".join([
                inc.get("rca_text") or "",
                " ".join(inc.get("evidence") or []),
                inc.get("context_sample") or "",
                inc.get("service") or "",
            ]).lower()

            match_score = 0
            if words:
                matched_words = [w for w in words if w in text_corpus]
                match_score = len(matched_words) / max(len(words), 1)
            else:
                match_score = 0.5

            if match_score > 0 or not words:
                scored.append((match_score, inc))

        scored.sort(key=lambda x: x[0], reverse=True)
        for score, inc in scored[:max(0, lim - len(recalled_list))]:
            fix, failed = split_actions(inc.get("actions") or [], set(config.ALLOWED_ACTIONS))
            rca_snippet = (inc.get("rca_text") or "No diagnosis").strip()
            if len(rca_snippet) > 300:
                rca_snippet = rca_snippet[:300] + "..."
            recalled_list.append({
                "incident_id": inc["id"],
                "service": inc.get("service"),
                "summary": "; ".join(inc.get("evidence") or []) or "Telemetry incident",
                "root_cause_summary": rca_snippet,
                "confidence": round(score, 2) if words else 1.0,
                "working_fix": fix,
                "failed_actions": failed,
                "source": "local_store",
            })
    except Exception as exc:
        log.exception("Local incident memory recall failed: %s", exc)

    return {
        "ok": True,
        "count": len(recalled_list),
        "query": query,
        "service": target_svc,
        "past_incidents": recalled_list,
        "note": "Use past incidents as verified historical precedent. If a working fix resolved this exact issue before, prioritize proposing that action.",
    }


# ─── Exported Tools List ─────────────────────────────────────────────────────

ALL_TOOLS = [
    # SQLite event store
    get_error_rate,
    get_latency,
    get_recent_logs,
    get_logs,
    get_logs_for_service,
    get_slow_requests,
    get_traces,
    get_traces_for_service,
    get_service_health,
    # External observability APIs
    query_service_metrics,
    query_service_logs,
    query_service_traces,
    get_trace_by_id,
    # Documentation RAG
    search_docs,
    # Temporal Memory & Hindsight
    recall_past_incidents,
]
