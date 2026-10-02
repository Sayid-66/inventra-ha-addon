from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ..db.models import InstanceMeta, RevisionCounter


def get_instance_id(db: Session) -> str:
    """Return the dataset UUID; lazy initialization belongs to the caller's transaction."""
    instance_id = db.scalar(select(InstanceMeta.instance_id).where(InstanceMeta.id == 0))
    if instance_id is None:
        db.execute(insert(InstanceMeta).values(
            id=0, instance_id=str(uuid4()), created_at=datetime.utcnow(),
        ).on_conflict_do_nothing(index_elements=["id"]))
        instance_id = db.scalar(select(InstanceMeta.instance_id).where(InstanceMeta.id == 0))
    return instance_id


def get_current_revision(db: Session) -> int:
    return db.scalar(select(RevisionCounter.current_revision).where(RevisionCounter.id == 0)) or 0
