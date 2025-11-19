import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import (
    Delivery, DeliveryItem, Product, Variant, Location,
    DeliveryStatus, DeliveryItemStatus,
    DeliveryCreateRequest, DeliveryItemCreateRequest,
    ReceiveItemsRequest
)

app = FastAPI(title="Noven Pro - Lagerverwaltungssystem API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Helpers
class IdModel(BaseModel):
    id: str

def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")


def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    doc["id"] = str(doc.pop("_id"))
    # convert datetime to iso
    for k, v in list(doc.items()):
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


@app.get("/")
def read_root():
    return {"message": "Noven Pro Backend läuft"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "❌ Not Set",
        "database_name": "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:120]}"
    return response


# -------------------- Deliveries --------------------
@app.post("/deliveries")
def create_delivery(payload: DeliveryCreateRequest):
    # Business rule fixes: receivedQty = 0 at creation; status defaults to PENDING
    delivery = Delivery(
        supplier=payload.supplier,
        reference=payload.reference,
        expectedDate=payload.expectedDate,
        status="PENDING",
        receivedQty=0,
        meta=payload.meta or {}
    )
    new_id = create_document("delivery", delivery)
    doc = db["delivery"].find_one({"_id": ObjectId(new_id)})
    return serialize(doc)


@app.get("/deliveries")
def list_deliveries(
    status_in: Optional[List[DeliveryStatus]] = Query(default=None, alias="status.in"),
    limit: int = Query(default=100, ge=1, le=500)
):
    filt: Dict[str, Any] = {}
    if status_in:
        filt["status"] = {"$in": list(status_in)}
    # Ensure no legacy RECEIVED status appears (Problem 4); defensive filter
    filt["status"] = filt.get("status", {"$exists": True})
    if isinstance(filt["status"], dict):
        filt["status"]["$nin"] = list(set(filt["status"].get("$nin", [])) | {"RECEIVED"})
    docs = db["delivery"].find(filt).limit(limit).sort("created_at", -1)
    return [serialize(d) for d in docs]


@app.get("/deliveries/{delivery_id}")
def get_delivery(delivery_id: str):
    doc = db["delivery"].find_one({"_id": oid(delivery_id)})
    if not doc:
        raise HTTPException(404, "Delivery not found")
    # include items
    items = list(db["deliveryitem"].find({"delivery_id": delivery_id}))
    return {"delivery": serialize(doc), "items": [serialize(i) for i in items]}


@app.post("/deliveries/{delivery_id}/items")
def add_delivery_item(delivery_id: str, payload: DeliveryItemCreateRequest):
    # No auto status change from PENDING -> RECEIVED (Problem 2): we strictly keep delivery.status
    # receivedQty is always 0 on creation (Problem 1)
    # If product/variant are both None, allow as free item with notes
    # Validate delivery exists
    if not db["delivery"].find_one({"_id": oid(delivery_id)}):
        raise HTTPException(404, "Delivery not found")

    item = DeliveryItem(
        delivery_id=delivery_id,
        product_id=payload.product_id,
        variant_id=payload.variant_id,
        expectedQty=payload.expectedQty,
        receivedQty=0,
        status="PENDING",
        notes=payload.notes,
        location=None,
    )
    new_id = create_document("deliveryitem", item)
    created = db["deliveryitem"].find_one({"_id": ObjectId(new_id)})
    return serialize(created)


class ReceiveItemInput(BaseModel):
    itemId: str
    qty: int

class ReceivePayload(BaseModel):
    items: List[ReceiveItemInput]


@app.post("/deliveries/{delivery_id}/receive")
def receive_items(delivery_id: str, payload: ReceivePayload):
    # Receive quantities for items; do NOT auto change delivery.status (Problem 2)
    # Update item.receivedQty += qty, set item.status = RECEIVED when any qty received
    if not db["delivery"].find_one({"_id": oid(delivery_id)}):
        raise HTTPException(404, "Delivery not found")

    total_added = 0
    for it in payload.items:
        item = db["deliveryitem"].find_one({"_id": oid(it.itemId), "delivery_id": delivery_id})
        if not item:
            raise HTTPException(400, f"Item {it.itemId} not found for this delivery")
        if it.qty < 0:
            raise HTTPException(400, "qty must be >= 0")
        new_received = int(item.get("receivedQty", 0)) + int(it.qty)
        status = item.get("status", "PENDING")
        if it.qty > 0:
            status = "RECEIVED"
        db["deliveryitem"].update_one(
            {"_id": item["_id"]},
            {"$set": {"receivedQty": new_received, "status": status, "updated_at": datetime.now(timezone.utc)}}
        )
        total_added += int(it.qty)

    # Update delivery.receivedQty sum of all items' receivedQty
    agg = db["deliveryitem"].aggregate([
        {"$match": {"delivery_id": delivery_id}},
        {"$group": {"_id": None, "sum": {"$sum": "$receivedQty"}}}
    ])
    new_total = 0
    for r in agg:
        new_total = r.get("sum", 0)
    db["delivery"].update_one(
        {"_id": oid(delivery_id)},
        {"$set": {"receivedQty": int(new_total), "updated_at": datetime.now(timezone.utc)}}
    )

    delivery = db["delivery"].find_one({"_id": oid(delivery_id)})
    return serialize(delivery)


@app.post("/deliveries/{delivery_id}/send-to-quality")
def send_to_quality(delivery_id: str):
    # Set status directly to IN_QUALITY_CHECK (Problem 3)
    updated = db["delivery"].update_one(
        {"_id": oid(delivery_id)},
        {"$set": {"status": "IN_QUALITY_CHECK", "updated_at": datetime.now(timezone.utc)}}
    )
    if updated.matched_count == 0:
        raise HTTPException(404, "Delivery not found")
    doc = db["delivery"].find_one({"_id": oid(delivery_id)})
    return serialize(doc)


@app.post("/deliveries/{delivery_id}/complete")
def complete_delivery(delivery_id: str):
    updated = db["delivery"].update_one(
        {"_id": oid(delivery_id)},
        {"$set": {"status": "COMPLETED", "updated_at": datetime.now(timezone.utc)}}
    )
    if updated.matched_count == 0:
        raise HTTPException(404, "Delivery not found")
    doc = db["delivery"].find_one({"_id": oid(delivery_id)})
    return serialize(doc)


# -------------------- Delivery Items --------------------
@app.get("/delivery-items")
def list_delivery_items(
    delivery_id: Optional[str] = None,
    status_in: Optional[List[DeliveryItemStatus]] = Query(default=None, alias="status.in"),
    limit: int = Query(default=200, ge=1, le=1000)
):
    filt: Dict[str, Any] = {}
    if delivery_id:
        filt["delivery_id"] = delivery_id
    if status_in:
        filt["status"] = {"$in": list(status_in)}
    docs = db["deliveryitem"].find(filt).limit(limit).sort("created_at", -1)
    return [serialize(d) for d in docs]


class ApprovePayload(BaseModel):
    notes: Optional[str] = None


@app.post("/delivery-items/{item_id}/approve")
def approve_item(item_id: str, payload: ApprovePayload):
    updated = db["deliveryitem"].update_one(
        {"_id": oid(item_id)},
        {"$set": {"status": "APPROVED", "notes": payload.notes, "updated_at": datetime.now(timezone.utc)}}
    )
    if updated.matched_count == 0:
        raise HTTPException(404, "Item not found")
    doc = db["deliveryitem"].find_one({"_id": oid(item_id)})
    return serialize(doc)


class StorePayload(BaseModel):
    rack: str
    slot: str
    zone: Optional[str] = None
    level: Optional[str] = None


@app.post("/delivery-items/{item_id}/store")
def store_item(item_id: str, payload: StorePayload):
    location = {
        "rack": payload.rack,
        "slot": payload.slot,
        "zone": payload.zone,
        "level": payload.level,
    }
    updated = db["deliveryitem"].update_one(
        {"_id": oid(item_id)},
        {"$set": {"status": "STORED", "location": location, "updated_at": datetime.now(timezone.utc)}}
    )
    if updated.matched_count == 0:
        raise HTTPException(404, "Item not found")
    doc = db["deliveryitem"].find_one({"_id": oid(item_id)})
    return serialize(doc)


# -------------------- Products / Variants (basic) --------------------
@app.post("/products")
def create_product(product: Product):
    new_id = create_document("product", product)
    doc = db["product"].find_one({"_id": ObjectId(new_id)})
    return serialize(doc)


@app.get("/products")
def list_products(q: Optional[str] = None, limit: int = 100):
    filt: Dict[str, Any] = {}
    if q:
        filt["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"sku": {"$regex": q, "$options": "i"}},
        ]
    docs = db["product"].find(filt).limit(limit).sort("created_at", -1)
    return [serialize(d) for d in docs]


@app.post("/variants")
def create_variant(variant: Variant):
    # Ensure referenced product exists
    if not db["product"].find_one({"_id": oid(variant.product_id)}):
        raise HTTPException(400, "Referenced product does not exist")
    new_id = create_document("variant", variant)
    doc = db["variant"].find_one({"_id": ObjectId(new_id)})
    return serialize(doc)


@app.get("/variants")
def list_variants(product_id: Optional[str] = None, limit: int = 200):
    filt: Dict[str, Any] = {}
    if product_id:
        filt["product_id"] = product_id
    docs = db["variant"].find(filt).limit(limit).sort("created_at", -1)
    return [serialize(d) for d in docs]


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
