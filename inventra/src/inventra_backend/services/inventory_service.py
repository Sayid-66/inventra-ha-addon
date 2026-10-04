from __future__ import annotations

import json as _json
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .ids import new_id
from .location_kind import is_freezer_location_name
from ..db.models import Batch, ChangeKind, ConsumptionEvent, CorrectionEvent, Location, Store, Product, PurchaseEvent, RelocationEvent
from ..errors import BusinessRuleViolation
from ..idempotency.operations import run_idempotent
from ..resolver.provenance import derive_field_provenance
from ..resolver.resolution_store import get_resolution
from ..revision.change_log import ChangeSet


def _require_active_product(db: Session, product_id: str) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise BusinessRuleViolation(
            "PRODUCT_NOT_FOUND", f"product {product_id} does not exist and no newProduct was given"
        )
    return product


def _require_active_reference(db: Session, model, entity_id: str, code: str) -> None:
    row = db.get(model, entity_id)
    if row is None or row.deleted_at is not None:
        raise BusinessRuleViolation(code, "Referenced location or store does not exist or is deleted")


def _batch_to_dict(batch: Batch) -> dict:
    return {
        "id": batch.id,
        "productId": batch.product_id,
        "purchaseEventId": batch.purchase_event_id,
        "correctionEventId": batch.correction_event_id,
        "locationId": batch.location_id,
        "mhd": batch.mhd,
        "eventTimestamp": batch.event_timestamp,
        "storedAt": batch.stored_at,
        "isContentTracked": batch.is_content_tracked,
        "contentUnitLabel": batch.content_unit_label,
        "remainingQuantity": batch.remaining_quantity,
    }


def _purchase_event_to_dict(event: PurchaseEvent) -> dict:
    return {
        "eventId": event.id, "productId": event.product_id, "timestamp": event.timestamp,
        "barcode": event.barcode, "locationId": event.location_id, "quantity": event.quantity,
        "storeId": event.store_id, "pricePerUnitCents": event.price_per_unit_cents, "mhd": event.mhd,
        "contentUnitLabel": event.content_unit_label, "contentTotal": event.content_total,
        "contentBreakdown": event.content_breakdown, "userId": event.user_id,
        "sourceDeviceId": event.source_device_id, "source": event.source,
    }


def _ordered_batches(db: Session, product_id: str, location_id: str, is_content_tracked: bool) -> list[Batch]:
    stmt = (
        select(Batch)
        .where(
            Batch.product_id == product_id,
            Batch.location_id == location_id,
            Batch.is_content_tracked == is_content_tracked,
        )
        .order_by(case((Batch.mhd.is_(None), 1), else_=0), Batch.mhd, Batch.event_timestamp)
    )
    return list(db.execute(stmt).scalars().all())


def _current_quantity(db: Session, product_id: str, location_id: str, is_content_tracked: bool) -> int:
    total = db.execute(
        select(func.coalesce(func.sum(Batch.remaining_quantity), 0)).where(
            Batch.product_id == product_id,
            Batch.location_id == location_id,
            Batch.is_content_tracked == is_content_tracked,
        )
    ).scalar_one()
    return int(total)


class InsufficientStockError(BusinessRuleViolation):
    def __init__(self, product_id: str, location_id: str):
        super().__init__("INSUFFICIENT_STOCK", f"insufficient stock for product {product_id} at location {location_id}")


