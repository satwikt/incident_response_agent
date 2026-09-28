"""Incident lifecycle: fingerprint breaches, dedupe them into incidents, and resolve them on recovery.

One call per watcher cycle (:func:`process_cycle`). It is deterministic given the database state and its inputs, so
the whole lifecycle can be tested without any LLM, network or clock.

Rules
* A **fingerprint** identifies a problem as (service, breach kind, route, exception signature). Volatile details such
  as counts, percentages and timestamps are not part of it, so the same problem keeps the same fingerprint.
* A breach whose fingerprint matches an **active** incident is attached to it: no new incident, no new page.
  Distinct fingerprints appearing together in one cycle join a single incident (one page, not one per route).
* A breach matching a **recently resolved** incident opens a NEW incident linked with ``recurrence_of``.
* An incident recovers only after ``recovery_windows`` consecutive **healthy** windows. A window counts as healthy only if
  every route the incident touched has enough traffic and is comfortably under its thresholds (hysteresis). A route
  with too little traffic is *unknown*: the streak neither advances nor resets.
* At most ``max_open`` incidents are active per service, so a flood of distinct fingerprints cannot open unlimited ones.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set

from agent.detect import KIND_PRIORITY, Breach
from db import incidents

log = logging.getLogger("copilot.incidents")


@dataclass
class IncidentConfig:
    recovery_windows: int = 3
    recurrence_window_ms: int = 24 * 3_600_000
    renotify_ms: int = 30 * 60_000
    max_open: int = 5


@dataclass
class Action:
    kind: str                      # opened | reminder | resolved | regressed | capped
    incident: dict
    detail: str = ""


def _hash(service: str, kind: str, route: Optional[str], sig: str) -> str:
    return "fp-" + hashlib.sha1(f"{service}|{kind}|{route or '*'}|{sig}".encode()).hexdigest()[:10]


def make_fingerprints(service: str, breaches: Sequence[Breach],
                      signature_for: Callable[[Optional[str], str], str]) -> List[dict]:
    """Fingerprints for the breaches, ordered so the first one is the incident's primary (highest priority)."""
    seen: Dict[str, dict] = {}
    for b in sorted(breaches, key=lambda x: (KIND_PRIORITY.get(x.kind, 9), x.route or "")):
        sig = signature_for(b.route, b.kind) or b.kind
        fp = _hash(service, b.kind, b.route, sig)
        seen.setdefault(fp, {"fp": fp, "kind": b.kind, "route": b.route, "sig": sig})
    return list(seen.values())


def _severity(breaches: Sequence[Breach]) -> str:
    return "critical" if any(b.kind in ("health", "error_rate") for b in breaches) else "warning"


def _route_status(route: Optional[str], healthy: Set[str], global_ok: bool) -> Optional[bool]:
    """True = healthy, None = unknown (not enough data)."""
    if route is None:
        return True if global_ok else None
    return True if route in healthy else None


def process_cycle(service: str, breaches: Sequence[Breach], signature_for: Callable[[Optional[str], str], str],
                  healthy: Set[str], global_traffic_ok: bool, now: int, cfg: IncidentConfig) -> List[Action]:
    actions: List[Action] = []
    active = incidents.list_active(service)
    fps = make_fingerprints(service, breaches, signature_for) if breaches else []
    fp_ids = {f["fp"] for f in fps}
    evidence = [b.text for b in breaches]
    matched: Set[str] = set()

    if breaches:
        match = next((i for i in active if fp_ids & {f["fp"] for f in i["fingerprints"]}), None)
        if match is not None:
            was = match["status"]
            updated = incidents.touch_breach(match["id"], now, fps, evidence, _severity(breaches))
            matched.add(match["id"])
            if was == "MONITORING":
                updated = incidents.transition(match["id"], "OPEN", None, "watcher", now, "breach returned while monitoring")
                actions.append(Action("regressed", updated, "breach returned during monitoring"))
            elif updated["status"] == "OPEN":
                last = updated["last_notified_at"] or updated["opened_at"]
                if now - last >= cfg.renotify_ms:
                    actions.append(Action("reminder", updated))
        else:
            live = [i for i in active if i["status"] != "SUPPRESSED"]
            if len(live) >= cfg.max_open:
                target = live[0]
                incidents.touch_breach(target["id"], now, fps, evidence, _severity(breaches))
                incidents.record_event(target["id"], "capped", "watcher",
                                       f"{len(fps)} new fingerprint(s) attached: incident cap {cfg.max_open} reached", now)
                matched.add(target["id"])
                actions.append(Action("capped", incidents.get(target["id"]) or target,
                                      f"max_open={cfg.max_open} reached"))
                log.warning("Incident cap (%d) reached for %s; attached new fingerprints to %s",
                            cfg.max_open, service, target["id"])
            else:
                prior = incidents.find_recent_resolved(service, fp_ids, now - cfg.recurrence_window_ms)
                created = incidents.create(service, fps, _severity(breaches), evidence, now,
                                           recurrence_of=prior["id"] if prior else None)
                matched.add(created["id"])
                actions.append(Action("opened", created, f"recurrence of {prior['id']}" if prior else ""))

    for inc in active:
        if inc["id"] in matched:
            continue
        statuses = [_route_status(f["route"], healthy, global_traffic_ok) for f in inc["fingerprints"]]
        if any(s is None for s in statuses):
            continue                                   # unknown: hold the streak where it is
        streak = inc["healthy_windows"] + 1
        if streak >= cfg.recovery_windows:
            resolved = incidents.transition(inc["id"], "RESOLVED", None, "auto", now,
                                            f"{streak} consecutive healthy windows")
            actions.append(Action("resolved", resolved, f"{streak} healthy windows"))
        else:
            incidents.set_healthy_windows(inc["id"], streak)
    return actions
