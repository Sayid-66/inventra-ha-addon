from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device, Source
from ..revision.change_log import change_set
from ..schemas.events import ConsumptionCreateRequest, CorrectionCreateRequest, PurchaseCreateRequest, RelocationCreateRequest
from ..services import bring_service
from ..services.inventory_service import commit_purchase, consume, correct_stock, relocate

router = APIRouter(prefix="", tags=["events"])


@router.post("/purchases", status_code=201)
def create_purchase_route(
    body: PurchaseCreateRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        result = commit_purchase(
            db, cs, body.operation_id, body.id, body.product_id,
            body.new_product.model_dump(by_alias=True) if body.new_product else None,
            body.barcode, body.location_id, body.quantity, body.store_id,
            body.price_per_unit_cents, body.mhd, body.min_stock, body.content_unit_label,
            body.content_total, body.content_breakdown, body.timestamp,
            device.user_id, device.device_id, Source.ANDROID.value,
        )
    background_tasks.add_task(bring_service.schedule_stock_change, body.product_id, True)
    return result


@router.post("/consumptions", status_code=201)
def create_consumption_route(
    body: ConsumptionCreateRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        result = consume(
            db, cs, body.operation_id, body.id, body.product_id, body.location_id,
            body.stock_kind, body.quantity, body.timestamp, device.user_id, device.device_id, Source.ANDROID.value,
        )
    background_tasks.add_task(bring_service.schedule_stock_change, body.product_id, False)
    return result


@router.post("/corrections", status_code=201)
def create_correction_route(
    body: CorrectionCreateRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        result = correct_stock(
            db, cs, body.operation_id, body.id, body.product_id, body.location_id, body.stock_kind,
            body.content_unit_label, body.new_quantity, body.mhd_for_increase, body.timestamp,
            device.user_id, device.device_id, Source.ANDROID.value,
        )
    increased = result["newQuantity"] > result["oldQuantity"]
    background_tasks.add_task(bring_service.schedule_stock_change, body.product_id, increased)
    return result


@router.post("/relocations", status_code=201)
def create_relocation_route(
    body: RelocationCreateRequest, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        result = relocate(
            db, cs, body.operation_id, body.id, body.product_id, body.from_location_id,
            body.to_location_id, body.stock_kind, body.quantity, body.timestamp,
            device.user_id, device.device_id, Source.ANDROID.value,
        )
    background_tasks.add_task(bring_service.schedule_stock_change, body.product_id, False)
    return result
