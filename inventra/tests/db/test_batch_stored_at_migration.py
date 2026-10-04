import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from inventra_backend.db.models import Batch
from inventra_backend.services.inventory_service import _batch_to_dict
from inventra_backend.services.snapshot_service import fetch_snapshot_page
from inventra_backend.services.sync_service import fetch_sync_page


def migrate(path, direction, revision):
    subprocess.run([sys.executable, "-m", "alembic", direction, revision],
                   cwd=Path(__file__).resolve().parents[2],
                   env={**os.environ, "INVENTRA_DB_PATH": str(path)}, check=True, timeout=30)


@pytest.mark.parametrize("counter", [3, 20])
@pytest.mark.parametrize("populated", [False, True])
def test_migration_backfills_and_resyncs_one_revision(tmp_path, counter, populated):
    path = tmp_path / "stored.db"
    migrate(path, "upgrade", "0011_instance_identity")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as db:
        db.execute(text("UPDATE revision_counter SET current_revision=:counter WHERE id=0"), {"counter": counter})
        db.execute(text("INSERT INTO change_log (revision, entity_type, entity_id, change_kind, snapshot, created_at) VALUES (7, 'Product', 'p', 'CREATE', '{}', '2026-01-01')"))
        if populated:
            db.execute(text("INSERT INTO products (id, name, version) VALUES ('p', 'Milk', 1), ('other', 'Other', 1)"))
            db.execute(text("INSERT INTO locations (id, name, normalized_name, version) VALUES ('f', 'Freezer', 'freezer', 1), ('v', 'Pantry', 'pantry', 1)"))
            db.execute(text("INSERT INTO batches (id, product_id, location_id, event_timestamp, is_content_tracked, content_unit_label, remaining_quantity, mhd) VALUES ('a', 'p', 'f', 1000, 1, 'g', 300, '2027-01-01'), ('b', 'p', 'v', 4000, 0, NULL, 2, NULL), ('c', 'p', 'f', 6000, 0, NULL, 0, NULL)"))
            for i, product, destination, timestamp in [(1, 'p', 'f', 500), (2, 'p', 'f', 2000), (3, 'p', 'f', 3000), (4, 'other', 'f', 9000), (5, 'p', 'v', 3500)]:
                db.execute(text("INSERT INTO relocation_events (id, product_id, timestamp, from_location_id, to_location_id, stock_kind, quantity, user_id, source) VALUES (:id, :product, :timestamp, 'v', :destination, 'CONTENT', 1, 'u', 'ANDROID')"),
                           {"id": str(i), "product": product, "timestamp": timestamp, "destination": destination})
    migrate(path, "upgrade", "head")
    assert "stored_at" in {c["name"] for c in inspect(engine).get_columns("batches")}
    expected_revision = max(counter, 7) + 1 if populated else counter
    with engine.connect() as db:
        assert db.execute(text("SELECT current_revision FROM revision_counter WHERE id=0")).scalar_one() == expected_revision
        rows = db.execute(text("SELECT * FROM change_log WHERE entity_type='Batch'")).mappings().all()
        assert len(rows) == (3 if populated else 0)
        assert {row["revision"] for row in rows} == ({expected_revision} if populated else set())
        assert all(row["change_kind"] == "UPDATE" for row in rows)
    if populated:
        with Session(engine) as db:
            batches = {b.id: b for b in db.query(Batch).all()}
            assert {key: b.stored_at for key, b in batches.items()} == {"a": 3000, "b": 4000, "c": 6000}
            for row in rows:
                snapshot = json.loads(row["snapshot"])
                assert snapshot == _batch_to_dict(batches[row["entity_id"]])
                assert type(snapshot["isContentTracked"]) is bool
            snapshot = fetch_snapshot_page(db, None, None, None)
            delta = fetch_sync_page(db, max(counter, 7))
            for entities in (snapshot["entities"], delta["changes"]):
                assert {e["entityId"]: e["snapshot"]["storedAt"] for e in entities if e["entityType"] == "Batch"} == {"a": 3000, "b": 4000, "c": 6000}
    # Repeated upgrade head must not allocate another revision or log duplicates.
    migrate(path, "upgrade", "head")
    with engine.connect() as db:
        assert db.execute(text("SELECT COUNT(*) FROM change_log")).scalar_one() == (4 if populated else 1)
    migrate(path, "downgrade", "0011_instance_identity")
    assert "stored_at" not in {c["name"] for c in inspect(engine).get_columns("batches")}
    engine.dispose()


