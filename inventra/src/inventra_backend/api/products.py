from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as OrmSession

from ..auth.device_token import require_device
from ..db.base import get_db, get_engine
from ..db.models import Device
from ..resolver.provenance import derive_field_provenance
from ..resolver.resolution_store import get_resolution
from ..revision.change_log import change_set
from ..schemas.products import (
    ProductCreateRequest, ProductUpdateRequest, ProductDeleteRequest, ProductResponse,
)
from ..services import bring_service
from ..services.product_service import (
    create_product, get_product, update_product, soft_delete_product, list_products,
)

router = APIRouter(prefix="/products", tags=["products"])


def _resolve_for_provenance(resolution_id: str | None) -> dict | None:
    if resolution_id is None:
        return None
    with OrmSession(get_engine()) as db:
        return get_resolution(db, resolution_id)


@router.get("", response_model=list[ProductResponse])
def list_products_route(db: Session = Depends(get_db), device: Device = Depends(require_device)):
    return list_products(db)


@router.post("", response_model=ProductResponse, status_code=201)
def create_product_route(
    body: ProductCreateRequest, db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    resolution = _resolve_for_provenance(body.resolution_id)
    submitted = {
        "name": body.name,
        "brand": body.brand,
        "quantity": str(body.quantity) if body.quantity is not None else None,
        "imageUrl": body.image_url,
        "category": body.category,
        "variant": body.variant,
    }
    provenance = derive_field_provenance(resolution, submitted, {})
    with change_set(db) as cs:
        return create_product(
            db, cs, body.operation_id, body.id, body.name, body.image_url, body.min_stock,
            body.content_unit_label, body.brand, body.quantity, body.quantity_unit,
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
    resolution = _resolve_for_provenance(body.resolution_id)
    submitted = {
        "name": body.name,
        "brand": body.brand,
        "quantity": str(body.quantity) if body.quantity is not None else None,
        "imageUrl": body.image_url,
        "category": body.category,
        "variant": body.variant,
    }
    provenance = derive_field_provenance(resolution, submitted, previous_provenance)
    with change_set(db) as cs:
        result = update_product(
            db, cs, body.operation_id, product_id, body.name, body.image_url,
            body.min_stock, body.content_unit_label, body.version, body.brand,
            body.quantity, body.quantity_unit, body.category, body.variant, provenance,
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
