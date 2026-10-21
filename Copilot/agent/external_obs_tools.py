"""Parameterized, injection-proof external observability tools for Prometheus, Loki, Tempo, and Flagd.

Security & Integrity Guarantee:
Tools DO NOT accept raw PromQL, LogQL, or TraceQL queries from the LLM.
All queries use pre-defined, hardened query templates with strict parameter sanitization
(service names, clamped time windows, and restricted log levels).
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from agent import config

log = logging.getLogger("copilot.external_obs_tools")

# Strict regex for service name validation
_SERVICE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
_TRACE_ID_RE = re.compile(r"^[0-9a-fA-F]{16,32}$")
_ALLOWED_LOG_LEVELS = {"ERROR", "WARN", "WARNING", "INFO", "DEBUG", "ALL"}

# Fixed Prometheus templates
_METRIC_TEMPLATES: Dict[str, str] = {
    "errors": 'sum(rate(http_server_request_duration_seconds_count{service_name="{service}",http_response_status_code=~"5.."}[5m]))',
    "request_rate": 'sum(rate(http_server_request_duration_seconds_count{service_name="{service}"}[5m]))',
    "latency_p95": 'histogram_quantile(0.95, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{service_name="{service}"}[5m])))',
    "latency_p50": 'histogram_quantile(0.50, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{service_name="{service}"}[5m])))',
}


def _success(source: str, data: Any, window: Optional[Dict[str, Any]] = None, truncated: bool = False) -> dict:
    return {
        "ok": True,
        "source": source,
        "window": window,
        "data": data,
        "truncated": truncated,
        "error": None,
    }


def _error(source: str, code: str, message: str) -> dict:
    return {
        "ok": False,
        "source": source,
        "window": None,
        "data": [],
        "truncated": False,
        "error": {"code": code, "message": message},
    }


def _http_get_json(url: str, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "AI-Incident-Copilot/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body.decode("utf-8"))


# ─── 1. Prometheus Metrics Tool ──────────────────────────────────────────────

def query_service_metrics(
    service_name: str,
    metric_type: str = "errors",
    minutes: float = 10.0,
) -> dict:
    """Query live service metrics from Prometheus using fixed, injection-proof query templates.

    Args:
        service_name: Name of the microservice (e.g., 'checkout', 'frontend', 'payment').
        metric_type: Metric query type: 'errors' (5xx rate), 'request_rate' (rps), 'latency_p95', or 'latency_p50'.
        minutes: Lookback range in minutes (1 to 1440, default 10).
    """
    svc = (service_name or "").strip()
    if not svc or not _SERVICE_NAME_RE.match(svc):
        return _error("prometheus", "invalid_argument", f"Invalid service_name '{svc}'. Must be alphanumeric with dashes/underscores.")

    mtype = (metric_type or "errors").strip().lower()
    if mtype not in _METRIC_TEMPLATES:
        allowed = ", ".join(_METRIC_TEMPLATES.keys())
        return _error("prometheus", "invalid_argument", f"Unknown metric_type '{mtype}'. Allowed: {allowed}")

    try:
        mins = max(1.0, min(float(minutes), 1440.0))
    except (ValueError, TypeError):
        mins = 10.0

    prom_template = _METRIC_TEMPLATES[mtype]
    promql = prom_template.replace("{service}", svc)

    now_sec = time.time()
    start_sec = now_sec - (mins * 60)
    step_sec = max(15, int((mins * 60) / 60))

    params = urllib.parse.urlencode({
        "query": promql,
        "start": f"{start_sec:.3f}",
        "end": f"{now_sec:.3f}",
        "step": f"{step_sec}s",
    })
    target_url = f"{config.PROMETHEUS_URL}/api/v1/query_range?{params}"
    window_info = {"start_min_ago": mins, "step_seconds": step_sec}

    try:
        resp = _http_get_json(target_url, timeout=5.0)
        status = resp.get("status")
        if status != "success":
            err_msg = resp.get("error", "Unknown Prometheus query error")
            return _error("prometheus", "backend_unavailable", err_msg)

        results = []
        for item in resp.get("data", {}).get("result", []):
            metric_labels = item.get("metric", {})
            values = item.get("values", [])
            results.append({
                "metric_type": mtype,
                "service": svc,
                "labels": metric_labels,
                "datapoint_count": len(values),
                "latest_value": values[-1][1] if values else None,
                "series": values[-10:],  # Return top 10 recent points
            })
        return _success("prometheus", results, window=window_info)
    except Exception as exc:
        log.warning("Prometheus query failed (%s)", exc)
        return _error("prometheus", "backend_unavailable", f"Prometheus query failed: {exc}")


# ─── 2. Loki Logs Tool ───────────────────────────────────────────────────────

def query_service_logs(
    service_name: str,
    level: str = "ERROR",
    minutes: float = 10.0,
    limit: int = 50,
) -> dict:
    """Query recent application logs from Loki using fixed, injection-proof LogQL templates.

    Args:
        service_name: Name of the microservice (e.g. 'checkout', 'frontend').
        level: Filter log level ('ERROR', 'WARN', 'INFO', 'DEBUG').
        minutes: Lookback range in minutes (1 to 1440, default 10).
        limit: Maximum number of log lines to return (1 to 100, default 50).
    """
    svc = (service_name or "").strip()
    if not svc or not _SERVICE_NAME_RE.match(svc):
        return _error("loki", "invalid_argument", f"Invalid service_name '{svc}'.")

    lvl = (level or "ALL").strip().upper()
    if lvl not in _ALLOWED_LOG_LEVELS:
        return _error("loki", "invalid_argument", f"Invalid level '{lvl}'. Allowed: {', '.join(_ALLOWED_LOG_LEVELS)}")

    try:
        mins = max(1.0, min(float(minutes), 1440.0))
        lim = max(1, min(int(limit), 100))
    except (ValueError, TypeError):
        mins, lim = 10.0, 50

    if lvl == "ALL":
        logql = f'{{service_name="{svc}"}}'
    else:
        logql = f'{{service_name="{svc}"}} |= "{lvl}"'
    now_ns = int(time.time() * 1e9)
    start_ns = now_ns - int(mins * 60 * 1e9)

    params = urllib.parse.urlencode({
        "query": logql,
        "start": str(start_ns),
        "end": str(now_ns),
        "limit": str(lim),
        "direction": "backward",
    })
    target_url = f"{config.LOKI_URL}/loki/api/v1/query_range?{params}"

    try:
        resp = _http_get_json(target_url, timeout=5.0)
        entries = []
        truncated = False
        for stream in resp.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for val in stream.get("values", []):
                ts, raw_line = val[0], val[1]
                line_text = str(raw_line)
                if len(line_text) > 2000:
                    line_text = line_text[:2000] + " ...[truncated]"
                    truncated = True
                entries.append({
                    "timestamp": ts,
                    "service": svc,
                    "level": lvl,
                    "line": line_text,
                    "trace_id": labels.get("trace_id") or labels.get("traceId"),
                })
                if len(entries) >= lim:
                    break
            if len(entries) >= lim:
                break

        return _success("loki", entries, window={"minutes": mins, "limit": lim}, truncated=truncated)
    except Exception as exc:
        log.warning("Loki query failed (%s)", exc)
        return _error("loki", "backend_unavailable", f"Loki query failed: {exc}")


# ─── 3. Tempo / Jaeger Traces Tool ───────────────────────────────────────────

def query_service_traces(
    service_name: str,
    minutes: float = 10.0,
    limit: int = 20,
) -> dict:
    """Search for recent traces of a microservice from Tempo or Jaeger.

    Args:
        service_name: Name of the microservice (e.g., 'checkout', 'payment').
        minutes: Lookback range in minutes (1 to 1440, default 10).
        limit: Maximum number of traces to return (1 to 50, default 20).
    """
    svc = (service_name or "").strip()
    if not svc or not _SERVICE_NAME_RE.match(svc):
        return _error("tempo", "invalid_argument", f"Invalid service_name '{svc}'.")

    try:
        mins = max(1.0, min(float(minutes), 1440.0))
        lim = max(1, min(int(limit), 50))
    except (ValueError, TypeError):
        mins, lim = 10.0, 20

    now_sec = int(time.time())
    start_sec = now_sec - int(mins * 60)

    # 1. Try Tempo API
    tempo_params = urllib.parse.urlencode({
        "tags": f"service.name={svc}",
        "start": str(start_sec),
        "end": str(now_sec),
        "limit": str(lim),
    })
    tempo_url = f"{config.TEMPO_URL}/api/search?{tempo_params}"

    try:
        resp = _http_get_json(tempo_url, timeout=5.0)
        traces = []
        for t in resp.get("traces", []):
            traces.append({
                "trace_id": t.get("traceID") or t.get("trace_id"),
                "root_service": t.get("rootServiceName") or svc,
                "root_name": t.get("rootTraceName"),
                "duration_ms": float(t.get("durationMs", 0)),
                "start_time": t.get("startTimeUnixNano"),
            })
        return _success("tempo", traces, window={"minutes": mins, "limit": lim})
    except Exception as tempo_exc:
        log.info("Tempo query attempt failed (%s), trying Jaeger fallback.", tempo_exc)

    # 2. Fallback to Jaeger API
    try:
        jaeger_params = urllib.parse.urlencode({
            "service": svc,
            "lookback": f"{int(mins)}m",
            "limit": str(lim),
        })
        jaeger_url = f"{config.JAEGER_URL}/api/traces?{jaeger_params}"
        resp = _http_get_json(jaeger_url, timeout=5.0)
        traces = []
        for t in resp.get("data", []):
            traces.append({
                "trace_id": t.get("traceID"),
                "root_service": svc,
                "spans_count": len(t.get("spans", [])),
            })
        return _success("jaeger", traces, window={"minutes": mins, "limit": lim})
    except Exception as jaeger_exc:
        log.warning("Trace search backends unavailable (%s)", jaeger_exc)
        return _error("traces", "backend_unavailable", f"Trace query failed: {jaeger_exc}")


def get_trace_by_id(trace_id: str) -> dict:
    """Fetch details and span hierarchy for a specific trace ID.

    Args:
        trace_id: 16 to 32 hex character trace identifier.
    """
    tid = (trace_id or "").strip()
    if not tid or not _TRACE_ID_RE.match(tid):
        return _error("tempo", "invalid_argument", f"Invalid trace_id '{tid}'. Must be 16-32 hexadecimal characters.")

    # 1. Try Tempo API
    tempo_url = f"{config.TEMPO_URL}/api/traces/{tid}"
    try:
        resp = _http_get_json(tempo_url, timeout=5.0)
        return _success("tempo", {"trace_id": tid, "trace_data": resp})
    except Exception as tempo_exc:
        log.info("Tempo get_trace failed (%s), trying Jaeger fallback.", tempo_exc)

    # 2. Try Jaeger API
    jaeger_url = f"{config.JAEGER_URL}/api/traces/{tid}"
    try:
        resp = _http_get_json(jaeger_url, timeout=5.0)
        return _success("jaeger", {"trace_id": tid, "trace_data": resp})
    except Exception as jaeger_exc:
        log.warning("get_trace failed on both backends (%s)", jaeger_exc)
        return _error("traces", "backend_unavailable", f"Could not retrieve trace '{tid}': {jaeger_exc}")


# ─── 4. Flagd Feature Flags (Read-Only) ───────────────────────────────────────

def get_feature_flags() -> dict:
    """Read the current active feature flags and fault injections from the flagd API (read-only)."""
    target_url = f"{config.FLAGD_URL}/feature/api/read"
    try:
        resp = _http_get_json(target_url, timeout=4.0)
        flags = resp.get("flags") or resp
        return _success("flagd", flags)
    except Exception as exc:
        log.warning("flagd read failed (%s)", exc)
        return _error("flagd", "backend_unavailable", f"flagd API unavailable: {exc}")
