from sqlalchemy import select

from inventra_backend.db.base import Base, configure_engine, session_scope
from inventra_backend.db.models import ChangeLog, ChangeKind, RevisionCounter
from inventra_backend.revision.change_log import change_set


def test_multiple_records_in_one_changeset_share_one_revision(tmp_path):
    engine = configure_engine(str(tmp_path / "t.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(RevisionCounter(id=0, current_revision=0))
    with session_scope(engine) as db:
        with change_set(db) as cs:
            cs.record("Product", "p1", ChangeKind.CREATE, {"id": "p1"})
            cs.record("Batch", "b1", ChangeKind.CREATE, {"id": "b1"})
            revision_seen_inside = cs.revision
    with session_scope(engine) as db:
        rows = db.execute(select(ChangeLog).order_by(ChangeLog.id)).scalars().all()
        assert len(rows) == 2
        assert rows[0].revision == rows[1].revision == revision_seen_inside


def test_separate_changesets_get_increasing_revisions(tmp_path):
    engine = configure_engine(str(tmp_path / "t2.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(RevisionCounter(id=0, current_revision=0))
    revisions = []
    for i in range(3):
        with session_scope(engine) as db:
            with change_set(db) as cs:
                cs.record("Product", f"p{i}", ChangeKind.CREATE, {"id": f"p{i}"})
                revisions.append(cs.revision)
    assert revisions == sorted(set(revisions))
    assert len(set(revisions)) == 3


def test_changeset_with_no_records_allocates_no_revision(tmp_path):
    engine = configure_engine(str(tmp_path / "t3.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(RevisionCounter(id=0, current_revision=0))
    with session_scope(engine) as db:
        with change_set(db):
            pass  # no .record() calls
    with session_scope(engine) as db:
        assert db.get(RevisionCounter, 0).current_revision == 0
