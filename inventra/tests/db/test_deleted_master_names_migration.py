import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session

from inventra_backend.db.models import ChangeLog, Location, Store
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services import location_service, store_service


def upgrade(path, revision):
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "INVENTRA_DB_PATH": str(path)},
        check=True, timeout=30,
    )


@pytest.mark.parametrize("table,model,service,create", [
    ("locations", Location, location_service, "create_location"),
    ("stores", Store, store_service, "create_store"),
])
def test_0008_frees_deleted_names_without_sync_changes(tmp_path, table, model, service, create):
    path = tmp_path / "master.db"
    upgrade(path, "0007_resync_product_snapshots")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as db:
        db.execute(text(f"INSERT INTO {table} (id, name, normalized_name, version, deleted_at) VALUES ('old', 'Keller', 'keller', 2, '2026-09-26')"))
        db.execute(text(f"INSERT INTO {table} (id, name, normalized_name, version, deleted_at) VALUES ('already', 'Other', '~deleted~already~other', 2, '2026-09-26')"))
        db.execute(text(f"INSERT INTO {table} (id, name, normalized_name, version) VALUES ('live', 'Live', 'live', 1)"))
    upgrade(path, "0008_free_deleted_master_names")
    with Session(engine) as db:
        assert db.get(model, "old").normalized_name == "~deleted~old~keller"
        assert db.get(model, "old").name == "Keller"
        assert db.get(model, "old").version == 2
        assert db.get(model, "already").normalized_name == "~deleted~already~other"
        assert db.get(model, "live").normalized_name == "live"
        assert db.scalars(select(ChangeLog)).all() == []
        result = getattr(service, create)(db, ChangeSet(db), "create", "new", "Keller")
        db.commit()
        assert result["name"] == "Keller"
    engine.dispose()
