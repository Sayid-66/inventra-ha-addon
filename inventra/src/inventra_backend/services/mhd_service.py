from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .stock_query_service import build_summaries
from ..db.models import ChangeKind, MhdWarningAckState
from ..idempotency.operations import run_idempotent
from ..revision.change_log import ChangeSet


def mhd_warning_level(next_mhd: str | None, today: date) -> str:
    if next_mhd is None:
        return "NONE"
    days_until = (date.fromisoformat(next_mhd) - today).days
    if days_until <= 3:
        return "RED"
    if days_until <= 7:
        return "YELLOW"
    return "NONE"


def red_mhd_warning_signature(db: Session, today: date | None = None) -> str:
    today = today or date.today()
    red_entries = [
        (s["productId"], s["nextMhd"]) for s in build_summaries(db)
        if mhd_warning_level(s["nextMhd"], today) == "RED"
    ]
    red_entries.sort(key=lambda e: e[0])
    return "|".join(f"{pid}:{mhd}" for pid, mhd in red_entries)


def _get_or_create_state(db: Session) -> MhdWarningAckState:
    state = db.get(MhdWarningAckState, 0)
    if state is None:
        state = MhdWarningAckState(id=0, acknowledged_signature="")
        db.add(state)
        db.flush()
    return state


def get_ack_state(db: Session) -> dict:
    return {"acknowledgedSignature": _get_or_create_state(db).acknowledged_signature}


def acknowledge(db: Session, cs: ChangeSet, operation_id: str) -> dict:
    def perform() -> dict:
        current_signature = red_mhd_warning_signature(db)
        state = _get_or_create_state(db)
        if current_signature != "":
            state.acknowledged_signature = current_signature
            db.flush()
            cs.record("MhdWarningAckState", "0", ChangeKind.UPDATE, {"acknowledgedSignature": current_signature})
        return {"acknowledgedSignature": state.acknowledged_signature}

    return run_idempotent(db, operation_id, {"op": "acknowledge_mhd_warning"}, perform)
