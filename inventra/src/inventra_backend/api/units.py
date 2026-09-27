from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device, Unit
from ..schemas.units import UnitCreateRequest, UnitUpdateRequest, UnitResponse
from ..services.unit_service import save_unit, unit_response


router = APIRouter(prefix="/units", tags=["units"])


@router.get("", response_model=list[UnitResponse])
def list_units(db: Session = Depends(get_db), device: Device = Depends(require_device)):
    return [unit_response(unit) for unit in db.scalars(select(Unit).order_by(Unit.name, Unit.id))]


@router.post("", response_model=UnitResponse, status_code=201)
def create_unit(body: UnitCreateRequest, db: Session = Depends(get_db), device: Device = Depends(require_device)):
    return unit_response(save_unit(db, body.name, body.abbreviation))


@router.patch("/{unit_id}", response_model=UnitResponse)
def rename_unit(unit_id: str, body: UnitUpdateRequest, db: Session = Depends(get_db), device: Device = Depends(require_device)):
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    return unit_response(save_unit(db, body.name or unit.name, body.abbreviation or unit.abbreviation, unit))
