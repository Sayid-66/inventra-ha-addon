from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..resolver.provenance import derive_field_provenance
from ..resolver.resolution_store import get_resolution
from ..revision.change_log import change_set
from ..schemas.products import (
    ProductCreateRequest, ProductUpdateRequest, ProductDeleteRequest, ProductResponse,
)
from ..services import bring_service
from ..services.unit_normalizer import normalize_quantity, STANDARD_UNIT_IDS
from ..services.unit_service import require_unit
from ..services.product_service import (
    create_product, get_product, update_product, soft_delete_product, list_products,
)

router = APIRouter(prefix="/products", tags=["products"])


def _package_size(body, db: Session, resolution: dict | None) -> tuple[float | None, str | None, str | None]:
    if body.unit_id is not None:
        require_unit(db, body.unit_id)
        return body.quantity, body.unit_id, str(body.quantity) if body.quantity is not None else None
    raw = body.quantity_text
    # An explicitly cleared amount/unit must not be restored from a resolution.
    if raw is None and resolution and not ({"quantity", "unit_id", "product_quantity", "product_quantity_unit"} & body.model_fields_set):
        raw = resolution.get("proposed_fields", {}).get("quantity", {}).get("value")
    amount, abbreviation = normalize_quantity(raw, body.product_quantity, body.product_quantity_unit)
    if body.quantity is not None:
        amount = body.quantity
    # Stable seeded UUIDs let normalization keep working after both catalog
    # labels are renamed; no stale label is copied onto a product.
    unit_id = STANDARD_UNIT_IDS.get(abbreviation)
    if unit_id is not None:
        require_unit(db, unit_id)
    submitted = raw
    if body.product_quantity is not None and amount is not None and abbreviation:
        submitted = f"{amount:g} {abbreviation}"
    if body.quantity is not None:
        submitted = str(body.quantity)
    return amount, unit_id, submitted


def _resolve_for_provenance(db: Session, resolution_id: str | None) -> dict | None:
    if resolution_id is None:
        return None
    return get_resolution(db, resolution_id)


@router.get("", response_model=list[ProductResponse])
def list_products_route(db: Session = Depends(get_db), device: Device = Depends(require_device)):
    return list_products(db)


@router.post("", response_model=ProductResponse, status_code=201)
def create_product_route(
    body: ProductCreateRequest, db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    resolution = _resolve_for_provenance(db, body.resolution_id)
    quantity, unit_id, submitted_quantity = _package_size(body, db, resolution)
    submitted = {
        "name": body.name,
        "brand": body.brand,
        "quantity": submitted_quantity,
        "imageUrl": body.image_url,
        "category": body.category,
        "variant": body.variant,
    }
    provenance = derive_field_provenance(resolution, submitted, {})
    with change_set(db) as cs:
        return create_product(
            db, cs, body.operation_id, body.id, body.name, body.image_url, body.min_stock,
            body.content_unit_label, body.brand, quantity, unit_id,
            body.category, body.variant, provenance,
        )


@router.patch("/{product_id}", response_model=ProductResponse)
def update_product_route(
    product_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")], body: ProductUpdateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    product = get_product(db, product_id)
    if product is None:
        previous_provenance = {}
    else:
        previous_provenance = json.loads(product.field_provenance) if product.field_provenance else {}
    resolution = _resolve_for_provenance(db, body.resolution_id)
    quantity, unit_id, submitted_quantity = _package_size(body, db, resolution)
    present = body.model_fields_set
    updates = {
        field: getattr(body, field)
        for field in ("name", "image_url", "min_stock", "content_unit_label", "brand", "category", "variant")
        if field in present
    }
    submitted = {
        key: getattr(body, field)
        for field, key in (
            ("name", "name"), ("brand", "brand"), ("image_url", "imageUrl"),
            ("category", "category"), ("variant", "variant"),
        )
        if field in present
    }
    # Normalization remains shared with CREATE, but PATCH only applies a
    # package size when at least one of its input fields was actually sent.
    if {"quantity", "unit_id", "quantity_text", "product_quantity", "product_quantity_unit"} & present:
        updates.update(quantity=quantity, unit_id=unit_id)
        submitted["quantity"] = submitted_quantity
    provenance = derive_field_provenance(resolution, submitted, previous_provenance)
    with change_set(db) as cs:
        result = update_product(
            db, cs, body.operation_id, product_id, version=body.version,
            field_provenance=provenance, **updates,
        )
    background_tasks.add_task(bring_service.schedule_stock_change, product_id, False)
    return result


@router.delete("/{product_id}", response_model=ProductResponse)
def delete_product_route(
    product_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")], body: ProductDeleteRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        result = soft_delete_product(db, cs, body.operation_id, product_id, body.version)
    # Defer the nested session until the request commits, avoiding a self-deadlock on BEGIN IMMEDIATE.
    background_tasks.add_task(bring_service.on_product_deleted_sync, product_id)
    return result
