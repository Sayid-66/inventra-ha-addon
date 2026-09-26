from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..revision.change_log import change_set
from ..schemas.stores import StoreCreateRequest, StoreUpdateRequest, StoreDeleteRequest, StoreResponse
from ..services.store_service import create_store, update_store, soft_delete_store, list_stores

router = APIRouter(prefix="/stores", tags=["stores"])


@router.get("", response_model=list[StoreResponse])
def list_stores_route(db: Session = Depends(get_db), device: Device = Depends(require_device)):
    return list_stores(db)


@router.post("", response_model=StoreResponse, status_code=201)
def create_store_route(body: StoreCreateRequest, db: Session = Depends(get_db), device: Device = Depends(require_device)):
    with change_set(db) as cs:
        return create_store(db, cs, body.operation_id, body.id, body.name)


@router.patch("/{store_id}", response_model=StoreResponse)
def update_store_route(
    store_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")], body: StoreUpdateRequest, db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        return update_store(db, cs, body.operation_id, store_id, body.name, body.version)


@router.delete("/{store_id}", response_model=StoreResponse)
def delete_store_route(
    store_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")], body: StoreDeleteRequest, db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    with change_set(db) as cs:
        return soft_delete_store(db, cs, body.operation_id, store_id, body.version)
