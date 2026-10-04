from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth.device_token import require_device
from ..db.base import get_db
from ..db.models import Device
from ..services.sync_service import fetch_sync_page

router = APIRouter(tags=["sync"])


@router.get("/sync", responses={200: {
    "description": "Sync changes. Batch snapshots preserve unknown freezing dates as storedAt: null.",
    "content": {"application/json": {"schema": {
        "type": "object",
        "properties": {"changes": {"type": "array", "items": {
            "type": "object", "properties": {"snapshot": {
                "type": "object", "additionalProperties": True,
                "properties": {"storedAt": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": "Batch only: epoch milliseconds; in a freezer, the freezing date. Null means unknown.",
                }},
            }},
        }}},
    }}},
}})
def sync_route(
    since_revision: int = Query(alias="since_revision"),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db), device: Device = Depends(require_device),
):
    return fetch_sync_page(db, since_revision, limit)
