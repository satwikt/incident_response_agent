"""Unit tests for the 3-service Astronomy Shop architecture and fault engine."""

import pytest
from fastapi.testclient import TestClient

import sys
from pathlib import Path
services_dir = Path(__file__).resolve().parent.parent.parent / "services"
if str(services_dir) not in sys.path:
    sys.path.insert(0, str(services_dir))

from catalog.main import app as catalog_app
from checkout.main import app as checkout_app
from frontend.main import app as frontend_app
from common.faults import fault_registry


@pytest.fixture(autouse=True)
def reset_faults_fixture():
    fault_registry.reset_all()
    yield
    fault_registry.reset_all()


def test_catalog_list_and_get_products():
    client = TestClient(catalog_app)
    resp = client.get("/products")
    assert resp.status_code == 200
    products = resp.json()
    assert len(products) >= 5
    assert any("Telescope" in p["name"] for p in products)

    first_id = products[0]["id"]
    p_resp = client.get(f"/products/{first_id}")
    assert p_resp.status_code == 200
    assert p_resp.json()["id"] == first_id


def test_catalog_cart_flow():
    client = TestClient(catalog_app)
    cart_id = "test-cart-1"
    
    # Add item
    add_resp = client.post(f"/cart/{cart_id}/items", json={"product_id": "tel-001", "quantity": 2})
    assert add_resp.status_code == 200
    cart = add_resp.json()
    assert len(cart["items"]) == 1
    assert cart["items"][0]["quantity"] == 2
    assert cart["total_usd"] > 0

    # Get cart
    get_resp = client.get(f"/cart/{cart_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["total_usd"] == cart["total_usd"]


def test_fault_registry_lifecycle():
    assert not fault_registry.is_active("payment_failure")
    fault_registry.set_fault("payment_failure", True, status_code=500)
    assert fault_registry.is_active("payment_failure")
    
    fault_registry.reset_all()
    assert not fault_registry.is_active("payment_failure")


def test_checkout_fault_payment_failure():
    client = TestClient(checkout_app)
    fault_registry.set_fault("payment_failure", True, status_code=500)

    resp = client.post(
        "/checkout",
        json={
            "cart_id": "cart-abc",
            "customer_email": "tester@astro.local",
            "shipping_address": "Lab 1",
            "payment_method": "credit_card",
        },
    )
    assert resp.status_code == 500
    assert "PaymentGatewayException" in resp.json()["detail"]


def test_checkout_fault_payment_db_fail():
    client = TestClient(checkout_app)
    fault_registry.set_fault("payment_db_fail", True, table_name="payment_transactions_v2")

    resp = client.post(
        "/checkout",
        json={
            "cart_id": "cart-abc",
            "customer_email": "tester@astro.local",
            "shipping_address": "Lab 1",
            "payment_method": "credit_card",
        },
    )
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "DatabaseRuntimeError" in detail
    assert "payment_transactions_v2" in detail


def test_checkout_fault_db_pool_exhaustion():
    client = TestClient(checkout_app)
    fault_registry.set_fault("db_pool_exhaustion", True, leak_count=20)

    resp = client.post(
        "/checkout",
        json={
            "cart_id": "cart-abc",
            "customer_email": "tester@astro.local",
            "shipping_address": "Lab 1",
            "payment_method": "credit_card",
        },
    )
    assert resp.status_code == 503
    assert "DatabaseConnectionTimeout" in resp.json()["detail"]


def test_frontend_ui_endpoints():
    client = TestClient(frontend_app)
    # Storefront HTML
    store_resp = client.get("/")
    assert store_resp.status_code == 200
    assert "Deep Space" in store_resp.text

    # Faults Dashboard HTML
    faults_resp = client.get("/faults")
    assert faults_resp.status_code == 200
    assert "Runtime Fault Injection Deck" in faults_resp.text
