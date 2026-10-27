"""Incident memory on top of Hindsight.

What is stored (one Hindsight document per resolved incident, keyed by the incident id, so retaining again replaces it):
  * episodic: what happened, the evidence, the runtime context at the time, the diagnosis, how long it took;
  * procedural: which actions were tried, which one worked, and which did not (negative memory).

How it is found: exact recall by fingerprint tag (``fp:<fingerprint>``, ``all_strict`` semantics via ``any`` over the
incident's fingerprints) plus similarity search over the evidence text. Results are kept only above a relevance
cut-off on the FINAL score (semantic score alone is not discriminating: unrelated text still scores 0.34 to 0.46).

Recalled memory is a HYPOTHESIS: the caller verifies it against live evidence before acting on it.

Everything here fails open: if Hindsight is unreachable, slow or memory is switched off, the incident pipeline still works
(alert, diagnosis) and simply has no memory.
"""

from __future__ import annotations

import inspect
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

log = logging.getLogger("copilot.memory")

# Hindsight operation states (lower-case): a retain is only stored once it reaches one of OP_DONE.
OP_DONE = ("completed", "succeeded", "success", "done")
OP_FAILED = ("failed", "error")

_FIX_TAG = "fix:"
_FAILED_TAG = "failed:"


@dataclass
class Recalled:
    incident_id: str
    text: str
    exact: bool
    score: float
    fix: Optional[str] = None
    failed: List[str] = field(default_factory=list)
    source: str = "live"
    tags: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"incident_id": self.incident_id, "text": self.text, "exact": self.exact, "score": round(self.score, 4),
                "fix": self.fix, "failed": self.failed, "source": self.source}


@dataclass
class RecallResult:
    matches: List[Recalled] = field(default_factory=list)
    degraded: bool = False          # Hindsight could not be reached (fail-open)
    error: str = ""


@dataclass
class RetainOutcome:
    status: str                     # retained | indexed | skipped | failed
    operation_id: Optional[str] = None
    detail: str = ""


class MemoryStore(Protocol):
    enabled: bool

    def recall(self, incident: dict, query: str, exclude_id: Optional[str] = None) -> RecallResult: ...
    def retain(self, incident: dict, narrative: str, actions: List[dict], fix: Optional[str],
               failed: List[str], source: str = "live") -> RetainOutcome: ...
    def operation_status(self, operation_id: str) -> str: ...


class NullMemory:
    """Memory switched off: no calls, no results. Used for the memory-off half of the benchmark and as a safe default."""

    enabled = False

    def recall(self, incident: dict, query: str, exclude_id: Optional[str] = None) -> RecallResult:
        return RecallResult()

    def retain(self, incident: dict, narrative: str, actions: List[dict], fix: Optional[str],
               failed: List[str], source: str = "live") -> RetainOutcome:
        return RetainOutcome("skipped", detail="memory is off")

    def operation_status(self, operation_id: str) -> str:
        return "unknown"


# ── pure helpers ──────────────────────────────────────────────────────────────────────────────

def context_of(message: Optional[str]) -> str:
    """The runtime-context suffix of an event message, unpacked to clean text if formatted as JSON."""
    if not message or " | " not in message:
        return ""
    raw = message.rsplit(" | ", 1)[1].strip()
    if "{" in raw:
        try:
            start_idx = raw.index("{")
            end_idx = raw.rindex("}") + 1 if "}" in raw else len(raw)
            json_str = raw[start_idx:end_idx]
            import json
            pj = json.loads(json_str) if json_str.endswith("}") else {}
            if not pj:
                m_msg = re.search(r'"(?:message|msg)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)', raw)
                m_exc = re.search(r'"exception"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)', raw)
                msg = m_msg.group(1).replace('\\"', '"') if m_msg else ""
                exc = f" ({m_exc.group(1).replace('\\\"', '\"')})" if m_exc else ""
                if msg:
                    return f"{msg}{exc}"[:200]
            else:
                msg = pj.get("message") or pj.get("msg") or ""
                exc = f" ({pj.get('exception')})" if pj.get("exception") else ""
                if msg:
                    return f"{msg}{exc}"[:200]
        except Exception:
            pass
    return raw[:200]


