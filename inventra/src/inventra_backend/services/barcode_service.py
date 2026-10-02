from __future__ import annotations

from .barcode_helpers import _to_dict, tombstone_barcode

from sqlalchemy.orm import Session

from .product_service import get_product
from ..db.models import Barcode, ChangeKind
from ..errors import DuplicateEntityError, StaleVersionError
from ..idempotency.operations import run_idempotent
from ..revision.change_log import ChangeSet


def find_barcode(db: Session, code: str) -> Barcode | None:
    barcode = db.get(Barcode, code)
    if barcode is None or barcode.deleted_at is not None:
        return None
    return barcode


def assign_barcode(db: Session, cs: ChangeSet, operation_id: str, code: str, product_id: str) -> dict:
    def perform() -> dict:
        if get_product(db, product_id) is None:
            raise DuplicateEntityError("Barcode", "productId", product_id)  # target product must exist
        if db.get(Barcode, code) is not None:
            raise DuplicateEntityError("Barcode", "code", code)
        barcode = Barcode(code=code, product_id=product_id, version=1)
        db.add(barcode)
        db.flush()
        result = _to_dict(barcode)
        cs.record("Barcode", code, ChangeKind.CREATE, result)
        return result

    payload = {"op": "assign_barcode", "code": code, "productId": product_id}
    return run_idempotent(db, operation_id, payload, perform)


def soft_delete_barcode(db: Session, cs: ChangeSet, operation_id: str, code: str, version: int) -> dict:
    def perform() -> dict:
        barcode = db.get(Barcode, code)
        if barcode is None:
            raise DuplicateEntityError("Barcode", "code", code)
        if barcode.version != version:
            raise StaleVersionError("Barcode", code, _to_dict(barcode))
        return tombstone_barcode(db, cs, barcode)

    return run_idempotent(db, operation_id, {"op": "delete_barcode", "code": code, "version": version}, perform)
