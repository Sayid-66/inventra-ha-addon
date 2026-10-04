from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ChangeLog
from .instance_service import get_addon_version, get_current_revision, get_instance_id


# Bump whenever a migration changes synced data without change_log entries.
# 2 = units catalog migration 0006.
SYNC_DATA_EPOCH = 2


def fetch_sync_page(db: Session, since_revision: int, limit: int = 200) -> dict:
    stmt = (
        select(ChangeLog)
        .where(ChangeLog.revision > since_revision)
        .order_by(ChangeLog.revision, ChangeLog.id)
        .limit(limit + 1)
    )
    rows = list(db.execute(stmt).scalars().all())
    if not rows:
        return {
            "dataEpoch": SYNC_DATA_EPOCH,
            "instanceId": get_instance_id(db),
            "addonVersion": get_addon_version(),
            "currentRevision": get_current_revision(db),
            "changes": [], "nextRevision": since_revision, "hasMore": False,
        }

    if len(rows) <= limit:
        page_rows = rows
    else:
        boundary_revision = rows[limit - 1].revision
        lookahead_revision = rows[limit].revision
        if lookahead_revision == boundary_revision:
            # The page boundary falls inside a revision — never split it.
            page_rows = [r for r in rows[:limit] if r.revision != boundary_revision]
            if not page_rows:
                # This single revision alone has more rows than `limit`;
                # forward progress still requires returning ALL of them.
                page_rows = list(
                    db.execute(
                        select(ChangeLog)
                        .where(ChangeLog.revision == boundary_revision)
                        .order_by(ChangeLog.id)
                    ).scalars().all()
                )
        else:
            page_rows = rows[:limit]

    next_revision = max(r.revision for r in page_rows)
    has_more = db.execute(
        select(ChangeLog.id).where(ChangeLog.revision > next_revision).limit(1)
    ).first() is not None

    return {
        "dataEpoch": SYNC_DATA_EPOCH,
        "instanceId": get_instance_id(db),
        "addonVersion": get_addon_version(),
        "currentRevision": get_current_revision(db),
        "changes": [
            {
                "revision": r.revision, "entityType": r.entity_type, "entityId": r.entity_id,
                "changeKind": r.change_kind, "snapshot": json.loads(r.snapshot),
            }
            for r in page_rows
        ],
        "nextRevision": next_revision,
        "hasMore": has_more,
    }