def _deplete_fifo(
    db: Session, cs: ChangeSet, product_id: str, location_id: str, is_content_tracked: bool, amount: int,
) -> list[tuple[dict, int]]:
    """Depletes `amount` across ordered batches (earliest MHD first, ties
    -> older event first, no-MHD last). Returns each batch's pre-mutation
    snapshot with the amount taken from it, in depletion order. Ported
    from InventoryDao.depleteFifo — raises, mutating nothing, if the
    pool's total is less than `amount`."""
    if amount <= 0:
        raise BusinessRuleViolation("INVALID_QUANTITY", "quantity must be positive")
    ordered = _ordered_batches(db, product_id, location_id, is_content_tracked)
    if sum(b.remaining_quantity for b in ordered) < amount:
        raise InsufficientStockError(product_id, location_id)

    remaining = amount
    taken: list[tuple[dict, int]] = []
    for batch in ordered:
        if remaining == 0:
            break
        take = min(batch.remaining_quantity, remaining)
        snapshot = {
            "purchase_event_id": batch.purchase_event_id,
            "correction_event_id": batch.correction_event_id,
            "mhd": batch.mhd,
            "event_timestamp": batch.event_timestamp,
            "stored_at": batch.stored_at,
            "content_unit_label": batch.content_unit_label,
        }
        taken.append((snapshot, take))
        new_quantity = batch.remaining_quantity - take
        if new_quantity == 0:
            batch_id = batch.id
            db.delete(batch)
            db.flush()
            cs.record("Batch", batch_id, ChangeKind.DELETE, {"id": batch_id, "remainingQuantity": 0})
        else:
            batch.remaining_quantity = new_quantity
            db.flush()
            cs.record("Batch", batch.id, ChangeKind.UPDATE, _batch_to_dict(batch))
        remaining -= take
    return taken


