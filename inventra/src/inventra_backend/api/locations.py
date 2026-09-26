from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..revision.change_log import change_set
from ..schemas.locations import (
    LocationCreateRequest, LocationUpdateRequest, LocationDeleteRequest, LocationResponse,
)
from ..services.location_service import (
    create_location, update_location, soft_delete_location, list_locations,
)

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("", response_model=list[LocationResponse])
def list_locations_route(db: Session = Depends(get_db), device: Device = Depends(require_device)):
    return list_locations(db)


@router.post("", response_model=LocationResponse, status_code=201)
def create_location_route(
    body: LocationCreateRequest, db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        return create_location(db, cs, body.operation_id, body.id, body.name)


@router.patch("/{location_id}", response_model=LocationResponse)
def update_location_route(
    location_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")], body: LocationUpdateRequest,
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        return update_location(db, cs, body.operation_id, location_id, body.name, body.version)


@router.delete("/{location_id}", response_model=LocationResponse)
def delete_location_route(
    location_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")], body: LocationDeleteRequest,
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        return soft_delete_location(db, cs, body.operation_id, location_id, body.version)
