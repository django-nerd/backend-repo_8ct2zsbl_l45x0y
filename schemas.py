"""
Database Schemas for Noven Pro - Lagerverwaltungssystem

Each Pydantic model name maps to a MongoDB collection using the lowercase
of the class name. Example: Delivery -> "delivery"
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime

# Status definitions (business rules)
DeliveryStatus = Literal[
    "DRAFT", "PENDING", "IN_QUALITY_CHECK", "COMPLETED"
]

DeliveryItemStatus = Literal[
    "PENDING", "RECEIVED", "QUALITY_CHECK", "APPROVED", "STORED"
]


class Location(BaseModel):
    rack: str = Field(..., description="Regal")
    slot: str = Field(..., description="Slot/Fach")
    zone: Optional[str] = Field(None, description="Zone (optional)")
    level: Optional[str] = Field(None, description="Ebene (optional)")


class Product(BaseModel):
    sku: str = Field(..., description="SKU")
    name: str = Field(..., description="Produktname")
    description: Optional[str] = Field(None)


class Variant(BaseModel):
    product_id: str = Field(..., description="Referenz auf product._id (String)")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="z.B. Farbe, Größe")
    sku: Optional[str] = Field(None, description="Variante-SKU (falls abweichend)")


class DeliveryItem(BaseModel):
    delivery_id: str = Field(..., description="Referenz zur Lieferung")
    product_id: Optional[str] = Field(None)
    variant_id: Optional[str] = Field(None)
    expectedQty: int = Field(..., ge=0)
    receivedQty: int = Field(0, ge=0, description="Beim Erfassen immer 0")
    status: DeliveryItemStatus = Field("PENDING")
    notes: Optional[str] = None
    location: Optional[Location] = None


class Delivery(BaseModel):
    supplier: Optional[str] = Field(None)
    reference: Optional[str] = Field(None, description="Bestell-/Lieferschein-Nr.")
    status: DeliveryStatus = Field("PENDING")
    expectedDate: Optional[datetime] = None
    receivedQty: int = Field(0, ge=0, description="Gesamt empfangen; beim Anlegen 0")
    meta: Dict[str, Any] = Field(default_factory=dict)


# Response helper models (for typed responses if needed)
class DeliveryCreateRequest(BaseModel):
    supplier: Optional[str] = None
    reference: Optional[str] = None
    expectedDate: Optional[datetime] = None
    meta: Dict[str, Any] = Field(default_factory=dict)

class DeliveryItemCreateRequest(BaseModel):
    product_id: Optional[str] = None
    variant_id: Optional[str] = None
    expectedQty: int
    notes: Optional[str] = None

class ReceiveItemsRequest(BaseModel):
    items: List[Dict[str, int]] = Field(..., description="Liste von {itemId, qty}")

class SendToQualityRequest(BaseModel):
    pass
