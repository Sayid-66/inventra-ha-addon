from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Batch, ConsumptionEvent, CorrectionEvent, Location, Product, PurchaseEvent, RelocationEvent,
)


def _location_name(locations_by_id: dict, location_id: str) -> str:
    loc = locations_by_id.get(location_id)
    return loc.name if loc else ""


def build_summaries(db: Session) -> list[dict]:
    products = db.execute(select(Product).where(Product.deleted_at.is_(None))).scalars().all()
    batches = db.execute(select(Batch)).scalars().all()
    locations_by_id = {l.id: l for l in db.execute(select(Location)).scalars().all()}

    batches_by_product: dict[str, list[Batch]] = {}
    for b in batches:
        batches_by_product.setdefault(b.product_id, []).append(b)

    summaries = []
    for product in products:
        product_batches = batches_by_product.get(product.id, [])
        summaries.append(_build_summary(product, product_batches, locations_by_id))
    return summaries


def _build_summary(product: Product, batches: list[Batch], locations_by_id: dict) -> dict:
    stk_by_location: dict[str, int] = {}
    content_by_location: dict[str, int] = {}
    for b in batches:
        target = content_by_location if b.is_content_tracked else stk_by_location
        target[b.location_id] = target.get(b.location_id, 0) + b.remaining_quantity

    stk_list = [
        {"locationId": lid, "locationName": _location_name(locations_by_id, lid), "quantity": q}
        for lid, q in stk_by_location.items() if q > 0
    ]
    stk_list.sort(key=lambda e: e["locationName"].lower())
    content_list = [
        {"locationId": lid, "locationName": _location_name(locations_by_id, lid), "quantity": q}
        for lid, q in content_by_location.items() if q > 0
    ]
    content_list.sort(key=lambda e: e["locationName"].lower())

    next_mhd = min((b.mhd for b in batches if b.remaining_quantity > 0 and b.mhd), default=None)
    total_content = sum(e["quantity"] for e in content_list) if content_list else None

    return {
        "productId": product.id,
        "name": product.name,
        "imageUrl": product.image_url,
        "totalStk": sum(e["quantity"] for e in stk_list),
        "stkByLocation": stk_list,
        "totalContent": total_content,
        "contentUnitLabel": product.content_unit_label,
        "contentByLocation": content_list,
        "nextMhd": next_mhd,
        "minStock": product.min_stock,
    }


def list_current_stock(db: Session) -> list[dict]:
    summaries = [s for s in build_summaries(db) if s["totalStk"] > 0 or (s["totalContent"] or 0) > 0]
    summaries.sort(key=lambda s: (s["nextMhd"] is None, s["nextMhd"]))
    return summaries


def list_stock_history(db: Session) -> list[dict]:
    summaries = [s for s in build_summaries(db) if s["totalStk"] == 0 and (s["totalContent"] or 0) == 0]
    summaries.sort(key=lambda s: s["name"].lower())
    return summaries


def get_product_detail(db: Session, product_id: str) -> dict | None:
    product = db.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        return None

    batches = db.execute(select(Batch).where(Batch.product_id == product_id)).scalars().all()
    locations_by_id = {l.id: l for l in db.execute(select(Location)).scalars().all()}
    summary = _build_summary(product, batches, locations_by_id)

    location_mhd_breakdown: dict[str, dict] = {}
    for b in batches:
        if b.is_content_tracked or b.remaining_quantity <= 0:
            continue
        entry = location_mhd_breakdown.setdefault(
            b.location_id, {"locationId": b.location_id, "locationName": _location_name(locations_by_id, b.location_id), "quantity": 0, "mhds": set()},
        )
        entry["quantity"] += b.remaining_quantity
        if b.mhd:
            entry["mhds"].add(b.mhd)
    stk_by_location_detailed = [
        {**e, "mhds": sorted(e["mhds"])} for e in sorted(location_mhd_breakdown.values(), key=lambda e: e["locationName"].lower())
    ]

    purchases = db.execute(select(PurchaseEvent).where(PurchaseEvent.product_id == product_id)).scalars().all()
    corrections = db.execute(select(CorrectionEvent).where(CorrectionEvent.product_id == product_id)).scalars().all()
    consumptions = db.execute(select(ConsumptionEvent).where(ConsumptionEvent.product_id == product_id)).scalars().all()
    relocations = db.execute(select(RelocationEvent).where(RelocationEvent.product_id == product_id)).scalars().all()

    history = []
    for e in purchases:
        history.append({
            "type": "PURCHASE", "timestamp": e.timestamp, "quantity": e.quantity,
            "locationName": _location_name(locations_by_id, e.location_id),
            "pricePerUnitCents": e.price_per_unit_cents, "mhd": e.mhd,
            "contentUnitLabel": e.content_unit_label, "contentTotal": e.content_total,
        })
    for e in corrections:
        history.append({
            "type": "CORRECTION", "timestamp": e.timestamp,
            "locationName": _location_name(locations_by_id, e.location_id), "stockKind": e.stock_kind,
            "contentUnitLabel": e.content_unit_label, "oldQuantity": e.old_quantity, "newQuantity": e.new_quantity,
        })
    for e in consumptions:
        history.append({
            "type": "CONSUMPTION", "timestamp": e.timestamp,
            "locationName": _location_name(locations_by_id, e.location_id), "stockKind": e.stock_kind,
            "contentUnitLabel": e.content_unit_label, "quantity": e.quantity,
        })
    for e in relocations:
        history.append({
            "type": "RELOCATION", "timestamp": e.timestamp,
            "fromLocationName": _location_name(locations_by_id, e.from_location_id),
            "toLocationName": _location_name(locations_by_id, e.to_location_id),
            "stockKind": e.stock_kind, "contentUnitLabel": e.content_unit_label, "quantity": e.quantity,
        })
    history.sort(key=lambda h: h["timestamp"], reverse=True)

    return {
        **summary,
        "stkByLocationDetailed": stk_by_location_detailed,
        "purchases": sorted(
            [h for h in history if h["type"] == "PURCHASE"], key=lambda h: h["timestamp"]
        ),
        "history": history,
    }
