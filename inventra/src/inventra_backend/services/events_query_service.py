from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ConsumptionEvent, CorrectionEvent, PurchaseEvent, RelocationEvent
from .inventory_service import (
    _consumption_event_to_dict, _correction_event_to_dict, _purchase_event_to_dict, _relocation_event_to_dict,
)


def _envelope(event_type: str, event_dict: dict) -> dict:
    return {
        "eventId": event_dict["eventId"],
        "type": event_type,
        "productId": event_dict["productId"],
        "timestamp": event_dict["timestamp"],
        "userId": event_dict["userId"],
        "sourceDeviceId": event_dict["sourceDeviceId"],
        "source": event_dict["source"],
        "payload": event_dict,
    }


def list_events(db: Session, product_id: Optional[str] = None) -> list[dict]:
    def _rows(model):
        stmt = select(model)
        if product_id is not None:
            stmt = stmt.where(model.product_id == product_id)
        return db.execute(stmt).scalars().all()

    envelopes = (
        [_envelope("PURCHASE", _purchase_event_to_dict(e)) for e in _rows(PurchaseEvent)]
        + [_envelope("CONSUMPTION", _consumption_event_to_dict(e)) for e in _rows(ConsumptionEvent)]
        + [_envelope("CORRECTION", _correction_event_to_dict(e)) for e in _rows(CorrectionEvent)]
        + [_envelope("RELOCATION", _relocation_event_to_dict(e)) for e in _rows(RelocationEvent)]
    )
    envelopes.sort(key=lambda e: e["timestamp"])
    return envelopes
