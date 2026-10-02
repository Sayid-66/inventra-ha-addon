from datetime import timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import BringWatchState, Device, Product
from ..errors import BusinessRuleViolation
from ..services import bring_service

router = APIRouter(prefix="/bring/status", tags=["bring"])


def _rows():
    return select(BringWatchState).join(Product).where(Product.deleted_at.is_(None))


def _response(row):
    updated = row.updated_at
    if updated is not None:
        updated = updated.replace(tzinfo=timezone.utc) if updated.tzinfo is None else updated.astimezone(timezone.utc)
    return {
        "productId": row.product_id, "state": row.state, "origin": row.origin,
        "itemName": row.bring_item_name, "lastError": row.last_error,
        "retryCount": row.retry_count, "updatedAt": updated.isoformat() if updated else None,
    }


@router.get("")
def list_status(db: Session = Depends(get_db), device: Device = Depends(require_device)):
    return [_response(row) for row in db.scalars(_rows().order_by(BringWatchState.product_id))]


@router.get("/{product_id}")
def get_status(product_id: str, db: Session = Depends(get_db), device: Device = Depends(require_device)):
    row = db.scalar(_rows().where(BringWatchState.product_id == product_id))
    if row is None:
        raise HTTPException(404, "Bring status not found")
    return _response(row)


@router.post("/{product_id}/retry")
def retry_status(product_id: str, background_tasks: BackgroundTasks,
                 db: Session = Depends(get_db), device: Device = Depends(require_device)):
    row = db.scalar(_rows().where(BringWatchState.product_id == product_id))
    if row is None or row.state != "ERROR":
        raise BusinessRuleViolation("BRING_NOT_IN_ERROR", "Bring status is not in ERROR")
    bring_service.on_product_deleted(db, product_id)
    # Background tasks run before the dependency's final commit; make deletion visible first.
    db.commit()
    background_tasks.add_task(bring_service.schedule_stock_change, product_id, False)
    return {"productId": product_id, "state": None}
