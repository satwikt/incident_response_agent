"""Observability & Remediation tools for the SRE Copilot (Google ADK)."""

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from .config import (
    SERVICE_NAME,
    PROMETHEUS_URL,
    LOKI_URL,
    TEMPO_URL,
    APP_HEALTH_URL,
    REFERENCE_DOCS_PATH,
    _PROJECT_ROOT,
)

log = logging.getLogger("copilot.tools")


def _prom_vector_by_route(payload: dict) -> dict:
    out = {}
    for row in payload.get("data", {}).get("result", []) or []:
        route = row.get("metric", {}).get("http_route") or "(unlabelled)"
        raw = row.get("value", [None, "0"])[1]
        try:
            out[route] = float(raw)
        except (TypeError, ValueError):
            out[route] = 0.0
    return out


def _prom_query(query: str) -> dict:
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query})
        resp.raise_for_status()
        return resp.json()


def get_error_rate(minutes: int = 5) -> dict:
    """Fetch request error counts and error rates by HTTP route from Prometheus for the given window in minutes.

    All counts and the error_percent calculation are scoped to the
    specified window using increase() — no all-time cumulative values.

    Args:
        minutes: Lookback window in minutes (default 5).
    """
    try:
        # Use increase() for both numerator and denominator so error_percent
        # reflects only the current poll window, not all-time counters.
        err_q  = f'sum by (http_route) (increase(request_outcomes_total{{outcome="error"}}[{minutes}m]))'
        ok_q   = f'sum by (http_route) (increase(request_outcomes_total{{outcome="success"}}[{minutes}m]))'

        errors    = _prom_vector_by_route(_prom_query(err_q))
        successes = _prom_vector_by_route(_prom_query(ok_q))

        routes = sorted(set(errors) | set(successes))
        by_route = []
        total_errors  = 0.0
        total_success = 0.0
        for route in routes:
            e = errors.get(route, 0.0)
            s = successes.get(route, 0.0)
            total_errors  += e
            total_success += s
            denom = e + s
            error_pct = (100.0 * e / denom) if denom > 0 else 0.0
            by_route.append(
                {
                    "http_route": route,
                    "error_count": round(e, 2),
                    "success_count": round(s, 2),
                    "error_percent": round(error_pct, 2),
                }
            )

        return {
            "source": "prometheus",
            "metric": "request_outcomes_total",
            "window_minutes": minutes,
            "total_error_count": round(total_errors, 2),
            "total_success_count": round(total_success, 2),
            "by_route": by_route,
        }
    except Exception as exc:
        log.exception("get_error_rate failed")
        return {"error": str(exc), "source": "prometheus"}


def get_latency(minutes: int = 5) -> dict:
    """Fetch 95th percentile (p95) HTTP latency in milliseconds per route from Prometheus.

    Two queries are executed:
    1. Properly labelled routes (http_route != "") — included as-is.
    2. Unlabelled routes (http_route == "") with non-4xx status — included as
       '(unlabelled)' to surface legitimate endpoints with incomplete OTel setup.
       True 404/405 unmatched noise is excluded.

    Args:
        minutes: Lookback window in minutes (default 5).
    """
    try:
        # Query 1: routes with a proper http_route label
        labelled_q = (
            f"histogram_quantile(0.95, sum(rate("
            f'http_server_duration_milliseconds_bucket{{http_route!=""}}[{minutes}m]'
            f")) by (le, http_route))"
        )

        # Query 2: unlabelled routes that are NOT client errors (404/405).
        # These are legitimate endpoints whose OTel instrumentation didn't
        # capture the route template — we still want their latency visible.
        unlabelled_q = (
            f"histogram_quantile(0.95, sum(rate("
            f'http_server_duration_milliseconds_bucket{{http_route="",http_response_status_code!~"4[0-9][0-9]"}}[{minutes}m]'
            f")) by (le))"
        )

        by_route: dict = _prom_vector_by_route(_prom_query(labelled_q))

        # Merge unlabelled result under the '(unlabelled)' key
        unlabelled_payload = _prom_query(unlabelled_q)
        for row in unlabelled_payload.get("data", {}).get("result", []) or []:
            raw = row.get("value", [None, "0"])[1]
            try:
                val = float(raw)
            except (TypeError, ValueError):
                val = 0.0
            # Merge: take the max if multiple unlabelled series exist
            existing = by_route.get("(unlabelled)", 0.0)
            by_route["(unlabelled)"] = max(existing, val)

        rows = [
            {
                "http_route": route,
                "p95_latency_ms": round(val, 2) if val == val else None,
            }
            for route, val in by_route.items()
        ]
        return {
            "source": "prometheus",
            "metric": "http_server_duration_milliseconds",
            "percentile": "p95",
            "window_minutes": minutes,
            "by_route": rows,
            "unit": "milliseconds",
        }
    except Exception as exc:
        log.exception("get_latency failed")
        return {"error": str(exc), "source": "prometheus"}


