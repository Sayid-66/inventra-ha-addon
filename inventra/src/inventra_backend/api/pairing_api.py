from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db.base import get_db
from ..services.pairing_service import exchange_pairing_code

router = APIRouter(tags=["pairing"])


class PairRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    code: str
    device_name: str = Field(alias="deviceName")

    model_config = {"populate_by_name": True}


@router.post("/pair", status_code=201)
def pair_route(body: PairRequest, db: Session = Depends(get_db)):
    return exchange_pairing_code(db, body.operation_id, body.code, body.device_name)
