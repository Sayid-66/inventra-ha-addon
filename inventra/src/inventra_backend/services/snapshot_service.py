from __future__ import annotations

import json

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from ..db.models import ChangeLog
from .instance_service import get_current_revision, get_instance_id
from .sync_service import SYNC_DATA_EPOCH


def get_current_max_revision(db: Session) -> int:
    return db.execute(select(func.max(ChangeLog.revision))).scalar_one() or 0


def fetch_snapshot_page(
    db: Session, snapshot_revision: int | None, entity_type_cursor: str | None,
    entity_id_cursor: str | None, limit: int = 200,
) -> dict:
    if snapshot_revision is None:
        snapshot_revision = get_current_max_revision(db)

    latest_ids_subq = (
        select(ChangeLog.entity_type, ChangeLog.entity_id, func.max(ChangeLog.id).label("max_id"))
        .where(ChangeLog.revision <= snapshot_revision)
        .group_by(ChangeLog.entity_type, ChangeLog.entity_id)
        .subquery()
    )
    stmt = (
        select(ChangeLog)
        .join(latest_ids_subq, ChangeLog.id == latest_ids_subq.c.max_id)
        .order_by(ChangeLog.entity_type, ChangeLog.entity_id)
    )
    if entity_type_cursor is not None and entity_id_cursor is not None:
        stmt = stmt.where(
            tuple_(ChangeLog.entity_type, ChangeLog.entity_id) > tuple_(entity_type_cursor, entity_id_cursor)
        )
    stmt = stmt.limit(limit + 1)
    rows = list(db.execute(stmt).scalars().all())

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    next_cursor = None
    if has_more:
        next_cursor = {"entityType": page_rows[-1].entity_type, "entityId": page_rows[-1].entity_id}

    return {
        "dataEpoch": SYNC_DATA_EPOCH,
        "instanceId": get_instance_id(db),
        "currentRevision": get_current_revision(db),
        "snapshotRevision": snapshot_revision,
        "entities": [
            {
                "entityType": r.entity_type, "entityId": r.entity_id,
                "changeKind": r.change_kind, "snapshot": json.loads(r.snapshot),
            }
            for r in page_rows
        ],
        "nextCursor": next_cursor,
    }
