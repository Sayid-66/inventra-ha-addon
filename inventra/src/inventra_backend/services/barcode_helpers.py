from datetime import datetime

from sqlalchemy.orm import Session

from ..db.models import Barcode, ChangeKind
from ..revision.change_log import ChangeSet


def _to_dict(barcode: Barcode) -> dict:
    return {
        "code": barcode.code,
        "productId": barcode.product_id,
        "version": barcode.version,
        "deletedAt": barcode.deleted_at.isoformat() if barcode.deleted_at else None,
    }


def tombstone_barcode(db: Session, cs: ChangeSet, barcode: Barcode) -> dict:
    barcode.version += 1
    barcode.deleted_at = datetime.utcnow()
    db.flush()
    result = _to_dict(barcode)
    cs.record("Barcode", barcode.code, ChangeKind.DELETE, result)
    return result
