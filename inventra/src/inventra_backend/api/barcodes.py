from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..revision.change_log import change_set
from ..schemas.barcodes import BarcodeAssignRequest, BarcodeDeleteRequest, BarcodeResponse
from ..services.barcode_service import assign_barcode, soft_delete_barcode

router = APIRouter(prefix="/barcodes", tags=["barcodes"])


@router.post("", response_model=BarcodeResponse, status_code=201)
def assign_barcode_route(
    body: BarcodeAssignRequest, db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        return assign_barcode(db, cs, body.operation_id, body.code, body.product_id)


@router.delete("/{code}", response_model=BarcodeResponse)
def delete_barcode_route(
    code: str, body: BarcodeDeleteRequest, db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        return soft_delete_barcode(db, cs, body.operation_id, code, body.version)
