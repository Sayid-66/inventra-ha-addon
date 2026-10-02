from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from inventra_backend.db.base import Base, configure_engine
from inventra_backend.db.models import InstanceMeta, RevisionCounter, ChangeLog
from inventra_backend.services.instance_service import get_instance_id
from inventra_backend.services.sync_service import fetch_sync_page
from inventra_backend.services.snapshot_service import fetch_snapshot_page


def test_create_all_and_lazy_identity_persist_without_revision(tmp_path):
    engine = configure_engine(str(tmp_path / "identity.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seeded = get_instance_id(db)
        assert UUID(seeded).version == 4
        assert len(db.scalars(select(InstanceMeta)).all()) == 1
        db.execute(delete(InstanceMeta))
        identity = get_instance_id(db)
        assert identity != seeded
        assert get_instance_id(db) == identity
        assert db.get(RevisionCounter, 0) is None
        assert db.scalars(select(ChangeLog)).all() == []
        db.commit()
    with Session(engine) as db:
        assert get_instance_id(db) == identity
        assert fetch_sync_page(db, 1000)["currentRevision"] == 0
        assert fetch_snapshot_page(db, None, None, None)["currentRevision"] == 0
    other = configure_engine(str(tmp_path / "other.db"))
    Base.metadata.create_all(other)
    with Session(other) as db:
        assert get_instance_id(db) != identity
    engine.dispose()
    other.dispose()


def test_responses_read_counter_instead_of_change_log_max(db_session):
    counter = db_session.get(RevisionCounter, 0)
    counter.current_revision = 700
    db_session.flush()
    sync = fetch_sync_page(db_session, 1000)
    snapshot = fetch_snapshot_page(db_session, None, None, None)
    assert sync["currentRevision"] == snapshot["currentRevision"] == 700
    assert sync["instanceId"] == snapshot["instanceId"]
    assert sync["nextRevision"] == 1000
    assert sync["changes"] == []
    assert snapshot["snapshotRevision"] == 0
