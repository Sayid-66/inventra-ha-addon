from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db.models import ChangeLog, ChangeKind, RevisionCounter


def _allocate_revision(session: Session) -> int:
    session.execute(
        update(RevisionCounter)
        .where(RevisionCounter.id == 0)
        .values(current_revision=RevisionCounter.current_revision + 1)
    )
    return session.execute(
        select(RevisionCounter.current_revision).where(RevisionCounter.id == 0)
    ).scalar_one()


class ChangeSet:
    """Collects change_log rows for one write transaction. All rows added
    through `record()` share exactly one `revision`, allocated lazily on
    the first `record()` call — a changeset that never records anything
    never consumes a revision number."""

    def __init__(self, session: Session):
        self._session = session
        self._revision: int | None = None

    @property
    def revision(self) -> int:
        if self._revision is None:
            self._revision = _allocate_revision(self._session)
        return self._revision

    def record(self, entity_type: str, entity_id: str, change_kind: ChangeKind, snapshot: dict) -> None:
        self._session.add(
            ChangeLog(
                revision=self.revision,
                entity_type=entity_type,
                entity_id=entity_id,
                change_kind=change_kind.value,
                snapshot=json.dumps(snapshot),
            )
        )


@contextmanager
def change_set(session: Session) -> Iterator[ChangeSet]:
    yield ChangeSet(session)
