from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import MetaData, Table, func, inspect, select
from types import SimpleNamespace
from sqlalchemy.orm import Session

from ..db.models import (
    ChangeKind,
    ChangeLog,
    ConsumptionEvent,
    CorrectionEvent,
    Product,
    PurchaseEvent,
    RelocationEvent,
)
from ..errors import DuplicateEntityError, StaleVersionError
from ..idempotency.operations import run_idempotent
from ..revision.change_log import ChangeSet
from .unit_service import require_unit, unit_reference


_UNSET = object()


def _to_dict(product: Product) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "imageUrl": product.image_url,
        "minStock": product.min_stock,
        "contentUnitLabel": product.content_unit_label,
        "brand": product.brand,
        "quantity": product.quantity,
        "unit": unit_reference(product.unit),
        "category": product.category,
        "variant": product.variant,
        "fieldProvenance": json.loads(product.field_provenance) if product.field_provenance else {},
        "version": product.version,
        "deletedAt": product.deleted_at.isoformat() if product.deleted_at else None,
    }


def get_product(db: Session, id: str) -> Product | None:
    return db.get(Product, id)


def create_product(
    db: Session, cs: ChangeSet, operation_id: str, id: str, name: str,
    image_url: Optional[str], min_stock: Optional[int], content_unit_label: Optional[str],
    brand: Optional[str] = None, quantity: Optional[float] = None, unit_id: Optional[str] = None,
    category: Optional[str] = None, variant: Optional[str] = None, field_provenance: Optional[dict] = None,
) -> dict:
    def perform() -> dict:
        unit = require_unit(db, unit_id)
        product = Product(
            id=id, name=name, image_url=image_url, min_stock=min_stock,
            content_unit_label=content_unit_label, brand=brand, quantity=quantity,
            unit=unit, category=category, variant=variant,
            field_provenance=json.dumps(field_provenance) if field_provenance else None,
            version=1,
        )
        db.add(product)
        db.flush()
        result = _to_dict(product)
        cs.record("Product", id, ChangeKind.CREATE, result)
        return result

    payload = {
        "op": "create_product", "id": id, "name": name, "imageUrl": image_url,
        "minStock": min_stock, "contentUnitLabel": content_unit_label,
        "brand": brand, "quantity": quantity, "unitId": unit_id,
        "category": category, "variant": variant,
    }
    return run_idempotent(db, operation_id, payload, perform)


def update_product(
    db: Session, cs: ChangeSet, operation_id: str, id: str, version: int,
    name: Optional[str] | object = _UNSET,
    image_url: Optional[str] | object = _UNSET, min_stock: Optional[int] | object = _UNSET,
    content_unit_label: Optional[str] | object = _UNSET,
    brand: Optional[str] | object = _UNSET, quantity: Optional[float] | object = _UNSET,
    unit_id: Optional[str] | object = _UNSET, category: Optional[str] | object = _UNSET,
    variant: Optional[str] | object = _UNSET, field_provenance: Optional[dict] | object = _UNSET,
) -> dict:
    fields = {
        "name": name, "image_url": image_url, "min_stock": min_stock,
        "content_unit_label": content_unit_label, "brand": brand,
        "quantity": quantity, "category": category, "variant": variant,
    }

    def perform() -> dict:
        product = db.get(Product, id)
        if product is None or product.deleted_at is not None:
            raise DuplicateEntityError("Product", "id", id)
        if product.version != version:
            raise StaleVersionError("Product", id, _to_dict(product))
        for field, value in fields.items():
            if value is not _UNSET:
                setattr(product, field, value)
        if unit_id is not _UNSET:
            product.unit = require_unit(db, unit_id)
        if field_provenance is not _UNSET:
            product.field_provenance = json.dumps(field_provenance) if field_provenance else None
        product.version += 1
        db.flush()
        result = _to_dict(product)
        cs.record("Product", id, ChangeKind.UPDATE, result)
        return result

    payload = {
        "op": "update_product", "id": id, "name": name, "imageUrl": image_url,
        "minStock": min_stock, "contentUnitLabel": content_unit_label, "version": version,
        "brand": brand, "quantity": quantity, "unitId": unit_id,
        "category": category, "variant": variant,
    }
    payload = {key: value for key, value in payload.items() if value is not _UNSET}
    return run_idempotent(db, operation_id, payload, perform)


def soft_delete_product(db: Session, cs: ChangeSet, operation_id: str, id: str, version: int) -> dict:
    def perform() -> dict:
        product = db.get(Product, id)
        if product is None:
            raise DuplicateEntityError("Product", "id", id)
        if product.version != version:
            raise StaleVersionError("Product", id, _to_dict(product))
        product.version += 1
        product.deleted_at = datetime.utcnow()
        db.flush()
        result = _to_dict(product)
        cs.record("Product", id, ChangeKind.UPDATE, result)
        return result

    return run_idempotent(db, operation_id, {"op": "delete_product", "id": id, "version": version}, perform)


def backfill_deleted_product_history(db: Session) -> int:
    """Append upserts for old product/event tombstones; caller owns the transaction.

    Batch is deliberately excluded: FIFO depletion has its own DELETE path.
    Barcode also has an independent delete operation. Neither can be repaired
    automatically under the product-history migration's conservative policy.
    Live upserts are untouched and a no-op does not allocate a revision.
    """
    from .inventory_service import (
        _purchase_event_to_dict,
        _consumption_event_to_dict,
        _correction_event_to_dict,
        _relocation_event_to_dict,
    )

    # 0005 invokes this repair before 0006 adds unit_id. Reflect that historical
    # products schema only on the migration path, avoiding future ORM columns.
    if "unit_id" not in {c["name"] for c in inspect(db.connection()).get_columns("products")}:
        table = Table("products", MetaData(), autoload_with=db.connection())
        products = [SimpleNamespace(**dict(row), unit=None) for row in
                    db.execute(select(table).where(table.c.deleted_at.is_not(None))).mappings()]
    else:
        products = db.scalars(select(Product).where(Product.deleted_at.is_not(None))).all()
    if not products:
        return 0
    latest_ids = (
        select(func.max(ChangeLog.id).label("max_id"))
        .group_by(ChangeLog.entity_type, ChangeLog.entity_id)
        .subquery()
    )
    latest = {
        (row.entity_type, row.entity_id): row.change_kind
        for row in db.scalars(select(ChangeLog).join(latest_ids, ChangeLog.id == latest_ids.c.max_id))
    }
    cs = ChangeSet(db)
    recorded = 0
    for product in products:
        if latest.get(("Product", product.id)) in (None, ChangeKind.DELETE.value):
            cs.record("Product", product.id, ChangeKind.UPDATE, _to_dict(product))
            recorded += 1

    product_ids = [product.id for product in products]
    for entity_type, model, serialize in (
        ("PurchaseEvent", PurchaseEvent, _purchase_event_to_dict),
        ("ConsumptionEvent", ConsumptionEvent, _consumption_event_to_dict),
        ("CorrectionEvent", CorrectionEvent, _correction_event_to_dict),
        ("RelocationEvent", RelocationEvent, _relocation_event_to_dict),
    ):
        for row in db.scalars(select(model).where(model.product_id.in_(product_ids))):
            if latest.get((entity_type, row.id)) == ChangeKind.DELETE.value:
                cs.record(entity_type, row.id, ChangeKind.UPDATE, serialize(row))
                recorded += 1
    db.flush()
    return recorded


def list_products(db: Session) -> list[dict]:
    rows = db.execute(select(Product).where(Product.deleted_at.is_(None))).scalars().all()
    return [_to_dict(r) for r in rows]