def commit_purchase(
    db: Session, cs: ChangeSet, operation_id: str, event_id: str, product_id: str,
    new_product: Optional[dict], barcode: str, location_id: str, quantity: int,
    store_id: Optional[str], price_per_unit_cents: Optional[int], mhd: Optional[str],
    min_stock: Optional[int], content_unit_label: Optional[str], content_total: Optional[int],
    content_breakdown: Optional[str], timestamp: int, user_id: str, device_id: Optional[str], source: str,
    product_update: Optional[dict] = None,
) -> dict:
    """Atomically book stock and optionally apply provided product fields.

    new_product ignores product_update and keeps creation/initial minStock behavior.
    Without product_update, legacy purchases overwrite minStock from the request.
    With product_update, top-level minStock is ignored; provided fields use
    last-writer-wins without a version precondition. Absent keys are unchanged,
    explicit null clears nullable fields. All product changes share one bump/log.
    The caller owns the transaction, including rollback on any validation error.
    """
    def perform() -> dict:
        _require_active_reference(db, Location, location_id, "LOCATION_NOT_FOUND")
        if store_id is not None:
            _require_active_reference(db, Store, store_id, "STORE_NOT_FOUND")
        if new_product is None:
            product = _require_active_product(db, product_id)
        else:
            product = db.get(Product, product_id)
            if product is not None and product.deleted_at is not None:
                product = _require_active_product(db, product_id)
        if product is None:
            resolution_id = new_product.get("resolutionId")
            resolution = get_resolution(db, resolution_id) if resolution_id else None
            submitted = {
                "name": new_product["name"], "imageUrl": new_product.get("imageUrl"),
                "brand": new_product.get("brand"), "variant": new_product.get("variant"),
                "category": new_product.get("category"),
            }
            provenance = derive_field_provenance(resolution, submitted, {})
            product = Product(
                id=product_id, name=new_product["name"], image_url=new_product.get("imageUrl"),
                brand=new_product.get("brand"), variant=new_product.get("variant"),
                category=new_product.get("category"),
                field_provenance=_json.dumps(provenance) if provenance else None, version=1,
            )
            db.add(product)
            db.flush()
            from .product_service import _to_dict
            cs.record("Product", product_id, ChangeKind.CREATE, _to_dict(product))

        from ..db.models import Barcode
        barcode_row = db.get(Barcode, barcode)
        if barcode_row is None:
            new_barcode = Barcode(code=barcode, product_id=product.id, version=1)
            db.add(new_barcode)
            db.flush()
            cs.record("Barcode", barcode, ChangeKind.CREATE, {
                "code": barcode, "productId": product.id, "version": 1, "deletedAt": None,
            })
        else:
            owner = db.get(Product, barcode_row.product_id)
            owner_deleted = owner is not None and owner.deleted_at is not None
            if new_product is not None and barcode_row.deleted_at is None and not owner_deleted and barcode_row.product_id != product.id:
                raise BusinessRuleViolation("BARCODE_ALREADY_ASSIGNED", f"barcode {barcode} belongs to another product")
            if barcode_row.deleted_at is not None or owner_deleted:
                barcode_row.product_id = product.id
                barcode_row.deleted_at = None
                barcode_row.version += 1
                db.flush()
                cs.record("Barcode", barcode, ChangeKind.UPDATE, {
                    "code": barcode, "productId": product.id,
                    "version": barcode_row.version, "deletedAt": None,
                })

        is_content_tracked = content_unit_label is not None
        from .product_service import apply_product_fields
        product_changed = False
        if new_product is None and product_update is not None:
            submitted = {
                field: value if value is not None else ""
                for field, value in product_update.items()
                if field in ("name", "brand", "variant", "category")
                and getattr(product, field) != value
            }
            product_changed = apply_product_fields(db, product, product_update)
            if submitted:
                previous = _json.loads(product.field_provenance) if product.field_provenance else {}
                product.field_provenance = _json.dumps(derive_field_provenance(None, submitted, previous))
        elif product.min_stock != min_stock:
            product.min_stock = min_stock
            product_changed = True
        if is_content_tracked and product.content_unit_label is None:
            product.content_unit_label = content_unit_label
            product_changed = True
        if product_changed:
            product.version += 1
            db.flush()
            from .product_service import _to_dict
            cs.record("Product", product.id, ChangeKind.UPDATE, _to_dict(product))

        event = PurchaseEvent(
            id=event_id, product_id=product.id, timestamp=timestamp, barcode=barcode,
            location_id=location_id, quantity=quantity, store_id=store_id,
            price_per_unit_cents=price_per_unit_cents, mhd=mhd, content_unit_label=content_unit_label,
            content_total=content_total, content_breakdown=content_breakdown,
            user_id=user_id, source_device_id=device_id, source=source,
        )
        db.add(event)
        db.flush()
        cs.record("PurchaseEvent", event_id, ChangeKind.EVENT, _purchase_event_to_dict(event))

        batch_id = new_id()
        batch = Batch(
            id=batch_id, product_id=product.id, purchase_event_id=event_id, correction_event_id=None,
            location_id=location_id, mhd=mhd, event_timestamp=timestamp, stored_at=timestamp,
            is_content_tracked=is_content_tracked, content_unit_label=content_unit_label,
            remaining_quantity=content_total if is_content_tracked else quantity,
        )
        db.add(batch)
        db.flush()
        cs.record("Batch", batch_id, ChangeKind.CREATE, _batch_to_dict(batch))

        return _purchase_event_to_dict(event)

    payload = {
        "op": "commit_purchase", "id": event_id, "productId": product_id, "newProduct": new_product,
        "barcode": barcode, "locationId": location_id, "quantity": quantity, "storeId": store_id,
        "pricePerUnitCents": price_per_unit_cents, "mhd": mhd, "minStock": min_stock,
        "contentUnitLabel": content_unit_label, "contentTotal": content_total,
        "contentBreakdown": content_breakdown, "timestamp": timestamp,
    }
    # Preserve hashes of legacy operations queued before productUpdate existed.
    if product_update is not None:
        payload["productUpdate"] = product_update
    return run_idempotent(db, operation_id, payload, perform)


def _consumption_event_to_dict(event: ConsumptionEvent) -> dict:
    return {
        "eventId": event.id, "productId": event.product_id, "timestamp": event.timestamp,
        "locationId": event.location_id, "stockKind": event.stock_kind,
        "contentUnitLabel": event.content_unit_label, "quantity": event.quantity,
        "userId": event.user_id, "sourceDeviceId": event.source_device_id, "source": event.source,
    }


