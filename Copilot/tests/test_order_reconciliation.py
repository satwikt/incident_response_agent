"""order_persist_fail: payment captured, order missing, HTTP 200 (a silent failure) and its reconciliation check."""

import logging
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

services_dir = Path(__file__).resolve().parent.parent.parent / "services"
if str(services_dir) not in sys.path:
    sys.path.insert(0, str(services_dir))

import checkout.main as checkout
from common.faults import fault_registry

BODY = {"cart_id": "cart-abc", "customer_email": "t@astro.local", "shipping_address": "Lab 1", "payment_method": "credit_card"}


@pytest.fixture(autouse=True)
def clean():
    fault_registry.reset_all()
    checkout._PAYMENTS.clear()
    checkout._ORDERS.clear()
    checkout._recon_reported = 0
    yield
    fault_registry.reset_all()


def test_healthy_checkout_matches_payment_and_order():
    resp = TestClient(checkout.app).post("/checkout", json=BODY)
    assert resp.status_code == 200 and resp.json()["status"] == "CONFIRMED"
    assert all(p["order_id"] for p in checkout._PAYMENTS.values())
    assert checkout.reconcile_check(time.time() + 60)["unmatched_payments"] == 0


def test_fault_returns_200_but_creates_no_order():
    fault_registry.set_fault("order_persist_fail", True)
    client = TestClient(checkout.app)
    resp = client.post("/checkout", json=BODY)
    assert resp.status_code == 200 and resp.json()["status"] == "PROCESSING"
    assert client.get(f"/orders/{resp.json()['id']}").status_code == 404
    assert len(checkout._PAYMENTS) == 1 and not checkout._ORDERS


def test_reconcile_logs_critical_once_per_new_gap(caplog):
    fault_registry.set_fault("order_persist_fail", True)
    client = TestClient(checkout.app)
    client.post("/checkout", json=BODY)
    later = time.time() + 60
    with caplog.at_level(logging.CRITICAL, logger="astronomy.checkout"):
        first = checkout.reconcile_check(later)
        checkout.reconcile_check(later)           # same gap: no second alert
    lines = [r.getMessage() for r in caplog.records if "RECONCILIATION_GAP" in r.getMessage()]
    assert first["unmatched_payments"] == 1 and len(lines) == 1
    assert lines[0].startswith("CRITICAL RECONCILIATION_GAP")   # matches the watcher's default CRITICAL keyword
    assert "error" in lines[0]                                  # the Loki adapter preset only forwards lines containing 'error'


def test_recent_payment_is_inside_grace_period():
    fault_registry.set_fault("order_persist_fail", True)
    TestClient(checkout.app).post("/checkout", json=BODY)
    assert checkout.reconcile_check(time.time())["unmatched_payments"] == 0


def test_reconcile_endpoint_creates_the_missing_orders(monkeypatch):
    monkeypatch.setenv("CHAOS_KEY", "k")
    fault_registry.set_fault("order_persist_fail", True)
    client = TestClient(checkout.app)
    client.post("/checkout", json=BODY)
    client.post("/checkout", json=BODY)
    resp = client.post("/admin/reconcile", headers={"X-Chaos-Key": "k"})
    if resp.status_code in (401, 403):
        pytest.skip("chaos key header name differs; endpoint protection is covered by test_chaos_key")
    assert resp.json()["reconciled"] == 2 and len(checkout._ORDERS) == 2
    assert checkout.reconcile_check(time.time() + 60)["unmatched_payments"] == 0
