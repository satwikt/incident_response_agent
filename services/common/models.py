"""Shared Pydantic data models for the Astronomy Shop microservices."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Product(BaseModel):
    id: str
    name: str
    category: str
    price_usd: float
    description: str
    image_url: str
    stock: int = 100


class CartItem(BaseModel):
    product_id: str
    quantity: int = 1
    price_usd: float = 0.0
    product_name: str = ""


class Cart(BaseModel):
    id: str
    items: List[CartItem] = Field(default_factory=list)
    total_usd: float = 0.0


class AddToCartRequest(BaseModel):
    product_id: str
    quantity: int = 1


class CheckoutRequest(BaseModel):
    cart_id: str
    customer_email: str
    shipping_address: str
    payment_method: str = "credit_card"


class Order(BaseModel):
    id: str
    cart_id: str
    customer_email: str
    shipping_address: str
    items: List[CartItem] = Field(default_factory=list)
    total_usd: float
    status: str = "CONFIRMED"
    created_at: float
    trace_id: str = ""


class FaultToggleRequest(BaseModel):
    fault_type: str
    enabled: bool
    params: Dict[str, Any] = Field(default_factory=dict)