def consume(
    db: Session, cs: ChangeSet, operation_id: str, event_id: str, product_id: str, location_id: str,
    stock_kind: str, quantity: int, timestamp: int, user_id: str, device_id: Optional[str], source: str,
) -> dict:
    def perform() -> dict:
        product = _require_active_product(db, product_id)
        _require_active_reference(db, Location, location_id, "LOCATION_NOT_FOUND")
        is_content_tracked = stock_kind == "CONTENT"
        _deplete_fifo(db, cs, product_id, location_id, is_content_tracked, quantity)
        content_unit_label = product.content_unit_label if is_content_tracked else None
        event = ConsumptionEvent(
            id=event_id, product_id=product_id, timestamp=timestamp, location_id=location_id,
            stock_kind=stock_kind, content_unit_label=content_unit_label, quantity=quantity,
            user_id=user_id, source_device_id=device_id, source=source,
        )
        db.add(event)
        db.flush()
        result = _consumption_event_to_dict(event)
        cs.record("ConsumptionEvent", event_id, ChangeKind.EVENT, result)
        return result

    payload = {
        "op": "consume", "id": event_id, "productId": product_id, "locationId": location_id,
        "stockKind": stock_kind, "quantity": quantity, "timestamp": timestamp,
    }
    return run_idempotent(db, operation_id, payload, perform)


def _correction_event_to_dict(event: CorrectionEvent) -> dict:
    return {
        "eventId": event.id, "productId": event.product_id, "timestamp": event.timestamp,
        "locationId": event.location_id, "stockKind": event.stock_kind,
        "contentUnitLabel": event.content_unit_label, "oldQuantity": event.old_quantity,
        "newQuantity": event.new_quantity, "mhdForIncrease": event.mhd_for_increase,
        "userId": event.user_id, "sourceDeviceId": event.source_device_id, "source": event.source,
    }


def correct_stock(
    db: Session, cs: ChangeSet, operation_id: str, event_id: str, product_id: str, location_id: str,
    stock_kind: str, content_unit_label: Optional[str], new_quantity: int, mhd_for_increase: Optional[str],
    timestamp: int, user_id: str, device_id: Optional[str], source: str,
) -> dict:
    def perform() -> dict:
        _require_active_product(db, product_id)
        _require_active_reference(db, Location, location_id, "LOCATION_NOT_FOUND")
        if new_quantity < 0:
            raise BusinessRuleViolation("INVALID_QUANTITY", "newQuantity must be non-negative")
        is_content_tracked = stock_kind == "CONTENT"
        current = _current_quantity(db, product_id, location_id, is_content_tracked)
        if new_quantity == current:
            raise BusinessRuleViolation("NO_OP_CORRECTION", "newQuantity must differ from the current quantity")

        event = CorrectionEvent(
            id=event_id, product_id=product_id, timestamp=timestamp, location_id=location_id,
            stock_kind=stock_kind, content_unit_label=content_unit_label, old_quantity=current,
            new_quantity=new_quantity, mhd_for_increase=mhd_for_increase,
            user_id=user_id, source_device_id=device_id, source=source,
        )
        db.add(event)
        db.flush()
        cs.record("CorrectionEvent", event_id, ChangeKind.EVENT, _correction_event_to_dict(event))

        if new_quantity < current:
            _deplete_fifo(db, cs, product_id, location_id, is_content_tracked, current - new_quantity)
        else:
            batch_id = new_id()
            batch = Batch(
                id=batch_id, product_id=product_id, purchase_event_id=None, correction_event_id=event_id,
                location_id=location_id, mhd=mhd_for_increase, event_timestamp=timestamp, stored_at=timestamp,
                is_content_tracked=is_content_tracked, content_unit_label=content_unit_label,
                remaining_quantity=new_quantity - current,
            )
            db.add(batch)
            db.flush()
            cs.record("Batch", batch_id, ChangeKind.CREATE, _batch_to_dict(batch))

        return _correction_event_to_dict(event)

    payload = {
        "op": "correct_stock", "id": event_id, "productId": product_id, "locationId": location_id,
        "stockKind": stock_kind, "contentUnitLabel": content_unit_label, "newQuantity": new_quantity,
        "mhdForIncrease": mhd_for_increase, "timestamp": timestamp,
    }
    return run_idempotent(db, operation_id, payload, perform)


