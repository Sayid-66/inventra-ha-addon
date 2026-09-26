from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..services.stock_query_service import get_product_detail, list_current_stock, list_stock_history

router = APIRouter(tags=["stock"])


@router.get("/stock")
def get_stock_route(
    view: Literal["current", "history"] = Query(default="current"),
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    return list_current_stock(db) if view == "current" else list_stock_history(db)


@router.get("/products/{product_id}/detail")
def get_product_detail_route(
    product_id: str, db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    detail = get_product_detail(db, product_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="product not found")
    return detail
