from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..revision.change_log import change_set
from ..services.mhd_service import acknowledge, get_ack_state

router = APIRouter(tags=["mhd"])


class AckRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    model_config = {"populate_by_name": True}


@router.get("/mhd-warning-state")
def get_mhd_warning_state_route(db: Session = Depends(get_db), device: Device = Depends(require_device)):
    return get_ack_state(db)


@router.post("/mhd-warning-state/ack")
def ack_mhd_warning_state_route(
    body: AckRequest, db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        return acknowledge(db, cs, body.operation_id)
