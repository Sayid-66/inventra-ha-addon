import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[2]
HEAD = "0011_instance_identity"
PARENT = "0009_release_barcodes_of_deleted_products"


def migrate(path, command, revision):
    subprocess.run([sys.executable, "-m", "alembic", command, revision],
                   cwd=ROOT, env={**os.environ, "INVENTRA_DB_PATH": str(path)},
                   check=True, timeout=30)


@pytest.mark.parametrize("legacy", [False, True])
def test_scrub_preserves_other_rows_and_is_repeatable(tmp_path, legacy):
    path = tmp_path / "scrub.db"
    migrate(path, "upgrade", PARENT)
    engine = create_engine(f"sqlite:///{path}")
    snapshots = {
        "ordinary": ' { "ok": true } ',
        "device-only": '{"deviceId": "device"}',
        "token-only": '{"token": "unrelated"}',
        "invalid": 'not json',
        "array": '[{"deviceId":"device", "token":"untouched"}]',
        "null": 'null',
        "string": '"text"',
    }
    if legacy:
        snapshots["pair"] = json.dumps({"deviceId": "device", "token": "legacy-bearer", "extra": "Küche"})
    with engine.begin() as db:
        for operation_id, snapshot in snapshots.items():
            db.execute(text("INSERT INTO processed_operations (operation_id, payload_hash, result_snapshot, created_at) VALUES (:id, :hash, :snapshot, '2026-10-02')"),
                       {"id": operation_id, "hash": "a" * 64, "snapshot": snapshot})
    migrate(path, "upgrade", "head")
    if legacy:
        snapshots["pair"] = json.dumps({"deviceId": "device", "extra": "Küche"})
    def check():
        with engine.connect() as db:
            assert dict(db.execute(text("SELECT operation_id, result_snapshot FROM processed_operations")).all()) == snapshots
            assert set(db.execute(text("SELECT payload_hash FROM processed_operations")).scalars()) == {"a" * 64}
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD
    check()
    migrate(path, "upgrade", "head")
    check()
    migrate(path, "downgrade", PARENT)
    with engine.connect() as db:
        assert dict(db.execute(text("SELECT operation_id, result_snapshot FROM processed_operations")).all()) == snapshots
    migrate(path, "upgrade", "head")
    check()
    engine.dispose()
