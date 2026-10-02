import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text


def upgrade(path, revision):
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision],
                   cwd=Path(__file__).resolve().parents[2],
                   env={**os.environ, "INVENTRA_DB_PATH": str(path)}, check=True, timeout=30)


@pytest.mark.parametrize("counter", [7, 20])
@pytest.mark.parametrize("repair", [False, True])
def test_0009_repairs_in_one_revision_or_allocates_none(tmp_path, repair, counter):
    path = tmp_path / "repair.db"
    upgrade(path, "0008_free_deleted_master_names")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as db:
        db.execute(text("INSERT INTO products (id, name, version, deleted_at) VALUES ('old', 'Old', 2, '2026-09-01'), ('live', 'Live', 1, NULL)"))
        db.execute(text("INSERT INTO barcodes (code, product_id, version, deleted_at) VALUES ('live', 'live', 1, NULL), ('gone', 'old', 4, '2026-09-01')"))
        if repair:
            db.execute(text("INSERT INTO barcodes (code, product_id, version) VALUES ('one', 'old', 3), ('two', 'old', 1)"))
        db.execute(text("UPDATE revision_counter SET current_revision = :counter WHERE id = 0"), {"counter": counter})
        db.execute(text("INSERT INTO change_log (revision, entity_type, entity_id, change_kind, snapshot, created_at) VALUES (7, 'Product', 'live', 'CREATE', '{}', '2026-09-01')"))
    upgrade(path, "head")
    with engine.connect() as db:
        rows = db.execute(text("SELECT * FROM change_log WHERE revision > 7")).mappings().all()
        assert len(rows) == (2 if repair else 0)
        assert db.execute(text("SELECT current_revision FROM revision_counter WHERE id = 0")).scalar_one() == (counter + 1 if repair else counter)
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0011_instance_identity"
        for row in rows:
            assert row['revision'] == counter + 1
            assert row['entity_type'] == 'Barcode'
            assert row['change_kind'] == 'DELETE'
            barcode = db.execute(text("SELECT * FROM barcodes WHERE code = :code"), {"code": row['entity_id']}).mappings().one()
            assert barcode['version'] == (4 if barcode['code'] == 'one' else 2)
            assert barcode['deleted_at'] is not None
            from datetime import datetime
            assert json.loads(row['snapshot']) == {'code': barcode['code'], 'productId': 'old',
                'version': barcode['version'], 'deletedAt': datetime.fromisoformat(barcode['deleted_at']).isoformat()}
        assert db.execute(text("SELECT version FROM barcodes WHERE code = 'gone'")).scalar_one() == 4
        assert db.execute(text("SELECT deleted_at FROM barcodes WHERE code = 'live'")).scalar_one() is None
    # Exercise the repair again even though Alembic normally runs it only once.
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location('repair', Path(__file__).resolve().parents[2] / 'migrations/versions/0009_release_barcodes_of_deleted_products.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            module.upgrade()
        assert db.execute(text("SELECT COUNT(*) FROM change_log")).scalar_one() == (3 if repair else 1)
        assert db.execute(text("SELECT current_revision FROM revision_counter WHERE id = 0")).scalar_one() == (counter + 1 if repair else counter)
    engine.dispose()
