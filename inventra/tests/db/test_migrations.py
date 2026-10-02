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
        "relocation_events", "change_log", "revision_counter", "instance_meta",
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


def test_0006_migrates_legacy_units_and_preserves_references(tmp_path):
    import sqlite3
    from uuid import UUID
    import pytest

    db_path = str(tmp_path / "legacy-units.db")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0005_product_history"],
        cwd=ADDON_ROOT, env={**_inherited_env(), "INVENTRA_DB_PATH": db_path}, check=True,
    )
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO locations (id, name, normalized_name, version) VALUES ('loc', 'Kitchen', 'kitchen', 1)")
        db.execute("INSERT INTO stores (id, name, normalized_name, version) VALUES ('store', 'Shop', 'shop', 1)")
        db.execute("INSERT INTO devices (device_id, user_id, device_name, token_hash, created_at) VALUES ('device', 'user', 'Phone', 'hash', '2026-09-27')")
        for i, label in enumerate(["g", "GRAMM", "KG", "Dose", "dose", None, ""]):
            db.execute("INSERT INTO products (id, name, quantity, quantity_unit, content_unit_label, version) VALUES (?, 'Test', 400, ?, 'unchanged', 1)", (str(i), label))
        db.execute("INSERT INTO barcodes (code, product_id, version) VALUES ('123', '0', 1)")
        db.execute("INSERT INTO purchase_events (id, product_id, timestamp, barcode, location_id, quantity, content_unit_label, user_id, source) VALUES ('purchase', '0', 1, '123', 'loc', 2, 'audit label', 'user', 'ANDROID')")
    _migrate(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "quantity_unit" not in {r[1] for r in db.execute("PRAGMA table_info(products)")}
        rows = db.execute("SELECT p.id, u.id, u.abbreviation, u.is_standard FROM products p LEFT JOIN units u ON p.unit_id=u.id ORDER BY p.id").fetchall()
        assert rows[0][1] == rows[1][1]
        assert rows[0][2:] == ("g", 1)
        assert rows[2][2:] == ("kg", 1)
        assert rows[3][1] == rows[4][1]
        assert rows[3][2:] == ("Dose", 0)
        assert rows[5][1] is None
        assert rows[6][2:] == ("", 0)
        assert all(UUID(r[0]).version == 7 for r in db.execute("SELECT id FROM units"))
        assert db.execute("SELECT content_unit_label FROM purchase_events").fetchone()[0] == "audit label"
        assert db.execute("SELECT DISTINCT quantity, content_unit_label FROM products").fetchall() == [(400, "unchanged")]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM units WHERE abbreviation='g'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO units (id, name, abbreviation, is_standard, created_at) VALUES ('duplicate', 'Duplicate', 'G', 0, '2026-09-27')")
        for table in ("stores", "locations", "devices", "barcodes", "purchase_events"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def test_0006_empty_products_and_downgrade(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "empty-units.db")
    _migrate(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM units WHERE is_standard=1").fetchone()[0] == 12
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0005_product_history"],
        cwd=ADDON_ROOT, env={**_inherited_env(), "INVENTRA_DB_PATH": db_path}, check=True,
    )
    _migrate(db_path)


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
        from sqlalchemy import text
        db.execute(text("INSERT INTO products (id, name, version, deleted_at) VALUES (:id, :name, :version, :deleted_at)"),
                   {"id": "deleted", "name": "Historical milk", "version": 2, "deleted_at": datetime(2026, 9, 26, 10)})
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
        assert [r.change_kind for r in rows] == ["DELETE", "DELETE", "UPDATE", "UPDATE", "UPDATE"]
        assert json.loads(rows[2].snapshot)["deletedAt"] == "2026-09-26T10:00:00"
        assert json.loads(rows[3].snapshot)["quantity"] == 4
        assert rows[2].revision == rows[3].revision > rows[1].revision
        assert rows[4].entity_type == "Product"
        assert rows[4].revision > rows[3].revision
        assert json.loads(rows[4].snapshot)["deletedAt"] == "2026-09-26T10:00:00"
    _migrate(db_path)
    with Session(engine) as db:
        assert len(db.scalars(select(ChangeLog)).all()) == 5
    engine.dispose()
