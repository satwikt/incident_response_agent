"""Shared helpers for mapping backend payloads to Copilot evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional

_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_ALIASES = {"WARN": "WARNING", "ERR": "ERROR", "FATAL": "CRITICAL", "TRACE": "DEBUG"}


def normalize_level(value: Any, default: str = "INFO") -> str:
    """Return a Copilot-valid level, falling back to the supplied default."""
    if value is None:
        return default
    level = str(value).strip().upper()
    level = _ALIASES.get(level, level)
    return level if level in _LEVELS else default


def pick_service(labels: Mapping[str, Any], override: Optional[str] = None) -> str:
    """Resolve the service identity, preferring an explicit operator override."""
    if override and override.strip():
        return override.strip()
    for key in ("service_name", "service.name", "service", "job"):
        value = labels.get(key)
        if value:
            return str(value)
    raise ValueError("service label is required")


def stable_id(prefix: str, *parts: Any) -> str:
    """Deterministic event id so re-polling the same evidence dedupes."""
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:32]}"


def as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pick_attr(labels: Mapping[str, Any], *keys: str) -> Any:
    """Return the first present value among ``keys``.

    The Astronomy Shop exposes the same attribute under two spellings: OTel
    semantic conventions on spans (`http.response.status_code`) and the
    Prometheus exporter's underscored form on metrics
    (`http_response_status_code`). Both are accepted so evidence is not
    silently missing the attribute that drives detection.
    """
    for key in keys:
        value = labels.get(key)
        if value not in (None, ""):
            return value
    return None
