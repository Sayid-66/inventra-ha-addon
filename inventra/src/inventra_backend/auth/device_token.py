from __future__ import annotations

import hashlib
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import get_db
from ..db.models import Device


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_device(request: Request, db: Session = Depends(get_db)) -> Device:
    if getattr(request.state, "trust_zone", None) != "api":
        raise HTTPException(status_code=403, detail="device-token endpoints are API-zone only")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth.removeprefix("Bearer ")
    device = db.execute(
        select(Device).where(Device.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if device is None or device.revoked_at is not None:
        raise HTTPException(status_code=401, detail="invalid or revoked device token")
    device.last_seen_at = datetime.utcnow()
    db.commit()
    return device
