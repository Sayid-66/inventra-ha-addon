from __future__ import annotations

import hashlib
import json
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.models import ProcessedOperation


class OperationPayloadMismatch(Exception):
    def __init__(self, operation_id: str):
        super().__init__(f"operationId {operation_id} reused with a different payload")
        self.operation_id = operation_id


def _hash_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_idempotent(
    session: Session,
    operation_id: str,
    payload: dict,
    perform: Callable[[], dict],
    *,
    redact: Callable[[dict], dict] | None = None,
) -> dict:
    """Runs `perform` (the actual business write, which must itself add
    all its rows to `session` without committing) at most once per
    `operation_id`. `perform` is expected to run inside the same request
    transaction that will commit this ProcessedOperation row, so the
    business write and its idempotency record are atomic together
    (spec §7)."""
    payload_hash = _hash_payload(payload)
    existing = session.get(ProcessedOperation, operation_id)
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise OperationPayloadMismatch(operation_id)
        return json.loads(existing.result_snapshot)

    result = perform()
    try:
        session.add(
            ProcessedOperation(
                operation_id=operation_id,
                payload_hash=payload_hash,
                result_snapshot=json.dumps(redact(result) if redact is not None else result),
            )
        )
        session.flush()
    except IntegrityError:
        # Defend the lost-update serialization boundary if two writers still race on this insert.
        session.rollback()
        existing = session.get(ProcessedOperation, operation_id)
        if existing is None:
            raise
        if existing.payload_hash != payload_hash:
            raise OperationPayloadMismatch(operation_id)
        return json.loads(existing.result_snapshot)
    return result
