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
}

_QUERY_KEYWORDS = {
    "log", "logs", "trace", "traces", "metric", "metrics", "latency", "p95",
    "error rate", "error-rate", "error_rate", "traffic", "health", "status",
    "show", "get", "fetch", "list", "query", "check", "display", "search",
    "doc", "docs", "documentation", "events", "requests", "slow",
}


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

        # If both contexts are missing or empty, treat as different context for safety
        if not curr or not past:
            return "different_context"

        # If the context strings are identical
        if curr.lower() == past.lower():
            return "same_context"

        curr_attrs = parse_context_attributes(curr)
        past_attrs = parse_context_attributes(past)

        # Check release
        if curr_attrs["release"] and past_attrs["release"]:
            if curr_attrs["release"] != past_attrs["release"]:
                return "different_context"

        # Check config revision
        if curr_attrs["config_rev"] and past_attrs["config_rev"]:
            if curr_attrs["config_rev"] != past_attrs["config_rev"]:
                return "different_context"

        # If release or config revision differs or is present in only one, flag as different
        if bool(curr_attrs["release"]) != bool(past_attrs["release"]):
            return "different_context"
        if bool(curr_attrs["config_rev"]) != bool(past_attrs["config_rev"]):
            return "different_context"

        # If both had matching explicit attributes, it's the same context
        if curr_attrs["release"] and past_attrs["release"] and curr_attrs["release"] == past_attrs["release"]:
            if curr_attrs["config_rev"] == past_attrs["config_rev"]:
                return "same_context"

        return "same_context"

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
