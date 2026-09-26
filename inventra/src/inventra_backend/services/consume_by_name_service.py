from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Device, ProcessedOperation, Product, Source
from ..errors import AmbiguousProductNameError, BusinessRuleViolation, ProductNameNotFoundError
from ..revision.change_log import ChangeSet
from .ids import new_id
from .inventory_service import consume


def _resolve_product(db: Session, product_name: str) -> Product:
    normalized = product_name.strip().casefold()
    active = db.execute(select(Product).where(Product.deleted_at.is_(None))).scalars().all()
    matches = [p for p in active if p.name.strip().casefold() == normalized]
    if len(matches) == 0:
        raise ProductNameNotFoundError(product_name)
    if len(matches) > 1:
        raise AmbiguousProductNameError(product_name, [{"id": p.id, "name": p.name} for p in matches])
    return matches[0]


def consume_by_name(
    db: Session, cs: ChangeSet, operation_id: str, device: Device,
    product_name: str, quantity: int, timestamp: int,
) -> dict:
    existing = db.get(ProcessedOperation, operation_id)
    if existing is not None:
        return json.loads(existing.result_snapshot)

    product = _resolve_product(db, product_name)
    if product.content_unit_label is not None:
        raise BusinessRuleViolation(
            "UNIT_NOT_SUPPORTED_VIA_VOICE",
            f"product {product.name!r} is tracked in {product.content_unit_label!r}; voice consumption only supports whole-piece products",
        )
    if device.default_location_id is None:
        raise BusinessRuleViolation(
            "NO_DEFAULT_LOCATION_CONFIGURED",
            f"device {device.device_id} has no default location configured for voice consumption",
        )
    return consume(
        db, cs, operation_id, new_id(), product.id, device.default_location_id,
        "STK", quantity, timestamp, device.user_id, device.device_id, Source.HOME_ASSISTANT.value,
    )
