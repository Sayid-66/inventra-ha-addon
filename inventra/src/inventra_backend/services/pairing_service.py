from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .ids import new_id
from ..auth.device_token import hash_token
from ..db.models import Device, PairingCode
from ..errors import BusinessRuleViolation
from ..idempotency.operations import run_idempotent


def create_pairing_code(db: Session, user_id: str, ttl_seconds: int) -> dict:
    """Not wrapped in operationId idempotency: each call is an explicit,
    low-stakes 'start pairing' action — generating one extra unused code
    on a client retry has no harmful effect, unlike a business write."""
    code = secrets.token_urlsafe(9)
    now = datetime.utcnow()
    pairing_code = PairingCode(code=code, user_id=user_id, created_at=now, expires_at=now + timedelta(seconds=ttl_seconds))
    db.add(pairing_code)
    db.flush()
    return {"code": code, "expiresAt": pairing_code.expires_at.isoformat()}


def exchange_pairing_code(db: Session, operation_id: str, code: str, device_name: str) -> dict:
    def perform() -> dict:
        pairing_code = db.get(PairingCode, code)
        if pairing_code is None:
            raise BusinessRuleViolation("PAIRING_CODE_INVALID", "unknown pairing code")
        if pairing_code.consumed_at is not None:
            raise BusinessRuleViolation("PAIRING_CODE_ALREADY_USED", "pairing code already used")
        if pairing_code.expires_at < datetime.utcnow():
            raise BusinessRuleViolation("PAIRING_CODE_EXPIRED", "pairing code expired")

        device_id = new_id()
        token = secrets.token_urlsafe(32)
        device = Device(
            device_id=device_id, user_id=pairing_code.user_id, device_name=device_name,
            token_hash=hash_token(token),
        )
        db.add(device)
        pairing_code.consumed_at = datetime.utcnow()
        db.flush()
        return {"deviceId": device_id, "token": token}

    payload = {"op": "exchange_pairing_code", "code": code, "deviceName": device_name}
    result = run_idempotent(
        db, operation_id, payload, perform,
        redact=lambda result: {key: value for key, value in result.items() if key != "token"},
    )
    if "token" not in result:
        device = db.get(Device, result["deviceId"])
        if device is None or device.revoked_at is not None:
            raise BusinessRuleViolation("DEVICE_REVOKED", "device no longer exists or is revoked")
        pairing_code = db.get(PairingCode, code)
        if (device.last_seen_at is not None or pairing_code is None
                or pairing_code.consumed_at is None
                or datetime.utcnow() - pairing_code.consumed_at > timedelta(minutes=10)):
            raise BusinessRuleViolation("PAIRING_CODE_ALREADY_USED", "pairing replay no longer permitted")
        token = secrets.token_urlsafe(32)
        device.token_hash = hash_token(token)
        db.flush()
        return {"deviceId": device.device_id, "token": token}
    return result


def revoke_device(db: Session, device_id: str) -> None:
    device = db.get(Device, device_id)
    if device is None:
        raise BusinessRuleViolation("DEVICE_NOT_FOUND", f"device {device_id} does not exist")
    device.revoked_at = datetime.utcnow()
    db.flush()
