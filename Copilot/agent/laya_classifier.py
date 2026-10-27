"""Laya Decision Engine: fast, non-generative System 1 decision engine for classification and agent routing.

Tasks:
1. classify_incident_context: compares current incident runtime context against historical context
   recalled from memory. Returns 'same_context' (routes to Nemotron Nano) or 'different_context'
   (routes to Nemotron Super).
2. classify_chat_intent: classifies user chat queries into 'rca' (Reactive RCA Agent) or 'query'
   (Reactive Query Agent).

Resilience:
Attempts to utilize `laya.Router()` when the `laya` package is installed. If `laya` is unavailable
(e.g., in offline or minimalist test environments), it falls back to a deterministic, rule-based
heuristic classifier to ensure zero crashes and consistent routing.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

log = logging.getLogger("copilot.laya")

# Regex patterns for context extraction
_RELEASE_PATTERN = re.compile(r"\b(?:release|version)[\s:=]+([vV]?\d+(?:\.\d+)*[a-zA-Z0-9_-]*)", re.IGNORECASE)
_CONFIG_REV_PATTERN = re.compile(r"\b(?:config(?:_rev)?|revision)[\s:=]+([a-zA-Z0-9_-]+)", re.IGNORECASE)

# Keywords for intent detection
_RCA_KEYWORDS = {
    "why", "root cause", "root-cause", "rootcause", "rca", "diagnose", "diagnosis",
    "fix", "remediate", "remediation", "broken", "failing", "failed", "crash",
    "outage", "incident", "investigate", "resolve", "recommend", "solve",
    "what happened", "what went wrong", "what is wrong", "what's wrong", "troubleshoot",
    "degraded", "down", "issue", "problem", "explain error", "cause", "postmortem",
    # inflections: whole-word matching would otherwise miss them
    "crashing", "crashed", "crashes", "downtime", "outages", "broke", "unavailable", "timeouts", "timing out",
}

# A request that opens with a lookup verb is a telemetry lookup unless it also asks a diagnostic question
# ("show failed requests" is a lookup, "show me why checkout is down" is not).
_LOOKUP_START = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:show|get|fetch|list|display|query|search|find|give|print|dump)\b")
_DIAGNOSTIC_WORDS = (
    "why", "root cause", "root-cause", "rootcause", "rca", "diagnose", "diagnosis", "fix", "remediate", "remediation",
    "recommend", "solve", "resolve", "troubleshoot", "investigate", "postmortem", "what happened", "what went wrong",
    "what is wrong", "what's wrong", "explain error", "cause",
)

_QUERY_KEYWORDS = {
    "log", "logs", "trace", "traces", "metric", "metrics", "latency", "p95",
    "error rate", "error-rate", "error_rate", "traffic", "health", "status",
    "show", "get", "fetch", "list", "query", "check", "display", "search",
    "doc", "docs", "documentation", "events", "requests", "slow",
}


# Runtime-context facts that change on every event or chat and say nothing about the cause.
_VOLATILE_KEYS = {"source", "trace_id", "span_id", "traceid", "spanid", "ts", "timestamp", "request_id", "chat_session", "id"}
_KV_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*([:=])\s*([^\s,;|]+)")
# "key: value" is only read as a runtime fact for these keys (prose like "active: declining" is not one);
# "key=value" is accepted for any key.
_COLON_FACT_KEYS = {"release", "version", "config", "config_rev", "revision", "pool", "leaked_mb", "fallback",
                    "memory", "region", "build", "image", "commit", "flag"}
_TOOL_PREFIXES = ("get_", "query_", "recall_", "search_")
_SIGNATURE_PATTERNS = (
    re.compile(r"\b[A-Za-z_][\w.]*(?:Error|Exception|Timeout|Exhausted)\b"),   # exception types
    re.compile(r"(?<![\d,.:])\b[45]\d\d\b(?![,.:]?\d)"),                                                 # HTTP status codes
    re.compile(r"\b(?:GET|POST|PUT|DELETE|PATCH)\s+/[\w/\-{}]*"),                  # routes
    re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b"),                                      # snake_case names (faults, tables)
)


def _context_facts(text: str) -> tuple[dict[str, str], str]:
    """(key=value runtime facts, remaining free text) of a context string, without volatile ids."""
    facts: dict[str, str] = {}
    for m in _KV_PATTERN.finditer(text):
        key, sep, value = m.group(1).lower(), m.group(2), m.group(3).strip(".,;)`").lower()
        if key in _VOLATILE_KEYS or (sep == ":" and key not in _COLON_FACT_KEYS):
            continue
        facts[key] = value.lstrip("v") if key in ("release", "version") else value
    attrs = parse_context_attributes(text)
    if attrs["release"]:
        facts.setdefault("release", attrs["release"].lower().lstrip("v"))
    if attrs["config_rev"]:
        facts.setdefault("config_rev", attrs["config_rev"].lower())
    rest = re.sub(r"\s+", " ", _KV_PATTERN.sub(" ", text)).strip(" ,;|").lower()
    return facts, rest


def _is_status_code(token: str) -> bool:
    return token.isdigit() and len(token) == 3


def _is_specific_cause(token: str) -> bool:
    return not _is_status_code(token) and not token.startswith(("get ", "post ", "put ", "delete ", "patch "))


def _evidence_signature(evidence: Optional[List[str]]) -> set[str]:
    """Exception types, status codes, routes and fault/table names mentioned in the evidence (tool names excluded)."""
    text = " ".join(str(e) for e in (evidence or []))
    tokens: set[str] = set()
    for pat in _SIGNATURE_PATTERNS:
        for m in pat.finditer(text):
            tok = re.sub(r"\s+", " ", m.group(0)).lower()
            if not tok.startswith(_TOOL_PREFIXES):
                tokens.add(tok)
    return tokens


def parse_context_attributes(context_str: str) -> dict[str, Optional[str]]:
    """Extract known runtime context attributes (release, config_rev) from a text string."""
    attrs: dict[str, Optional[str]] = {"release": None, "config_rev": None}
    if not context_str:
        return attrs

    rel_match = _RELEASE_PATTERN.search(context_str)
    if rel_match:
        attrs["release"] = rel_match.group(1).strip()

    cfg_match = _CONFIG_REV_PATTERN.search(context_str)
    if cfg_match:
        attrs["config_rev"] = cfg_match.group(1).strip()

    return attrs


class LayaClassifier:
    """System 1 decision router wrapping laya.Router with deterministic fallback."""

    def __init__(self) -> None:
        self._router: Any = None
        self._laya_available: bool = False
        self._init_laya()

    def _init_laya(self) -> None:
        """Attempt to lazily initialize the laya.Router engine."""
        try:
            import laya  # type: ignore[import-untyped]
            if hasattr(laya, "Router"):
                self._router = laya.Router()
                self._laya_available = True
                log.info("Laya decision router initialized successfully.")
        except (ImportError, Exception) as exc:
            self._router = None
            self._laya_available = False
            log.info("Laya library not loaded (%s). Using deterministic heuristic fallback.", exc)

    @property
    def is_laya_available(self) -> bool:
        return self._laya_available

    def classify_incident_context(
        self,
        current_context: str,
        current_evidence: Optional[List[str]] = None,
        past_context: str = "",
        past_evidence: Optional[List[str]] = None,
    ) -> str:
        """Classify context match between current incident and a past recalled incident.

        Returns:
            "same_context" if the runtime environment, release, and configuration match.
            "different_context" if there is a mismatch in release, config revision, or evidence signatures.
        """
        # If laya is installed and initialized, query the Router
        if self._laya_available and self._router is not None:
            try:
                curr_ev_str = "; ".join(current_evidence or [])
                past_ev_str = "; ".join(past_evidence or [])
                state_desc = (
                    f"Current context: {current_context}. Current evidence: {curr_ev_str}. "
                    f"Past context: {past_context}. Past evidence: {past_ev_str}."
                )
                questions = {
                    "context_match": {
                        "type": "choice",
                        "options": ["same_context", "different_context"],
                    }
                }
                res = self._router.decide(state=state_desc, questions=questions)
                if isinstance(res, dict) and res.get("context_match") in ("same_context", "different_context"):
                    return str(res["context_match"])
            except Exception as exc:
                log.warning("Laya router query failed for context classification (%s); using fallback.", exc)

        # Deterministic heuristic fallback
        return self._heuristic_classify_context(
            current_context=current_context,
            current_evidence=current_evidence,
            past_context=past_context,
            past_evidence=past_evidence,
        )

    def _heuristic_classify_context(
        self,
        current_context: str,
        current_evidence: Optional[List[str]] = None,
        past_context: str = "",
        past_evidence: Optional[List[str]] = None,
    ) -> str:
        curr = (current_context or "").strip()
        past = (past_context or "").strip()
        has_evidence = bool(current_evidence) and bool(past_evidence)
        if not curr and not past and not has_evidence:
            return "different_context"

        ckv, crest = _context_facts(curr)
        pkv, prest = _context_facts(past)
        ctx_same: Optional[bool] = None   # None: no comparable runtime facts (free text, ids only, or nothing)
        if ckv or pkv:
            # Every runtime fact counts (release, config_rev, pool, leaked_mb, fallback, ...); a fact known on one side
            # only is a mismatch, and so is a context known on one side only.
            if ckv != pkv:
                return "different_context"
            ctx_same = True
        elif crest and crest == prest:
            ctx_same = True

        sig_cur = _evidence_signature(current_evidence)
        sig_past = _evidence_signature(past_evidence)
        if sig_cur and sig_past:
            # Two failures on the same route share the route; what tells them apart is the status code and the named
            # cause (exception type, fault or table name). When both sides state one and they never agree, it is a
            # different incident (503 pool exhaustion vs 500 payment failure).
            for kind in (_is_status_code, _is_specific_cause):
                a = {t for t in sig_cur if kind(t)}
                b = {t for t in sig_past if kind(t)}
                if a and b and not (a & b):
                    return "different_context"
            return "same_context" if len(sig_cur & sig_past) / len(sig_cur) >= 0.5 else "different_context"
        return "same_context" if ctx_same else "different_context"

    def classify_chat_intent(self, user_message: str) -> str:
        """Classify user chat intent into 'rca' or 'query'.

        Returns:
            "query" for telemetry lookups, logs, metrics, traces, and documentation searches.
            "rca" for incident diagnosis, root cause questions, and remediation requests.
        """
        msg = (user_message or "").strip().lower()
        if not msg:
            return "query"

        # If laya is installed and initialized, query the Router
        if self._laya_available and self._router is not None:
            try:
                questions = {
                    "intent": {
                        "type": "choice",
                        "options": ["rca", "query"],
                    }
                }
                res = self._router.decide(state=f"User prompt: {user_message}", questions=questions)
                if isinstance(res, dict) and res.get("intent") in ("rca", "query"):
                    return str(res["intent"])
            except Exception as exc:
                log.warning("Laya router query failed for intent classification (%s); using fallback.", exc)

        # Deterministic heuristic fallback
        return self._heuristic_classify_intent(msg)

    def _heuristic_classify_intent(self, msg: str) -> str:
        if _LOOKUP_START.match(msg) and not any(re.search(r"\b" + re.escape(w) + r"\b", msg) for w in _DIAGNOSTIC_WORDS):
            return "query"

        # Check for RCA indicators first
        for kw in _RCA_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", msg):
                return "rca"

        # Statements like "service is slow" without explicit query verbs are diagnostic (RCA)
        if "slow" in msg and not any(v in msg for v in ("show", "get", "fetch", "list", "display", "query")):
            return "rca"

        # Check for query indicators
        for kw in _QUERY_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", msg):
                return "query"

        # Default fallback
        return "rca" if any(w in msg for w in ("wrong", "issue", "problem", "happened", "failing", "error")) else "query"


# Singleton instance
_laya_classifier: Optional[LayaClassifier] = None


def get_laya_classifier() -> LayaClassifier:
    """Return the singleton instance of LayaClassifier."""
    global _laya_classifier
    if _laya_classifier is None:
        _laya_classifier = LayaClassifier()
    return _laya_classifier


def set_laya_classifier(classifier: Optional[LayaClassifier]) -> None:
    """Override singleton classifier instance (useful for testing)."""
    global _laya_classifier
    _laya_classifier = classifier
