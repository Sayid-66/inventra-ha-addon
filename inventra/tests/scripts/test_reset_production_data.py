"""Exercise the standalone admin command against real SQLite files."""
import json
from datetime import datetime
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine

from inventra_backend.db.base import Base
from inventra_backend.db import models as m


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/reset_production_data.py"
KEEP_DEVICE = "01a0dd7c-fc9b-7dd5-951c-3a8695801443"
REMOVE_LOCATIONS = (
    "01a07313-9cc6-7337-8feb-ca869b408ce3",
    "01a07107-8803-7073-9ced-53e159c1ed73",
)
COUNTS = {
    "products": 20, "barcodes": 9, "batches": 16, "purchase_events": 26,
    "consumption_events": 30, "correction_events": 5, "relocation_events": 1,
    "mhd_warning_ack_state": 1, "resolver_source_cache": 68,
    "resolution_results": 58, "processed_operations": 151, "bring_watch_state": 2,
}


def seed_database(path):
    """Synthetic production-sized fixture; contains actual child foreign keys."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    now = datetime(2026, 1, 1)
    with engine.begin() as db:
        db.execute(m.RevisionCounter.__table__.insert(), {"id": 0, "current_revision": 93})
        db.execute(m.Location.__table__.insert(), [
            {"id": id_, "name": name, "normalized_name": name.lower()}
            for id_, name in [("real-kitchen", "Kueche"),
                              (REMOVE_LOCATIONS[0], "Test A"), (REMOVE_LOCATIONS[1], "Test B")]
        ])
        db.execute(m.Store.__table__.insert(), [
            {"id": name, "name": name, "normalized_name": name.lower()} for name in ("Aldi", "REWE")
        ])
        db.execute(m.Device.__table__.insert(), [
            {"device_id": KEEP_DEVICE if i == 0 else f"device-{i}", "user_id": "user",
             "device_name": "TestPhone-PostFix" if i == 0 else "Test",
             "token_hash": str(i), "default_location_id": "real-kitchen" if i == 0 else REMOVE_LOCATIONS[0]}
            for i in range(30)
        ])
        db.execute(m.PairingCode.__table__.insert(), {"code": "keep-code", "user_id": "user", "expires_at": now})
        db.execute(m.Product.__table__.insert(), [{"id": f"p{i}", "name": f"Product {i}"} for i in range(20)])
        event = {"product_id": "p0", "timestamp": 1, "location_id": REMOVE_LOCATIONS[0],
                 "user_id": "user", "source": "ANDROID"}
        for model, count, extra in (
            (m.PurchaseEvent, 26, {"barcode": "code", "quantity": 1, "store_id": "Aldi"}),
            (m.ConsumptionEvent, 30, {"stock_kind": "UNIT", "quantity": 1}),
            (m.CorrectionEvent, 5, {"stock_kind": "UNIT", "old_quantity": 1, "new_quantity": 2}),
        ):
            db.execute(model.__table__.insert(), [{**event, **extra, "id": f"e{i}"} for i in range(count)])
        db.execute(m.RelocationEvent.__table__.insert(), {
            "id": "r0", "product_id": "p0", "timestamp": 1, "from_location_id": REMOVE_LOCATIONS[0],
            "to_location_id": REMOVE_LOCATIONS[1], "stock_kind": "UNIT", "quantity": 1,
            "user_id": "user", "source": "ANDROID",
        })
        db.execute(m.Batch.__table__.insert(), [
            {"id": f"b{i}", "product_id": "p0", "purchase_event_id": "e0", "correction_event_id": "e0",
             "location_id": REMOVE_LOCATIONS[0], "event_timestamp": 1,
             "is_content_tracked": False, "remaining_quantity": 1} for i in range(16)
        ])
        db.execute(m.Barcode.__table__.insert(), [{"code": f"code{i}", "product_id": "p0"} for i in range(9)])
        db.execute(m.MhdWarningAckState.__table__.insert(), {"id": 0})
        db.execute(m.ResolverSourceCache.__table__.insert(), [
            {"barcode": str(i), "source": "TEST", "status": "FOUND", "fetched_at": now, "expires_at": now}
            for i in range(68)
        ])
        db.execute(m.ResolutionResult.__table__.insert(), [
            {"resolution_id": str(i), "barcode": str(i), "proposed_fields_json": "{}", "created_at": now, "expires_at": now}
            for i in range(58)
        ])
        db.execute(m.ProcessedOperation.__table__.insert(), [
            {"operation_id": str(i), "payload_hash": str(i), "result_snapshot": "{}"} for i in range(151)
        ])
        db.execute(m.BringWatchState.__table__.insert(), [
            {"product_id": f"p{i}", "state": "PENDING_ADD", "origin": "INVENTRA_CREATED", "bring_item_name": "Test"}
            for i in range(2)
        ])
        db.execute(m.ChangeLog.__table__.insert(), [
            {"revision": 93, "entity_type": "Product", "entity_id": "p0", "change_kind": "UPDATE", "snapshot": "{}"}
            for _ in range(206)
        ])
    engine.dispose()


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "synthetic.db"
    seed_database(path)
    return path


def run(path, *args):
    assert SCRIPT.is_file(), "Reset admin script has not been implemented"
    return subprocess.run([sys.executable, str(SCRIPT), str(path), *args], capture_output=True, text=True)


def dump(path):
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


def test_dry_run_and_safe_default_do_not_write(database):
    original = database.read_bytes()
    result = run(database, "--dry-run")
    assert result.returncode == 0, result.stderr
    for table, count in {**COUNTS, "locations": 3, "devices": 30, "stores": 2, "change_log": 206}.items():
        after = {"locations": 1, "devices": 1, "stores": 2, "change_log": 22}.get(table, 0)
        assert f"{table}: {count} -> {after}" in result.stdout
    assert database.read_bytes() == original
    assert run(database).returncode != 0
    assert database.read_bytes() == original
    assert run(database, "--dry-run", "--confirm").returncode != 0
    assert database.read_bytes() == original


def test_confirm_cascades_via_product_tombstones_and_repeat_preserves_them(database):
    with sqlite3.connect(database) as db:
        preserved = {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in ("stores", "pairing_codes")}
    result = run(database, "--confirm")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        for table in COUNTS:
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT id FROM locations").fetchall() == [("real-kitchen",)]
        assert db.execute("SELECT device_id FROM devices").fetchall() == [(KEEP_DEVICE,)]
        for table, rows in preserved.items():
            assert db.execute(f"SELECT * FROM {table}").fetchall() == rows
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT current_revision FROM revision_counter").fetchone() == (94,)
        changes = db.execute("SELECT revision, entity_type, entity_id, change_kind, snapshot FROM change_log").fetchall()
        assert len(changes) == 22
        assert {(row[1], row[2]) for row in changes} == {
            *(("Product", f"p{i}") for i in range(20)), *(("Location", id_) for id_ in REMOVE_LOCATIONS)
        }
        for revision, entity_type, id_, kind, snapshot in changes:
            assert revision == 94 and kind == "DELETE"
            payload = json.loads(snapshot)
            assert payload["id"] == id_ and payload["deletedAt"] and payload["version"] == 2
            assert "name" in payload
        assert "fieldProvenance" in json.loads(next(r[4] for r in changes if r[1] == "Product"))
    original = dump(database)
    assert run(database, "--confirm").returncode == 0
    assert dump(database) == original


@pytest.mark.parametrize("mode", ["--dry-run", "--confirm"])
@pytest.mark.parametrize("damage", ["missing-device", "ambiguous-device", "bad-counter", "kept-device-location", "fk-violation", "late-error"])
def test_abort_leaves_entire_database_unchanged(database, mode, damage):
    with sqlite3.connect(database) as db:
        if damage == "missing-device":
            db.execute("DELETE FROM devices WHERE device_id = ?", (KEEP_DEVICE,))
        elif damage == "ambiguous-device":
            db.execute("INSERT INTO devices (device_id,user_id,device_name,token_hash,created_at) VALUES (?, 'u', 'TestPhone-PostFix', 'ambiguous', '2026-01-01')", (KEEP_DEVICE + "extra",))
        elif damage == "bad-counter":
            db.execute("UPDATE revision_counter SET current_revision = 92")
        elif damage == "kept-device-location":
            db.execute("UPDATE devices SET default_location_id = ? WHERE device_id = ?", (REMOVE_LOCATIONS[0], KEEP_DEVICE))
        elif damage == "fk-violation":
            db.execute("UPDATE devices SET default_location_id = 'missing' WHERE device_id = ?", (KEEP_DEVICE,))
        else:
            # Abort after destructive statements when the new ChangeSet is flushed.
            db.execute("CREATE TRIGGER fail_tombstone BEFORE INSERT ON change_log BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    original = dump(database)
    result = run(database, mode)
    if damage == "late-error" and mode == "--dry-run":
        assert result.returncode == 0, result.stderr  # a read-only plan cannot execute triggers
    else:
        assert result.returncode != 0
        assert "ABORT" in result.stderr
    assert dump(database) == original


def test_missing_file_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    assert run(path, "--confirm").returncode != 0
    assert not path.exists()


def test_other_locations_are_kept(database):
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO locations (id,name,normalized_name,version) VALUES ('extra','Extra','extra',1)")
    assert run(database, "--confirm").returncode == 0
    with sqlite3.connect(database) as db:
        assert {r[0] for r in db.execute("SELECT id FROM locations")} == {"real-kitchen", "extra"}


def test_full_device_id_and_counter_ahead_of_log(database):
    from sqlalchemy.orm import Session
    from inventra_backend.services.sync_service import fetch_sync_page

    full_id = KEEP_DEVICE + "abcd"
    with sqlite3.connect(database) as db:
        db.execute("UPDATE devices SET device_id = ? WHERE device_id = ?", (full_id, KEEP_DEVICE))
        db.execute("UPDATE revision_counter SET current_revision = 120")
    result = run(database, "--confirm")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT device_id FROM devices").fetchall() == [(full_id,)]
        assert db.execute("SELECT current_revision FROM revision_counter").fetchone() == (121,)
    engine = create_engine(f"sqlite:///{database}")
    try:
        with Session(engine) as db:
            page = fetch_sync_page(db, since_revision=120, limit=5)
            assert len(page["changes"]) == 22  # the shared revision must not be split
            assert page["nextRevision"] == 121 and not page["hasMore"]
            assert all(change["changeKind"] == "DELETE" for change in page["changes"])
    finally:
        engine.dispose()


def test_dry_run_reads_live_wal_snapshot_without_changing_rows(database):
    with sqlite3.connect(database) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("UPDATE products SET name = 'Committed in WAL' WHERE id = 'p0'")
        live.commit()
        original = dump(database)
        result = run(database, "--dry-run")
        assert result.returncode == 0, result.stderr
        assert "products: 20 -> 0" in result.stdout
        assert dump(database) == original
