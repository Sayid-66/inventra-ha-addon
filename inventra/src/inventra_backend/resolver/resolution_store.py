from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from uuid6 import uuid7

from ..config import Settings
from ..db.models import ResolutionResult


def _new_resolution_id() -> str:
    return str(uuid7())


def create_resolution(
    db: Session, barcode: str, proposed_fields: dict, settings: Settings,
) -> str:
    resolution_id = _new_resolution_id()
    now = datetime.utcnow()
    db.add(ResolutionResult(
        resolution_id=resolution_id, barcode=barcode,
        proposed_fields_json=json.dumps(proposed_fields),
        created_at=now, expires_at=now + timedelta(seconds=settings.resolver_result_ttl_seconds),
    ))
    db.flush()
    return resolution_id


def get_resolution(db: Session, resolution_id: str) -> dict | None:
    row = db.get(ResolutionResult, resolution_id)
    if row is None or row.expires_at <= datetime.utcnow():
        return None
    return {"barcode": row.barcode, "proposed_fields": json.loads(row.proposed_fields_json)}
