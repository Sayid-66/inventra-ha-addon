from datetime import datetime, timedelta

from inventra_backend.db.base import Base, configure_engine, session_scope
from inventra_backend.db.models import (
    ChangeLog, ChangeKind, RevisionCounter, ProcessedOperation, Device,
    PairingCode, MhdWarningAckState,
)


def test_change_log_multiple_rows_share_one_revision(tmp_path):
    engine = configure_engine(str(tmp_path / "t.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(ChangeLog(revision=1, entity_type="Product", entity_id="p1", change_kind=ChangeKind.CREATE, snapshot="{}"))
        db.add(ChangeLog(revision=1, entity_type="Batch", entity_id="b1", change_kind=ChangeKind.CREATE, snapshot="{}"))
    with session_scope(engine) as db:
        rows = db.query(ChangeLog).filter(ChangeLog.revision == 1).all()
        assert len(rows) == 2


def test_revision_counter_single_row(tmp_path):
    engine = configure_engine(str(tmp_path / "t2.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(RevisionCounter(id=0, current_revision=0))
    with session_scope(engine) as db:
        assert db.get(RevisionCounter, 0).current_revision == 0


def test_processed_operation_roundtrip(tmp_path):
    engine = configure_engine(str(tmp_path / "t3.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(ProcessedOperation(operation_id="op1", payload_hash="h1", result_snapshot="{}"))
    with session_scope(engine) as db:
        assert db.get(ProcessedOperation, "op1").payload_hash == "h1"


def test_device_and_pairing_code_roundtrip(tmp_path):
    engine = configure_engine(str(tmp_path / "t4.db"))
    Base.metadata.create_all(engine)
    now = datetime.utcnow()
    with session_scope(engine) as db:
        db.add(Device(device_id="d1", user_id="u1", device_name="Pixel", token_hash="hash1"))
        db.add(PairingCode(code="abc123", user_id="u1", expires_at=now + timedelta(minutes=5)))
    with session_scope(engine) as db:
        assert db.get(Device, "d1").revoked_at is None
        assert db.get(PairingCode, "abc123").consumed_at is None


def test_mhd_ack_state_single_row(tmp_path):
    engine = configure_engine(str(tmp_path / "t5.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(MhdWarningAckState(id=0, acknowledged_signature=""))
    with session_scope(engine) as db:
        assert db.get(MhdWarningAckState, 0).acknowledged_signature == ""
