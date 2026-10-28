"""Thread-safe runtime fault injection registry for Astronomy Shop services."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict

log = logging.getLogger("astronomy.faults")


class FaultRegistry:
    """In-memory thread-safe registry of active runtime fault injection flags."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_faults: Dict[str, Dict[str, Any]] = {
            "payment_failure": {"enabled": False, "rate": 1.0, "status_code": 500},
            "payment_db_fail": {"enabled": False, "table_name": "payment_transactions_v2"},
            # silent failure: the payment is captured but the order is never persisted; the customer sees HTTP 200
            "order_persist_fail": {"enabled": False, "rate": 1.0},
            "high_latency": {"enabled": False, "delay_seconds": 2.5},
            "db_pool_exhaustion": {"enabled": False, "leak_count": 20},
            "image_delay": {"enabled": False, "delay_seconds": 3.0},
            "memory_leak": {"enabled": False, "mb_per_req": 20},
            "error_spike": {"enabled": False, "rate": 0.8, "status_code": 503},
        }

    def is_active(self, fault_name: str) -> bool:
        with self._lock:
            fault = self._active_faults.get(fault_name.lower())
            return bool(fault and fault.get("enabled", False))

    def get_params(self, fault_name: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._active_faults.get(fault_name.lower(), {}))

    def set_fault(self, fault_name: str, enabled: bool, **params: Any) -> Dict[str, Any]:
        key = fault_name.lower()
        with self._lock:
            if key not in self._active_faults:
                self._active_faults[key] = {"enabled": enabled}
            else:
                self._active_faults[key]["enabled"] = enabled
            self._active_faults[key].update(params)
            result = dict(self._active_faults[key])
        log.warning("Fault state updated: %s -> enabled=%s (params=%s)", key, enabled, params)
        return result

    def reset_all(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            for k in self._active_faults:
                self._active_faults[k]["enabled"] = False
            snapshot = {k: dict(v) for k, v in self._active_faults.items()}
        log.warning("All runtime faults have been RESET to nominal.")
        return snapshot

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._active_faults.items()}


# Global singleton instance
fault_registry = FaultRegistry()
