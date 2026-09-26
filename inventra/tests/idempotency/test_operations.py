import pytest

from inventra_backend.db.base import Base, configure_engine, session_scope
from inventra_backend.idempotency.operations import run_idempotent, OperationPayloadMismatch


def test_first_call_performs_and_stores_result(tmp_path):
    engine = configure_engine(str(tmp_path / "t.db"))
    Base.metadata.create_all(engine)
    calls = []
    with session_scope(engine) as db:
        result = run_idempotent(db, "op1", {"a": 1}, lambda: calls.append(1) or {"ok": True})
    assert result == {"ok": True}
    assert calls == [1]


def test_replay_same_payload_does_not_reperform(tmp_path):
    engine = configure_engine(str(tmp_path / "t2.db"))
    Base.metadata.create_all(engine)
    calls = []

    def perform():
        calls.append(1)
        return {"ok": True}

    with session_scope(engine) as db:
        run_idempotent(db, "op1", {"a": 1}, perform)
    with session_scope(engine) as db:
        result = run_idempotent(db, "op1", {"a": 1}, perform)
    assert result == {"ok": True}
    assert calls == [1]  # perform() ran exactly once, not twice


def test_replay_different_payload_raises(tmp_path):
    engine = configure_engine(str(tmp_path / "t3.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        run_idempotent(db, "op1", {"a": 1}, lambda: {"ok": True})
    with pytest.raises(OperationPayloadMismatch):
        with session_scope(engine) as db:
            run_idempotent(db, "op1", {"a": 2}, lambda: {"ok": True})
