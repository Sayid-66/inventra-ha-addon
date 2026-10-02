from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import get_db
from ..db.models import Device

LAST_SEEN_UPDATE_INTERVAL = timedelta(minutes=5)


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
    now = datetime.utcnow()
    if device.last_seen_at is None or now - device.last_seen_at > LAST_SEEN_UPDATE_INTERVAL:
        device.last_seen_at = now
    # Every session uses BEGIN IMMEDIATE; release its write lock even when throttled.
    db.commit()
    return device