def get_recent_logs(keyword: Optional[str] = None, limit: int = 20, minutes: int = 10) -> dict:
    """Fetch application log lines from Loki for the specified time window.
    
    Args:
        keyword: Optional search keyword/pattern to filter log lines (e.g., 'error', 'ZeroDivisionError', 'NoneType').
        limit: Max number of log lines to return (default 20).
        minutes: Lookback duration in minutes (default 10).
    """
    try:
        if keyword:
            logql = f'{{service_name="{SERVICE_NAME}"}} |~ "(?i){keyword}"'
        else:
            logql = f'{{service_name="{SERVICE_NAME}"}}'

        end_ns = int(time.time() * 1_000_000_000)
        start_ns = end_ns - int(minutes * 60 * 1_000_000_000)

        params = {
            "query": logql,
            "limit": str(max(limit * 2, limit)),
            "start": str(start_ns),
            "end": str(end_ns),
            "direction": "backward",
        }

        with httpx.Client(timeout=15) as client:
            resp = client.get(f"{LOKI_URL}/loki/api/v1/query_range", params=params)
            resp.raise_for_status()
            payload = resp.json()

        rows = []
        for stream in payload.get("data", {}).get("result", []) or []:
            for ts, line in stream.get("values", []) or []:
                text = (line or "").strip()
                if text.startswith("{") and '"body"' in text:
                    try:
                        obj = json.loads(text)
                        text = obj.get("body") or text
                    except Exception:
                        pass
                rows.append(text)

        lines = rows[:limit]
        return {
            "source": "loki",
            "logql": logql,
            "window_minutes": minutes,
            "limit": limit,
            "keyword": keyword or "(all logs)",
            "match_count": len(lines),
            "sample_lines": lines,
        }
    except Exception as exc:
        log.exception("get_recent_logs failed")
        return {"error": str(exc), "source": "loki"}


def get_slow_traces(threshold_ms: int = 500, minutes: int = 5) -> dict:
    """Search Tempo for distributed trace samples that exceeded a duration threshold in milliseconds.

    Results are scoped to the specified look-back window so that only traces
    from the current poll cycle are returned — not historical incidents.

    Args:
        threshold_ms: Minimum duration in milliseconds to consider slow (default 500).
        minutes:      Look-back window in minutes (default 5).
    """
    try:
        end_s   = int(time.time())
        start_s = end_s - int(minutes * 60)

        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{TEMPO_URL}/api/search",
                params={
                    "tags": f"service.name={SERVICE_NAME}",
                    "minDuration": f"{threshold_ms}ms",
                    "start": start_s,
                    "end": end_s,
                },
            )
            resp.raise_for_status()
            payload = resp.json()

        traces = payload.get("traces") or []
        samples = [
            {
                "traceID": t.get("traceID"),
                "rootServiceName": t.get("rootServiceName"),
                "rootTraceName": t.get("rootTraceName"),
                "durationMs": t.get("durationMs"),
            }
            for t in traces[:10]
        ]
        return {
            "source": "tempo",
            "service": SERVICE_NAME,
            "window_minutes": minutes,
            "min_duration_ms": threshold_ms,
            "trace_count": len(traces),
            "samples": samples,
        }
    except Exception as exc:
        log.exception("get_slow_traces failed")
        return {"error": str(exc), "source": "tempo"}