@pytest.mark.parametrize("origin_event", ["purchase", "correction", None])
@pytest.mark.parametrize("content_tracked", [False, True])
@pytest.mark.parametrize("scenario, expected", [
    ("direct", 1000),
    ("relocated", 2000),
    ("freezer_transfer", 2000),
    ("freezer_transfer_only", 1000),
    ("stock_mismatch", 1000),
])
def test_backfill_respects_origin_freezer_transfers_and_stock_kind(
    tmp_path, origin_event, content_tracked, scenario, expected,
):
    if origin_event is None and scenario == "direct":
        expected = 2000  # Unknown origins use the matching relocation fallback.
    path = tmp_path / "backfill.db"
    migrate(path, "upgrade", "0011_instance_identity")
    engine = create_engine(f"sqlite:///{path}")
    stock_kind = "CONTENT" if content_tracked else "STK"
    with engine.begin() as db:
        db.execute(text("INSERT INTO products (id, name, version) VALUES ('p', 'Milk', 1)"))
        # Soft-deleted freezer names must still participate in classification.
        db.execute(text("""
            INSERT INTO locations (id, name, normalized_name, version, deleted_at)
            VALUES ('f', 'TK', 'tk', 1, NULL), ('v', 'Pantry', 'pantry', 1, NULL),
                   ('old', 'Gefriertruhe', 'gefriertruhe', 1, '2026-01-01')
        """))
        origin = "f" if scenario == "direct" else "v"
        if origin_event == "purchase":
            db.execute(text("""
                INSERT INTO purchase_events
                    (id, product_id, timestamp, barcode, location_id, quantity, user_id, source)
                VALUES ('origin', 'p', 1000, '123', :origin, 1, 'u', 'ANDROID')
            """), {"origin": origin})
        elif origin_event == "correction":
            db.execute(text("""
                INSERT INTO correction_events
                    (id, product_id, timestamp, location_id, stock_kind,
                     old_quantity, new_quantity, user_id, source)
                VALUES ('origin', 'p', 1000, :origin, :kind, 0, 1, 'u', 'ANDROID')
            """), {"origin": origin, "kind": stock_kind})
        db.execute(text("""
            INSERT INTO batches
                (id, product_id, purchase_event_id, correction_event_id, location_id,
                 event_timestamp, is_content_tracked, remaining_quantity)
            VALUES ('a', 'p', :purchase, :correction, 'f', 1000, :tracked, 1)
        """), {"purchase": "origin" if origin_event == "purchase" else None,
               "correction": "origin" if origin_event == "correction" else None,
               "tracked": content_tracked})
        events = []
        if scenario in ("direct", "relocated", "freezer_transfer"):
            events.append(("earlier", "v", 2000, stock_kind))
        if scenario in ("freezer_transfer", "freezer_transfer_only"):
            events.append(("later", "old", 3000, stock_kind))
        if scenario == "stock_mismatch":
            events.append(("wrong", "v", 3000, "STK" if content_tracked else "CONTENT"))
        for event_id, source, timestamp, kind in events:
            db.execute(text("""
                INSERT INTO relocation_events
                    (id, product_id, timestamp, from_location_id, to_location_id,
                     stock_kind, quantity, user_id, source)
                VALUES (:id, 'p', :timestamp, :source, 'f', :kind, 1, 'u', 'ANDROID')
            """), {"id": event_id, "timestamp": timestamp, "source": source, "kind": kind})
    migrate(path, "upgrade", "head")
    with engine.connect() as db:
        assert db.execute(text("SELECT stored_at FROM batches WHERE id='a'")).scalar_one() == expected
        rows = db.execute(text("SELECT * FROM change_log WHERE entity_type='Batch'")).mappings().all()
        assert len(rows) == 1
        assert rows[0]["change_kind"] == "UPDATE"
        assert json.loads(rows[0]["snapshot"])["storedAt"] == expected
        assert db.execute(text("SELECT current_revision FROM revision_counter WHERE id=0")).scalar_one() == rows[0]["revision"]
    engine.dispose()