def build_narrative(incident: dict, actions: List[dict], fix: Optional[str], failed: List[str], context: str,
                    minutes_to_resolve: Optional[float], source: str = "live") -> str:
    """The text Hindsight extracts facts from. Structured on purpose: it must read well as a memory."""
    fps = incident.get("fingerprints") or []
    exc = sorted({f.get("sig", "") for f in fps if f.get("kind") == "error_rate" and f.get("sig")})
    routes = sorted({f["route"] for f in fps if f.get("route")})
    rca = (incident.get("rca_text") or "").strip().replace("\n", " ")[:500]
    lines = [
        f"[{incident['id']} svc={incident['service']} outcome=resolved source={source}]",
        "Symptoms: " + ("; ".join(incident.get("evidence") or []) or "not recorded") + ".",
    ]
    if routes:
        lines.append("Affected routes: " + ", ".join(routes) + ".")
    if exc:
        lines.append("Exception type: " + ", ".join(exc) + ".")
    if context:
        lines.append("Runtime context during the incident: " + context + ".")
    if rca and "Automatic diagnosis unavailable" not in rca:
        lines.append("Diagnosis at the time: " + rca)
    if actions:
        lines.append("Actions tried in order: " + ", ".join(a["action"] for a in actions) + ".")
    if failed:
        lines.append("These did NOT resolve it: " + ", ".join(failed) + ".")
    lines.append(("Fix that worked: " + fix + ".") if fix else "Fix that worked: none recorded (it recovered without a logged action).")
    if minutes_to_resolve is not None:
        lines.append(f"Time to resolve: {minutes_to_resolve:.1f} minutes.")
    return "\n".join(lines)


def split_actions(actions: List[dict], allowed: Optional[set] = None) -> tuple[Optional[str], List[str]]:
    """(fix that worked, actions that did not). Prioritizes explicit 'worked: True' from operator feedback,
    falling back to the last action taken before recovery for unannotated actions."""
    if not actions:
        return None, []

    filtered = [a for a in actions if not allowed or a.get("action") in allowed]
    if not filtered and not allowed:
        filtered = actions

    # 1. Prioritize explicit operator feedback (worked=True / worked=False)
    worked_names = [a["action"] for a in filtered if a.get("worked") is True and a.get("action")]
    failed_names = [a["action"] for a in filtered if a.get("worked") is False and a.get("action")]

    if worked_names:
        fix = worked_names[-1]
        failed = [f for f in failed_names if f != fix]
        return fix, failed

    # 2. Fallback for UNANNOTATED actions only (e.g. raw ops journal): the last one is the tentative fix.
    #    An action the operator marked worked=False is never the fix.
    names = [a["action"] for a in filtered if a.get("action") and a.get("worked") is None]
    failed = list(dict.fromkeys(failed_names))
    if not names:
        return None, failed
    fix = names[-1]
    for n in names[:-1]:
        if n != fix and n not in failed:
            failed.append(n)
    return fix, [f for f in failed if f != fix]


# Models often wrap the line in markdown: "**Proposed action:** x", "**Proposed action**: x", "Proposed action: **x**", "`x`".
_PROPOSED = re.compile(r"proposed\s+action\s*[*_]*\s*:\s*[*_`\s]*([a-z0-9_-]+)", re.IGNORECASE)


def parse_proposed_action(text: str, allowed: Optional[set] = None) -> Optional[str]:
    """The proposed action named on a 'Proposed action: X' line, or None if none/unrecognized/not allowed."""
    for m in _PROPOSED.finditer(text or ""):
        name = m.group(1).lower().strip()
        if name in ("none", "noop", "null", "undefined"):
            return None
        if allowed is not None:
            if name in allowed:
                return name
            continue
        if re.match(r"^[a-z0-9_-]+$", name):
            return name
    return None


