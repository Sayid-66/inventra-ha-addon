from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..services.events_query_service import list_events

router = APIRouter(tags=["events"])


@router.get("/events")
def list_events_route(
    productId: Optional[str] = Query(default=None),
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    return list_events(db, productId)