def get_service_health() -> dict:
    """Check the operational status of the primary application via HTTP health check endpoint."""
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(APP_HEALTH_URL)
            return {
                "service": SERVICE_NAME,
                "status": "up" if resp.status_code == 200 else "degraded",
                "http_status": resp.status_code,
            }
    except Exception as exc:
        return {
            "service": SERVICE_NAME,
            "status": "down",
            "error": str(exc),
        }


def initialize_rag() -> None:
    """Initialize the RAG vector store at application startup."""
    store_dir = str(_PROJECT_ROOT / "data" / "chroma_store")
    docs_dir = _PROJECT_ROOT / "data" / "reference_docs"
    readme_path = _PROJECT_ROOT / "README.md"
    
    # Check if we already have the store populated
    if os.path.exists(store_dir):
        log.info(f"Vector store already exists at {store_dir}. Skipping initialization.")
        return

    log.info("Initializing vector store from markdown files...")
    
    all_final_chunks = []
    
    headers_to_split_on = [("#", "h1"), ("##", "h2"), ("###", "h3")]
    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    recur_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)

    # Gather files
    files_to_process = []
    if readme_path.exists():
        files_to_process.append(readme_path)
        
    if docs_dir.exists():
        for file in docs_dir.glob("**/*.md"):
            files_to_process.append(file)
            
    if not files_to_process:
        log.warning("No markdown files found to initialize RAG.")
        return

    try:
        for file_path in files_to_process:
            text = file_path.read_text(encoding="utf-8")
            if not text.strip():
                continue
                
            header_chunks = header_splitter.split_text(text)
            
            # Add metadata about source file
            for chunk in header_chunks:
                chunk.metadata["source"] = file_path.name
                
            final_chunks = recur_splitter.split_documents(header_chunks)
            all_final_chunks.extend(final_chunks)

        if not all_final_chunks:
            log.warning("Markdown files resulted in no chunks. Skipping RAG initialization.")
            return

        embeddings_model = GoogleGenerativeAIEmbeddings(model="gemini-embedding-2")

        vectorStore = Chroma(
            collection_name="References",
            embedding_function=embeddings_model,
            persist_directory=store_dir
        )

        vectorStore.add_documents(all_final_chunks)
        log.info(f"Successfully initialized vector store with {len(all_final_chunks)} chunks from {len(files_to_process)} files.")
    except Exception as e:
        log.error(f"Failed to initialize RAG store: {e}")


def search_docs(query: str) -> dict:
    """Search the reference documentation using RAG.
    
    Args:
        query: The search query string.
    """
    store_dir = str(_PROJECT_ROOT / "data" / "chroma_store")
    
    try:
        embeddings_model = GoogleGenerativeAIEmbeddings(model="gemini-embedding-2")
        vectorStore = Chroma(
            collection_name="References",
            embedding_function=embeddings_model,
            persist_directory=store_dir
        )
        
        docs = vectorStore.similarity_search(query, k=5)
        
        matches = []
        for doc in docs:
            matches.append({
                "content": doc.page_content,
                "metadata": doc.metadata
            })
            
        return {
            "source": "rag_docs",
            "query": query,
            "match_count": len(matches),
            "matches": matches,
        }
    except Exception as exc:
        log.exception("search_docs failed")
        return {
            "source": "rag_docs",
            "query": query,
            "error": str(exc),
            "matches": [],
        }
