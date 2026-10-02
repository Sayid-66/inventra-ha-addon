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


@pytest.mark.parametrize("redacted", [False, True])
def test_snapshot_redaction_preserves_first_result_and_default_bytes(tmp_path, redacted):
    import json
    from inventra_backend.db.models import ProcessedOperation

    engine = configure_engine(str(tmp_path / "redaction.db"))
    Base.metadata.create_all(engine)
    full = {"deviceId": "device", "token": "secret", "label": "Küche"}
    stored = {key: value for key, value in full.items() if key != "token"} if redacted else full
    options = {"redact": lambda result: {key: value for key, value in result.items() if key != "token"}} if redacted else {}
    with session_scope(engine) as db:
        assert run_idempotent(db, "op", {}, lambda: full.copy(), **options) == full
        assert db.get(ProcessedOperation, "op").result_snapshot == json.dumps(stored)
    with session_scope(engine) as db:
        assert run_idempotent(db, "op", {}, lambda: pytest.fail("reperformed"), **options) == stored


@pytest.mark.parametrize("redacted", [False, True])
def test_integrity_race_returns_winning_snapshot(redacted):
    import json
    from unittest.mock import Mock
    from sqlalchemy.exc import IntegrityError
    from inventra_backend.db.models import ProcessedOperation
    from inventra_backend.idempotency.operations import _hash_payload

    stored = {"deviceId": "winner"} if redacted else {"deviceId": "winner", "token": "winning-token"}
    db = Mock()
    db.get.side_effect = [None, ProcessedOperation(operation_id="op", payload_hash=_hash_payload({}),
                                                 result_snapshot=json.dumps(stored))]
    db.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    options = {"redact": lambda result: {"deviceId": result["deviceId"]}} if redacted else {}
    result = run_idempotent(db, "op", {}, lambda: {"deviceId": "loser", "token": "losing-token"}, **options)
    assert result == stored
    db.rollback.assert_called_once()
    inserted = db.add.call_args.args[0]
    assert json.loads(inserted.result_snapshot) == ({"deviceId": "loser"} if redacted else {"deviceId": "loser", "token": "losing-token"})
