"""Catalog & Cart Microservice for the Astronomy Shop."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from fastapi import Depends
from common.auth import require_chaos_key
from common.faults import fault_registry
from common.models import AddToCartRequest, Cart, CartItem, FaultToggleRequest, Product
from common.telemetry import setup_telemetry

log = logging.getLogger("astronomy.catalog")

app = FastAPI(title="Astronomy Shop - Catalog & Cart Service", version="1.0.0")
tracer = setup_telemetry(app, "catalog")

# In-memory leak buffer for memory leak fault simulation
_LEAK_BUFFER: List[bytes] = []

# Initial Astronomy Catalog
_INITIAL_PRODUCTS: List[Product] = [
    Product(
        id="tel-001",
        name="Stargazer Pro 200mm Reflector Telescope",
        category="Telescopes",
        price_usd=1299.00,
        description="High-aperture Newtonian reflector with parabolic primary mirror and dual-speed focuser.",
        image_url="/static/images/telescope.png",
        stock=25,
    ),
    Product(
        id="cam-002",
        name="DeepSky 4K Planetary Astrophotography Camera",
        category="Cameras",
        price_usd=450.00,
        description="Back-illuminated CMOS sensor with ultra-low readout noise and high frame rate planetary capture.",
        image_url="/static/images/camera.png",
        stock=40,
    ),
    Product(
        id="flt-003",
        name="Solar Spectrum H-Alpha 1.25\" Filter",
        category="Filters",
        price_usd=220.00,
        description="Narrowband 656.3nm hydrogen-alpha solar viewing filter for solar prominences and chromosphere detail.",
        image_url="/static/images/filter.png",
        stock=60,
    ),
    Product(
        id="mnt-004",
        name="Star Adventurer Motorized Equatorial Tracking Mount",
        category="Mounts",
        price_usd=390.00,
        description="Precision tracking mount for astrophotography with integrated polar scope and Wi-Fi control.",
        image_url="/static/images/mount.png",
        stock=30,
    ),
    Product(
        id="met-005",
        name="Campo del Cielo Certified Iron Meteorite Specimen (450g)",
        category="Specimens",
        price_usd=85.00,
        description="Authentic iron-nickel octahedrite meteorite fragment discovered in Gran Chaco, Argentina.",
        image_url="/static/images/meteorite.png",
        stock=15,
    ),
    Product(
        id="opt-006",
        name="NightVision Stargazing Monocular (8x42 ED)",
        category="Optics",
        price_usd=180.00,
        description="Extra-low dispersion optics with dielectric phase coatings for pinpoint dark-sky cluster resolution.",
        image_url="/static/images/monocular.png",
        stock=50,
    ),
]

# In-memory stores (synced with Postgres if available)
_PRODUCTS: Dict[str, Product] = {p.id: p for p in _INITIAL_PRODUCTS}
_CARTS: Dict[str, Cart] = {}


async def _apply_faults():
    """Evaluate and apply active runtime faults."""
    # 1. High Latency
    if fault_registry.is_active("high_latency"):
        params = fault_registry.get_params("high_latency")
        delay = float(params.get("delay_seconds", 2.0))
        log.warning("Applying injected latency: %.2f seconds", delay)
        await asyncio.sleep(delay)

    # 2. Error Spike
    if fault_registry.is_active("error_spike"):
        params = fault_registry.get_params("error_spike")
        status = int(params.get("status_code", 503))
        log.error("Applying injected error spike: HTTP %d", status)
        raise HTTPException(status_code=status, detail="Injected error spike: Service Unavailable")

    # 3. Memory Leak
    if fault_registry.is_active("memory_leak"):
        params = fault_registry.get_params("memory_leak")
        mb = int(params.get("mb_per_req", 20))
        _LEAK_BUFFER.append(b"x" * (mb * 1024 * 1024))
        log.warning("Injected memory leak: allocated %d MB (total leaked blocks: %d)", mb, len(_LEAK_BUFFER))


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "catalog",
        "products_count": len(_PRODUCTS),
        "active_carts": len(_CARTS),
        "leaked_buffers_count": len(_LEAK_BUFFER),
    }


@app.get("/products", response_model=List[Product])
async def list_products(category: Optional[str] = None):
    with tracer.start_as_current_span("catalog.list_products") as span:
        await _apply_faults()
        span.set_attribute("catalog.total_products", len(_PRODUCTS))
        if category:
            span.set_attribute("catalog.filter_category", category)
            return [p for p in _PRODUCTS.values() if p.category.lower() == category.lower()]
        return list(_PRODUCTS.values())


@app.get("/products/{product_id}", response_model=Product)
async def get_product(product_id: str):
    with tracer.start_as_current_span("catalog.get_product") as span:
        await _apply_faults()
        span.set_attribute("catalog.product_id", product_id)
        product = _PRODUCTS.get(product_id)
        if not product:
            log.warning("Product not found: %s", product_id)
            raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
        return product


@app.get("/cart/{cart_id}", response_model=Cart)
async def get_cart(cart_id: str):
    with tracer.start_as_current_span("cart.get") as span:
        await _apply_faults()
        span.set_attribute("cart.id", cart_id)
        if cart_id not in _CARTS:
            _CARTS[cart_id] = Cart(id=cart_id, items=[], total_usd=0.0)
        return _CARTS[cart_id]


@app.post("/cart/{cart_id}/items", response_model=Cart)
async def add_item_to_cart(cart_id: str, item_req: AddToCartRequest):
    with tracer.start_as_current_span("cart.add_item") as span:
        await _apply_faults()
        span.set_attribute("cart.id", cart_id)
        span.set_attribute("cart.product_id", item_req.product_id)
        span.set_attribute("cart.quantity", item_req.quantity)

        product = _PRODUCTS.get(item_req.product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {item_req.product_id} not found")

        if cart_id not in _CARTS:
            _CARTS[cart_id] = Cart(id=cart_id, items=[], total_usd=0.0)

        cart = _CARTS[cart_id]
        existing = next((i for i in cart.items if i.product_id == item_req.product_id), None)
        if existing:
            existing.quantity += item_req.quantity
        else:
            cart.items.append(
                CartItem(
                    product_id=product.id,
                    quantity=item_req.quantity,
                    price_usd=product.price_usd,
                    product_name=product.name,
                )
            )

        cart.total_usd = sum(i.price_usd * i.quantity for i in cart.items)
        log.info("Cart updated: %s (items=%d, total=$%.2f)", cart_id, len(cart.items), cart.total_usd)
        return cart


@app.delete("/cart/{cart_id}/items/{product_id}", response_model=Cart)
async def remove_item_from_cart(cart_id: str, product_id: str):
    with tracer.start_as_current_span("cart.remove_item") as span:
        span.set_attribute("cart.id", cart_id)
        span.set_attribute("cart.product_id", product_id)
        if cart_id in _CARTS:
            cart = _CARTS[cart_id]
            cart.items = [i for i in cart.items if i.product_id != product_id]
            cart.total_usd = sum(i.price_usd * i.quantity for i in cart.items)
            return cart
        raise HTTPException(status_code=404, detail="Cart not found")


# ─── Faults API ──────────────────────────────────────────────────────────────

@app.get("/admin/faults")
async def get_faults():
    return fault_registry.get_all()


@app.post("/admin/faults", dependencies=[Depends(require_chaos_key)])
async def set_fault(req: FaultToggleRequest):
    return fault_registry.set_fault(req.fault_type, req.enabled, **req.params)


@app.post("/admin/faults/reset", dependencies=[Depends(require_chaos_key)])
async def reset_faults():
    global _LEAK_BUFFER
    _LEAK_BUFFER.clear()
    return fault_registry.reset_all()
