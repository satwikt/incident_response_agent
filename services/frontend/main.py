"""Frontend Gateway & Web Storefront Service for Astronomy Shop."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import random
import time
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
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
from common.models import AddToCartRequest, CheckoutRequest, FaultToggleRequest
from common.telemetry import setup_telemetry

log = logging.getLogger("astronomy.frontend")

CATALOG_URL = os.getenv("CATALOG_SERVICE_URL", "http://catalog:5001")
CHECKOUT_URL = os.getenv("CHECKOUT_SERVICE_URL", "http://checkout:5002")
ENABLE_SYNTHETIC_TRAFFIC = os.getenv("ENABLE_SYNTHETIC_TRAFFIC", "true").lower() in ("1", "true", "yes")

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def _background_traffic_generator():
    """Simulate steady realistic customer browsing and purchasing traffic."""
    log.info("Starting background synthetic traffic generator...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                # 1. Browse products
                await client.get(f"http://127.0.0.1:8080/api/products")
                await asyncio.sleep(random.uniform(1.0, 3.0))

                # 2. Add random product to cart
                cart_id = f"sim-{random.randint(100, 999)}"
                p_id = random.choice(["tel-001", "cam-002", "flt-003", "mnt-004", "met-005"])
                await client.post(
                    f"http://127.0.0.1:8080/api/cart/{cart_id}/items",
                    json={"product_id": p_id, "quantity": 1},
                )
                await asyncio.sleep(random.uniform(1.0, 2.5))

                # 3. Occasional checkout
                if random.random() < 0.6:
                    await client.post(
                        f"http://127.0.0.1:8080/api/checkout",
                        json={
                            "cart_id": cart_id,
                            "customer_email": f"customer-{random.randint(10, 99)}@astronomy.local",
                            "shipping_address": "Observatory Station 5",
                            "payment_method": "credit_card",
                        },
                    )
            except Exception as exc:
                # Expected when faults are injected
                pass
            await asyncio.sleep(random.uniform(2.0, 5.0))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if ENABLE_SYNTHETIC_TRAFFIC:
        task = asyncio.create_task(_background_traffic_generator())
    yield
    if task:
        task.cancel()


app = FastAPI(title="Astronomy Shop - Frontend Gateway", version="1.0.0", lifespan=lifespan)
tracer = setup_telemetry(app, "frontend")


# ─── UI Routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_storefront():
    index_file = STATIC_DIR / "index.html"
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.get("/faults", response_class=HTMLResponse)
async def serve_faults_dashboard():
    faults_file = STATIC_DIR / "faults.html"
    return HTMLResponse(content=faults_file.read_text(encoding="utf-8"))


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "frontend"}


# ─── Proxy API Routes ────────────────────────────────────────────────────────

@app.get("/api/products")
async def proxy_list_products(request: Request):
    headers = {}
    inject(headers)
    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            resp = await client.get(f"{CATALOG_URL}/products", headers=headers)
            return RawResponse(content=resp.content, status_code=resp.status_code, media_type="application/json")
        except Exception as exc:
            log.error("Failed to reach catalog service: %s", exc)
            raise HTTPException(status_code=503, detail="Catalog Service Unavailable")


@app.get("/api/products/{product_id}")
async def proxy_get_product(product_id: str):
    headers = {}
    inject(headers)
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(f"{CATALOG_URL}/products/{product_id}", headers=headers)
        return RawResponse(content=resp.content, status_code=resp.status_code, media_type="application/json")


@app.get("/api/cart/{cart_id}")
async def proxy_get_cart(cart_id: str):
    headers = {}
    inject(headers)
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(f"{CATALOG_URL}/cart/{cart_id}", headers=headers)
        return RawResponse(content=resp.content, status_code=resp.status_code, media_type="application/json")


@app.post("/api/cart/{cart_id}/items")
async def proxy_add_to_cart(cart_id: str, item_req: AddToCartRequest):
    headers = {}
    inject(headers)
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.post(
            f"{CATALOG_URL}/cart/{cart_id}/items",
            headers=headers,
            json=item_req.model_dump(),
        )
        return RawResponse(content=resp.content, status_code=resp.status_code, media_type="application/json")


@app.post("/api/checkout")
async def proxy_checkout(req: CheckoutRequest):
    headers = {}
    inject(headers)
    async with httpx.AsyncClient(timeout=12.0) as client:
        try:
            resp = await client.post(
                f"{CHECKOUT_URL}/checkout",
                headers=headers,
                json=req.model_dump(),
            )
            return RawResponse(content=resp.content, status_code=resp.status_code, media_type="application/json")
        except Exception as exc:
            log.error("Failed to reach checkout service: %s", exc)
            raise HTTPException(status_code=503, detail=f"Checkout service unreachable: {exc}")


# ─── Static Images with Fault Simulation ──────────────────────────────────────

@app.get("/static/images/{image_name}")
async def serve_image(image_name: str):
    with tracer.start_as_current_span("frontend.serve_image") as span:
        span.set_attribute("image.name", image_name)
        if fault_registry.is_active("image_delay"):
            params = fault_registry.get_params("image_delay")
            delay = float(params.get("delay_seconds", 3.0))
            log.warning("Image delay fault active: sleeping %.2f seconds for %s", delay, image_name)
            await asyncio.sleep(delay)
        return {"image": image_name, "status": "loaded"}


# ─── Faults Sync Endpoints ───────────────────────────────────────────────────

@app.get("/api/faults")
async def get_all_faults():
    return fault_registry.get_all()


@app.post("/api/faults")
async def set_fault(req: FaultToggleRequest):
    # Update local frontend registry
    local_state = fault_registry.set_fault(req.fault_type, req.enabled, **req.params)
    
    # Propagate to Catalog and Checkout services
    async with httpx.AsyncClient(timeout=4.0) as client:
        for url in [CATALOG_URL, CHECKOUT_URL]:
            try:
                await client.post(f"{url}/admin/faults", json=req.model_dump())
            except Exception as e:
                log.warning("Could not sync fault to %s: %s", url, e)
    return local_state


@app.post("/api/faults/reset")
async def reset_all_faults():
    fault_registry.reset_all()
    async with httpx.AsyncClient(timeout=4.0) as client:
        for url in [CATALOG_URL, CHECKOUT_URL]:
            try:
                await client.post(f"{url}/admin/faults/reset")
            except Exception as e:
                log.warning("Could not sync fault reset to %s: %s", url, e)
    return {"status": "all_faults_cleared"}