def _relocation_event_to_dict(event: RelocationEvent) -> dict:
    return {
        "eventId": event.id, "productId": event.product_id, "timestamp": event.timestamp,
        "fromLocationId": event.from_location_id, "toLocationId": event.to_location_id,
        "stockKind": event.stock_kind, "contentUnitLabel": event.content_unit_label,
        "quantity": event.quantity, "userId": event.user_id,
        "sourceDeviceId": event.source_device_id, "source": event.source,
    }


def relocate(
    db: Session, cs: ChangeSet, operation_id: str, event_id: str, product_id: str,
    from_location_id: str, to_location_id: str, stock_kind: str, quantity: int,
    timestamp: int, user_id: str, device_id: Optional[str], source: str,
) -> dict:
    def perform() -> dict:
        product = _require_active_product(db, product_id)
        _require_active_reference(db, Location, from_location_id, "LOCATION_NOT_FOUND")
        _require_active_reference(db, Location, to_location_id, "LOCATION_NOT_FOUND")
        if from_location_id == to_location_id:
            raise BusinessRuleViolation("SAME_LOCATION_RELOCATION", "fromLocationId and toLocationId must differ")

        is_content_tracked = stock_kind == "CONTENT"
        taken = _deplete_fifo(db, cs, product_id, from_location_id, is_content_tracked, quantity)

        destination_is_freezer = is_freezer_location_name(db.get(Location, to_location_id).name)
        keep_freezing_date = (
            is_freezer_location_name(db.get(Location, from_location_id).name)
            and destination_is_freezer
        )
        for snapshot, moved_amount in taken:
            new_stored_at = (
                snapshot["stored_at"]
                if keep_freezing_date
                else timestamp
            )
            existing = db.execute(
                select(Batch).where(
                    Batch.location_id == to_location_id,
                    Batch.purchase_event_id == snapshot["purchase_event_id"] if snapshot["purchase_event_id"] else Batch.purchase_event_id.is_(None),
                    Batch.correction_event_id == snapshot["correction_event_id"] if snapshot["correction_event_id"] else Batch.correction_event_id.is_(None),
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.remaining_quantity += moved_amount
                if (destination_is_freezer and existing.stored_at is None) or (keep_freezing_date and new_stored_at is None):
                    existing.stored_at = None
                else:
                    stored_dates = [value for value in (existing.stored_at, new_stored_at) if value is not None]
                    existing.stored_at = min(stored_dates) if stored_dates else None
                db.flush()
                cs.record("Batch", existing.id, ChangeKind.UPDATE, _batch_to_dict(existing))
            else:
                new_batch_id = new_id()
                new_batch = Batch(
                    id=new_batch_id, product_id=product_id,
                    purchase_event_id=snapshot["purchase_event_id"],
                    correction_event_id=snapshot["correction_event_id"],
                    location_id=to_location_id, mhd=snapshot["mhd"],
                    event_timestamp=snapshot["event_timestamp"], stored_at=new_stored_at,
                    is_content_tracked=is_content_tracked,
                    content_unit_label=snapshot["content_unit_label"], remaining_quantity=moved_amount,
                )
                db.add(new_batch)
                db.flush()
                cs.record("Batch", new_batch_id, ChangeKind.CREATE, _batch_to_dict(new_batch))

        content_unit_label = product.content_unit_label if is_content_tracked else None
        event = RelocationEvent(
            id=event_id, product_id=product_id, timestamp=timestamp, from_location_id=from_location_id,
            to_location_id=to_location_id, stock_kind=stock_kind, content_unit_label=content_unit_label,
            quantity=quantity, user_id=user_id, source_device_id=device_id, source=source,
        )
        db.add(event)
        db.flush()
        result = _relocation_event_to_dict(event)
        cs.record("RelocationEvent", event_id, ChangeKind.EVENT, result)
        return result

    payload = {
        "op": "relocate", "id": event_id, "productId": product_id, "fromLocationId": from_location_id,
        "toLocationId": to_location_id, "stockKind": stock_kind, "quantity": quantity, "timestamp": timestamp,
    }
    return run_idempotent(db, operation_id, payload, perform)
