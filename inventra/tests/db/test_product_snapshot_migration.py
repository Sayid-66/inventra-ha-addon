import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from inventra_backend.db.models import ChangeKind, ChangeLog, Product, RevisionCounter
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services import product_service


def _upgrade(db_path, revision):
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "INVENTRA_DB_PATH": str(db_path)},
        check=True,
    )


def test_0007_refreshes_all_products_and_preserves_revision_allocator(tmp_path):
    db_path = tmp_path / "snapshots.db"
    _upgrade(db_path, "0006_units_catalog")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as db:
        unit_id = db.execute(text("SELECT id FROM units WHERE abbreviation='g'")).scalar_one()
        for product_id, deleted_at, product_unit in (
            ("active", None, unit_id),
            ("deleted", "2026-09-26 10:00:00.123456", unit_id),
            ("no-unit", None, None),
        ):
            db.execute(text("""
                INSERT INTO products (id, name, image_url, min_stock, content_unit_label,
                    brand, quantity, unit_id, category, variant, field_provenance, version, deleted_at)
                VALUES (:id, 'Milk', '/milk.png', 2, 'bottle', 'Brand', 400, :unit,
                    'Dairy', 'Whole', :provenance, 3, :deleted_at)
            """), {"id": product_id, "unit": product_unit, "deleted_at": deleted_at,
                   "provenance": json.dumps({"name": "manual"})})
            db.execute(text("""
                INSERT INTO change_log (revision, entity_type, entity_id, change_kind, snapshot, created_at)
                VALUES (7, 'Product', :id, 'UPDATE', :snapshot, '2026-09-26')
            """), {"id": product_id, "snapshot": json.dumps({"id": product_id, "quantity": 400})})
        db.execute(text("UPDATE revision_counter SET current_revision=7 WHERE id=0"))

    _upgrade(db_path, "head")
    with Session(engine) as db:
        rows = db.scalars(select(ChangeLog).where(ChangeLog.revision == 8)).all()
        assert len(rows) == 3
        assert db.get(RevisionCounter, 0).current_revision == 8
        for row in rows:
            snapshot = json.loads(row.snapshot)
            expected = product_service._to_dict(db.get(Product, row.entity_id))
            assert snapshot.keys() == expected.keys()
            assert snapshot == expected
            assert row.change_kind == ChangeKind.UPDATE.value
            latest = db.scalars(select(ChangeLog).where(
                ChangeLog.entity_id == row.entity_id
            ).order_by(ChangeLog.revision.desc())).first()
            assert latest.id == row.id
        assert json.loads(next(r.snapshot for r in rows if r.entity_id == "active"))["unit"] == {
            "id": unit_id, "name": "Gramm", "abbreviation": "g",
        }
        cs = ChangeSet(db)
        product_service.update_product(db, cs, "later-write", "active", 3, name="Updated milk")
        db.commit()
        assert cs.revision == 9
        assert db.get(RevisionCounter, 0).current_revision == 9
    _upgrade(db_path, "head")
    with Session(engine) as db:
        assert len(db.scalars(select(ChangeLog)).all()) == 7
    engine.dispose()


def test_0007_empty_products_does_not_allocate_revision(tmp_path):
    db_path = tmp_path / "empty.db"
    _upgrade(db_path, "0006_units_catalog")
    _upgrade(db_path, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as db:
        assert db.get(RevisionCounter, 0).current_revision == 0
        assert db.scalars(select(ChangeLog)).all() == []
    engine.dispose()
