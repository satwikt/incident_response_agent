"""Checkout & Order Microservice for Astronomy Shop."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
import httpx
try:
    from opentelemetry import trace
    from opentelemetry.propagate import extract, inject
except ImportError:
    class _DummySpan:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def set_attribute(self, *args):
            pass
        def get_span_context(self):
            return None

    class _DummyTracer:
        def start_as_current_span(self, *args, **kwargs):
            return _DummySpan()

    class _DummyTrace:
        def get_tracer(self, *args, **kwargs):
            return _DummyTracer()
        def get_current_span(self):
            return None

    trace = _DummyTrace()
    extract = lambda h: None
    inject = lambda h: None

from common.faults import fault_registry
from common.models import Cart, CheckoutRequest, FaultToggleRequest, Order
from common.telemetry import setup_telemetry

log = logging.getLogger("astronomy.checkout")

app = FastAPI(title="Astronomy Shop - Checkout & Order Service", version="1.0.0")
tracer = setup_telemetry(app, "checkout")

CATALOG_URL = os.getenv("CATALOG_SERVICE_URL", "http://catalog:5001")

# In-memory orders store
_ORDERS: Dict[str, Order] = {}

# Held connections for DB pool exhaustion simulation
_HELD_CONNECTIONS: List[Any] = []


async def _apply_checkout_faults():
    """Evaluate and trigger active checkout faults."""
    # 1. High Latency
    if fault_registry.is_active("high_latency"):
        params = fault_registry.get_params("high_latency")
        delay = float(params.get("delay_seconds", 3.0))
        log.warning("Checkout latency fault active: sleeping %.2f seconds", delay)
        await asyncio.sleep(delay)

    # 2. Payment Failure
    if fault_registry.is_active("payment_failure"):
        params = fault_registry.get_params("payment_failure")
        code = int(params.get("status_code", 500))
        log.error("Checkout payment_failure fault active: declining transaction with HTTP %d", code)
        raise HTTPException(
            status_code=code,
            detail="PaymentGatewayException: Transaction declined by upstream clearinghouse (timeout)",
        )

    # 2b. Payment DB Fail (Runtime query to non-existing table)
    if fault_registry.is_active("payment_db_fail"):
        params = fault_registry.get_params("payment_db_fail")
        table_name = params.get("table_name", "payment_transactions_v2")
        query_sql = f"SELECT transaction_id, auth_token, amount_usd FROM {table_name} WHERE status = 'PENDING' LIMIT 5"
        log.error("Checkout payment_db_fail fault active: executing query against missing table '%s'", table_name)
        db_url = os.getenv("DATABASE_URL")
        if db_url and db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        try:
            if db_url:
                from sqlalchemy import create_engine, text
                engine = create_engine(db_url, pool_pre_ping=True)
                with engine.connect() as conn:
                    conn.execute(text(query_sql)).fetchall()
            else:
                import sqlite3
                conn = sqlite3.connect(":memory:")
                try:
                    conn.cursor().execute(query_sql)
                finally:
                    conn.close()
        except Exception as exc:
            log.error(
                "Database query to non-existing table '%s' failed: %s (SQL: %s)",
                table_name,
                exc,
                query_sql,
            )
            raise HTTPException(
                status_code=500,
                detail=f"DatabaseRuntimeError: relation '{table_name}' does not exist (failed to query pending payments: {exc})",
            )

    # 3. DB Pool Exhaustion
    if fault_registry.is_active("db_pool_exhaustion"):
        params = fault_registry.get_params("db_pool_exhaustion")
        leak_count = int(params.get("leak_count", 20))
        # Simulate connection exhaustion
        for _ in range(leak_count):
            _HELD_CONNECTIONS.append(time.time())
        log.error(
            "Checkout db_pool_exhaustion fault active: pool capacity exceeded (held=%d). Timeout waiting for available connection.",
            len(_HELD_CONNECTIONS),
        )
        raise HTTPException(
            status_code=503,
            detail="DatabaseConnectionTimeout: Connection pool exhausted (max_connections=20). Queue limit exceeded.",
        )

    # 4. Error Spike
    if fault_registry.is_active("error_spike"):
        log.error("Checkout error_spike fault active: returning 500")
        raise HTTPException(status_code=500, detail="InternalServerError: Checkout processing pipeline failed")


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "checkout",
        "orders_count": len(_ORDERS),
        "held_pool_connections": len(_HELD_CONNECTIONS),
    }


@app.post("/checkout", response_model=Order)
async def process_checkout(req: CheckoutRequest, request: Request):
    # Extract trace context from parent request
    parent_ctx = extract(request.headers)
    
    with tracer.start_as_current_span("checkout.process_order", context=parent_ctx) as order_span:
        current_span = trace.get_current_span()
        current_ctx = current_span.get_span_context() if current_span else None
        trace_id_str = format(current_ctx.trace_id, "032x") if current_ctx and getattr(current_ctx, "trace_id", None) else ""
        if order_span:
            order_span.set_attribute("checkout.cart_id", req.cart_id)
            order_span.set_attribute("checkout.customer", req.customer_email)

        log.info("Processing checkout for cart %s (customer: %s)", req.cart_id, req.customer_email)

        # 1. Fetch Cart from Catalog Service
        with tracer.start_as_current_span("checkout.fetch_cart") as cart_span:
            headers = {}
            inject(headers)
            cart_data = None
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{CATALOG_URL}/cart/{req.cart_id}", headers=headers)
                    if resp.status_code == 200:
                        cart_data = resp.json()
            except Exception as exc:
                log.warning("Could not contact catalog service (%s), using synthetic cart", exc)

            if not cart_data or not cart_data.get("items"):
                # If cart is empty, raise or create dummy order item for demo
                log.warning("Cart %s is empty or not found", req.cart_id)

        # 2. Evaluate Faults
        with tracer.start_as_current_span("checkout.payment_gateway") as pay_span:
            await _apply_checkout_faults()
            pay_span.set_attribute("payment.status", "APPROVED")

        # 3. Create Order
        with tracer.start_as_current_span("checkout.persist_order") as db_span:
            order_id = f"ord-{uuid.uuid4().hex[:8]}"
            items = cart_data.get("items", []) if cart_data else []
            total = float(cart_data.get("total_usd", 1299.00)) if cart_data and cart_data.get("total_usd") else 1299.00

            order = Order(
                id=order_id,
                cart_id=req.cart_id,
                customer_email=req.customer_email,
                shipping_address=req.shipping_address,
                items=items,
                total_usd=total,
                status="CONFIRMED",
                created_at=time.time(),
                trace_id=trace_id_str,
            )
            _ORDERS[order_id] = order
            db_span.set_attribute("order.id", order_id)
            db_span.set_attribute("order.total_usd", total)
            log.info("Order %s confirmed successfully for $%.2f (trace_id=%s)", order_id, total, trace_id_str)

        return order


@app.get("/orders/{order_id}", response_model=Order)
async def get_order(order_id: str):
    with tracer.start_as_current_span("checkout.get_order") as span:
        span.set_attribute("order.id", order_id)
        order = _ORDERS.get(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        return order


# ─── Faults API ──────────────────────────────────────────────────────────────

@app.get("/admin/faults")
async def get_faults():
    return fault_registry.get_all()


@app.post("/admin/faults")
async def set_fault(req: FaultToggleRequest):
    return fault_registry.set_fault(req.fault_type, req.enabled, **req.params)


@app.post("/admin/faults/reset")
async def reset_faults():
    global _HELD_CONNECTIONS
    _HELD_CONNECTIONS.clear()
    return fault_registry.reset_all()
