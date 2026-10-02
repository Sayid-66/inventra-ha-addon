from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import ChangeLog, Device, RevisionCounter
from .instance_service import get_instance_id


@dataclass(frozen=True)
class DashboardStatus:
    instance_id: str
    current_revision: int
    last_activity_at: datetime | None
    active_device_count: int


def get_dashboard_status(db: Session) -> DashboardStatus:
    current_revision = db.scalar(
        select(RevisionCounter.current_revision).where(RevisionCounter.id == 0)
    )
    last_activity_at = db.scalar(select(func.max(ChangeLog.created_at)))
    active_device_count = db.scalar(
        select(func.count()).select_from(Device).where(Device.revoked_at.is_(None))
    )

    return DashboardStatus(
        instance_id=get_instance_id(db),
        current_revision=current_revision if current_revision is not None else 0,
        last_activity_at=last_activity_at,
        active_device_count=active_device_count or 0,
    )