def _tag_value(tags: List[str], prefix: str) -> Optional[str]:
    for t in tags:
        if t.startswith(prefix):
            return t[len(prefix):]
    return None


class HindsightMemory:
    """Hindsight-backed memory. ``client`` is a hindsight_client.Hindsight (or anything with the same methods).

    The client's sync methods each spin up an asyncio event loop internally and lazily create an aiohttp session
    bound to whichever event loop was current on first use. Callers here span the watcher's default thread-pool
    executor and the diagnosis queue's worker, which are DIFFERENT OS threads across ticks; calling the client from
    a second thread/loop after its session bound to the first raises "Timeout context manager should be used
    inside a task" (aiohttp session pinned to a dead loop). Routing every client call through one dedicated,
    single-worker executor keeps the client on one thread (and therefore one event loop) for the app's lifetime,
    regardless of which thread calls in.
    """

    enabled = True

    def __init__(self, client: Any, bank_id: str, min_score: float = 0.05, max_results: int = 3,
                 status_fn: Optional[Any] = None, executor: Optional[ThreadPoolExecutor] = None) -> None:
        self._c = client
        self.bank_id = bank_id
        self.min_score = min_score
        self.max_results = max_results
        self._status_fn = status_fn          # (operation_id) -> str; injected so tests need no async client
        self._ready = False
        self._executor = executor            # None in tests (fake client, no real event loop involved)

    def _call(self, fn, *args, **kwargs):
        """Run a client call on the dedicated memory thread when one is configured; inline otherwise (tests)."""
        if self._executor is None:
            return fn(*args, **kwargs)
        return self._executor.submit(fn, *args, **kwargs).result()

    def ensure_bank(self) -> None:
        """Create the bank if needed. Retried on every call until it succeeds (Hindsight may still be starting)."""
        if self._ready:
            return
        try:
            self._call(self._c.create_bank, bank_id=self.bank_id,
                       reflect_mission="Incident response memory for an on-call engineer.", enable_observations=False)
            self._ready = True
        except Exception as exc:  # noqa: BLE001 - recall/retain surface real problems; we try again next time
            log.info("ensure_bank: %s", type(exc).__name__)

    # -- recall ----------------------------------------------------------------------------

    def _recall_defaults(self) -> dict:
        """Optional recall arguments the installed client sends as JSON null unless told otherwise.

        hindsight-client >= 1.1 serializes the unset ``prefer_observations`` as ``null`` and the Hindsight server answers
        HTTP 422 ("Input should be a valid boolean"), which made every recall fail open to "no memory". Passing an explicit
        boolean works with old and new clients; a client without the argument is left alone.
        """
        try:
            params = inspect.signature(self._c.recall).parameters
        except (TypeError, ValueError):
            return {}
        return {"prefer_observations": False} if "prefer_observations" in params else {}

    def recall(self, incident: dict, query: str, exclude_id: Optional[str] = None) -> RecallResult:
        fp_tags = [f"fp:{f['fp']}" for f in incident.get("fingerprints") or []]
        found: Dict[str, Recalled] = {}
        self.ensure_bank()
        extra = self._recall_defaults()
        try:
            if fp_tags:
                resp = self._call(self._c.recall, bank_id=self.bank_id, query=query, tags=fp_tags,
                                  tags_match="any_strict", max_tokens=2000, **extra)
                self._collect(resp, found, exact=True, exclude_id=exclude_id)
            resp = self._call(self._c.recall, bank_id=self.bank_id, query=query, tags=["kind:incident"],
                              tags_match="all_strict", max_tokens=2000, **extra)
            self._collect(resp, found, exact=False, exclude_id=exclude_id)
        except Exception as exc:  # noqa: BLE001 - fail open
            log.warning("memory recall failed (%s); continuing without memory", type(exc).__name__)
            return RecallResult(degraded=True, error=type(exc).__name__)
        ranked = sorted(found.values(), key=lambda r: (not r.exact, -r.score))
        return RecallResult(matches=ranked[: self.max_results])

    def _collect(self, resp: Any, found: Dict[str, Recalled], exact: bool, exclude_id: Optional[str]) -> None:
        for item in getattr(resp, "results", None) or []:
            doc = getattr(item, "document_id", None)
            scores = getattr(item, "scores", None)
            final = getattr(scores, "final", None)
            if not doc or doc == exclude_id or final is None or final < self.min_score:
                continue
            tags = list(getattr(item, "tags", None) or [])
            prior = found.get(doc)
            if prior is not None and (prior.exact or not exact) and prior.score >= final:
                continue
            found[doc] = Recalled(
                incident_id=doc, text=(getattr(item, "text", "") or "")[:400], exact=exact or (prior.exact if prior else False),
                score=float(final), fix=_tag_value(tags, _FIX_TAG),
                failed=list(dict.fromkeys(t[len(_FAILED_TAG):] for t in tags if t.startswith(_FAILED_TAG))),
                source=_tag_value(tags, "source:") or "live", tags=tags)

    # -- retain ----------------------------------------------------------------------------

    def retain(self, incident: dict, narrative: str, actions: List[dict], fix: Optional[str],
               failed: List[str], source: str = "live") -> RetainOutcome:
        self.ensure_bank()
        tags = [f"fp:{f['fp']}" for f in incident.get("fingerprints") or []]
        # an incident that is still being worked on (operator reported a failed fix) is not a resolved precedent
        outcome = "active" if incident.get("status") in ("OPEN", "ACKED", "REMEDIATING") else "resolved"
        tags += [f"svc:{incident['service']}", "kind:incident", f"outcome:{outcome}", f"source:{source}"]
        if fix:
            tags.append(_FIX_TAG + fix)
        tags += [_FAILED_TAG + f for f in failed]
        try:
            resp = self._call(
                self._c.retain, bank_id=self.bank_id, content=narrative, document_id=incident["id"],
                update_mode="replace", tags=tags, metadata={"incident_id": incident["id"], "source": source},
                retain_async=True, operation_id=str(uuid.uuid4()))
        except Exception as exc:  # noqa: BLE001
            return RetainOutcome("failed", detail=type(exc).__name__)
        op = getattr(resp, "operation_id", None)
        return RetainOutcome("retained", operation_id=op)

    def operation_status(self, operation_id: str) -> str:
        if not operation_id or self._status_fn is None:
            return "unknown"
        try:
            return str(self._call(self._status_fn, operation_id))
        except Exception:  # noqa: BLE001
            return "unknown"


def make_memory(url: str, bank_id: str, mode: str, min_score: float = 0.05) -> Any:
    """Build the configured memory: Hindsight when mode is 'on' and a URL is set, otherwise NullMemory."""
    if (mode or "").lower() != "on" or not url:
        return NullMemory()
    from hindsight_client import Hindsight  # imported lazily: only needed when memory is on
    from hindsight_client.hindsight_client import _run_async

    client = Hindsight(base_url=url)
    # One dedicated worker thread for every call this client ever makes (see HindsightMemory's docstring): its sync
    # methods lazily bind an aiohttp session to whichever event loop is current on first use, and callers here span
    # several different threads (the watcher's default executor, the diagnosis queue) across ticks.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindsight-client")

    def status(op_id: str) -> str:
        res = _run_async(client.operations.get_operation_status(bank_id=bank_id, operation_id=op_id))
        return str(getattr(res, "status", "unknown"))

    return HindsightMemory(client, bank_id, min_score=min_score, status_fn=status, executor=executor)
