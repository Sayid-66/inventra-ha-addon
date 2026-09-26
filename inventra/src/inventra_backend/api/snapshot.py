from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..services.snapshot_service import fetch_snapshot_page

router = APIRouter(tags=["snapshot"])


@router.get("/snapshot")
def snapshot_route(
    snapshot_revision: Optional[int] = Query(default=None, alias="snapshot_revision"),
    entity_type_cursor: Optional[str] = Query(default=None, alias="entity_type_cursor"),
    entity_id_cursor: Optional[str] = Query(default=None, alias="entity_id_cursor"),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    return fetch_snapshot_page(db, snapshot_revision, entity_type_cursor, entity_id_cursor, limit)
