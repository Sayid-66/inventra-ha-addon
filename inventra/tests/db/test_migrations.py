from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, inspect

from inventra_backend.db.base import configure_engine


ADDON_ROOT = Path(__file__).resolve().parents[2]


def _migrate(db_path):
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ADDON_ROOT,
        env={**_inherited_env(), "INVENTRA_DB_PATH": db_path},
        check=True,
    )


def test_alembic_upgrade_head_creates_all_tables(tmp_path):
    db_path = tmp_path / "migrated.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ADDON_ROOT,
        env={**_inherited_env(), "INVENTRA_DB_PATH": str(db_path)},
        check=True,
    )
    engine = configure_engine(str(db_path))
    tables = set(inspect(engine).get_table_names())
    expected = {
        "products", "barcodes", "locations", "stores", "batches",
        "purchase_events", "consumption_events", "correction_events",
        "relocation_events", "change_log", "revision_counter",
        "processed_operations", "devices", "pairing_codes",
        "mhd_warning_ack_state", "alembic_version",
    }
    assert expected.issubset(tables)
    device_columns = {column["name"]: column for column in inspect(engine).get_columns("devices")}
    assert device_columns["last_seen_at"]["nullable"] is True


def test_0003_adds_bring_watch_state_and_device_default_location(tmp_path):
    db_path = str(tmp_path / "m3.db")
    _migrate(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("bring_watch_state")}
    assert columns == {
        "product_id", "state", "origin", "bring_item_name", "bring_uid",
        "lock_reason", "retry_count", "confirmation_deadline_at",
        "last_error", "last_checked_at", "created_at", "updated_at",
    }
    device_columns = {c["name"] for c in inspector.get_columns("devices")}
    assert "default_location_id" in device_columns


def _inherited_env():
    import os
    return dict(os.environ)


def test_0005_repairs_product_and_event_tombstones_via_alembic(tmp_path):
    import json
    from datetime import datetime
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from inventra_backend.db.models import (
        Product, Location, PurchaseEvent, ChangeLog, ChangeKind, Source,
    )
    from inventra_backend.revision.change_log import ChangeSet

    db_path = str(tmp_path / "history.db")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0004_product_resolver_fields"],
        cwd=ADDON_ROOT, env={**_inherited_env(), "INVENTRA_DB_PATH": db_path}, check=True,
    )
    engine = configure_engine(db_path)
    with Session(engine) as db:
        db.add(Product(id="deleted", name="Historical milk", version=2,
                       deleted_at=datetime(2026, 9, 26, 10)))
        db.add(Location(id="loc", name="Kitchen", normalized_name="kitchen", version=1))
        db.add(PurchaseEvent(id="purchase", product_id="deleted", timestamp=123,
                             barcode="123", location_id="loc", quantity=4,
                             user_id="user", source=Source.ANDROID))
        db.flush()
        cs = ChangeSet(db)
        cs.record("Product", "deleted", ChangeKind.DELETE, {"id": "deleted"})
        cs.record("PurchaseEvent", "purchase", ChangeKind.DELETE, {"id": "purchase"})
        db.commit()
    _migrate(db_path)
    with Session(engine) as db:
        rows = db.scalars(select(ChangeLog).order_by(ChangeLog.id)).all()
        assert [r.change_kind for r in rows] == ["DELETE", "DELETE", "UPDATE", "UPDATE"]
        assert json.loads(rows[2].snapshot)["deletedAt"] == "2026-09-26T10:00:00"
        assert json.loads(rows[3].snapshot)["quantity"] == 4
        assert rows[2].revision == rows[3].revision > rows[1].revision
    _migrate(db_path)
    with Session(engine) as db:
        assert len(db.scalars(select(ChangeLog)).all()) == 4
    engine.dispose()
