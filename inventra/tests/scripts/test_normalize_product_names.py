import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine
from inventra_backend.db.base import Base
from inventra_backend.db import models as m

spec = importlib.util.spec_from_file_location("normalize_names", Path(__file__).resolve().parents[2] / "scripts/normalize_product_names.py")
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)

NAMES = [
    ("Gyros Geschnetzeltes Gyros", "Gut Ponholz", 400, "Gyros Geschnetzeltes 400 g"),
    ("H\u00e4hnchengeschnetzeltes", "Aldi", 400, "H\u00e4hnchengeschnetzeltes 400 g"),
    ("Hackfleisch", None, 500, "Hackfleisch 500 g"),
    ("H\u00e4hnchen Geschnetzeltes", "Gut Ponholz", 400, "H\u00e4hnchen Geschnetzeltes 400 g"),
    ("Pazifischer Wildlachs", "Ocean Sea", 250, "Pazifischer Wildlachs 250 g"),
]


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "names.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(m.RevisionCounter.__table__.insert(), {"id": 0, "current_revision": 0})
        db.execute(m.Unit.__table__.insert(), {"id": "g", "name": "Gramm", "abbreviation": "g", "is_standard": True})
        for i, (name, brand, amount, _) in enumerate(NAMES):
            db.execute(m.Product.__table__.insert(), {"id": str(i), "name": name,
                "brand": brand, "quantity": amount, "unit_id": "g", "category": "keep",
                "variant": "keep", "image_url": "https://example.org/image", "min_stock": 3,
                "field_provenance": json.dumps({"name": {"manual": i == 0}}), "version": 4})
        db.execute(m.Product.__table__.insert(), {"id": "deleted", "name": "Deleted", "deleted_at": __import__('datetime').datetime(2026, 1, 1)})
        db.execute(m.Product.__table__.insert(), {"id": "bad", "name": "Frisch vom Schwein"})
    engine.dispose()
    return path


def products(path):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return {row['id']: dict(row) for row in db.execute("SELECT * FROM products")}


def test_dry_run_strictly_read_only(database):
    before = database.read_bytes()
    report = normalizer.normalize_database(database, dry_run=True)
    assert database.read_bytes() == before
    assert not (database.parent / "backups").exists()
    assert "skipped: manual" in report
    assert "skipped: unusable" in report
    assert "would update" in report


def test_confirm_backup_sync_and_idempotence(database):
    before = products(database)
    report = normalizer.normalize_database(database, confirm=True)
    assert "skipped: manual" in report
    backups = list((database.parent / "backups").glob("inventra-pre-namefix-*.db"))
    assert len(backups) == 1
    assert products(backups[0]) == before
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    after = products(database)
    for i, (_, _, _, expected) in enumerate(NAMES):
        row = after[str(i)]
        assert row['name'] == (before[str(i)]['name'] if i == 0 else expected)
        assert row['version'] == (4 if i == 0 else 5)
        assert {k: v for k, v in row.items() if k not in ('name', 'version')} == {
            k: v for k, v in before[str(i)].items() if k not in ('name', 'version')}
    assert after['bad'] == before['bad']
    assert after['deleted'] == before['deleted']
    with sqlite3.connect(database) as db:
        changes = db.execute("SELECT entity_type,change_kind,snapshot FROM change_log").fetchall()
        assert len(changes) == 4
        assert all(entity == 'Product' and kind == 'UPDATE' and json.loads(snapshot)['version'] == 5
                   for entity, kind, snapshot in changes)
        revision = db.execute("SELECT current_revision FROM revision_counter").fetchone()
    normalizer.normalize_database(database, confirm=True)
    assert products(database) == after
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT current_revision FROM revision_counter").fetchone() == revision
        assert db.execute("SELECT count(*) FROM change_log").fetchone() == (4,)


def test_include_manual(database):
    report = normalizer.normalize_database(database, dry_run=True, include_manual=True)
    assert "skipped: manual" not in report
    normalizer.normalize_database(database, confirm=True, include_manual=True)
    assert products(database)['0']['name'] == NAMES[0][3]
    assert products(database)['0']['version'] == 5
    assert json.loads(products(database)['0']['field_provenance'])['name']['manual'] is True


def test_backup_failure_prevents_updates(database, monkeypatch):
    before = database.read_bytes()
    def fail(*args):
        raise ValueError("Backup integrity_check failed")
    monkeypatch.setattr(normalizer, "_backup", fail)
    with pytest.raises(ValueError, match="integrity_check"):
        normalizer.normalize_database(database, confirm=True)
    assert database.read_bytes() == before


def test_service_failure_rolls_back_all_updates(database, monkeypatch):
    before = products(database)
    original = normalizer.update_product
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("injected service failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(normalizer, "update_product", fail_second)
    with pytest.raises(ValueError, match="injected"):
        normalizer.normalize_database(database, confirm=True)
    assert products(database) == before
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM change_log").fetchone() == (0,)
        assert db.execute("SELECT current_revision FROM revision_counter").fetchone() == (0,)
    assert len(list((database.parent / "backups").glob("*.db"))) == 1



def test_custom_units_second_and_third_run_are_strict_noops(database):
    engine = create_engine(f"sqlite:///{database}")
    units = ["Rolle", "Pkg.", "Beutel", "Sonder-Einheit"]
    with engine.begin() as db:
        for i, unit in enumerate(units):
            db.execute(m.Unit.__table__.insert(), {"id": unit, "name": unit,
                "abbreviation": unit, "is_standard": False})
            db.execute(m.Product.__table__.insert(), {"id": f"custom-{i}",
                "name": "Toilettenpapier" if i == 0 else "Produkt", "quantity": 8,
                "unit_id": unit, "version": 1})
        db.execute(m.Product.__table__.insert(), {"id": "no-size", "name": "Milch 1 l"})
    engine.dispose()
    normalizer.normalize_database(database, confirm=True)
    after = products(database)
    for i, unit in enumerate(units):
        assert after[f"custom-{i}"]["name"] == ("Toilettenpapier" if i == 0 else "Produkt") + f" 8 {unit}"
    assert after["no-size"]["name"] == "Milch 1 l"
    with sqlite3.connect(database) as db:
        revision = db.execute("SELECT current_revision FROM revision_counter").fetchone()
        logs = db.execute("SELECT * FROM change_log ORDER BY revision").fetchall()
    for _ in range(2):
        normalizer.normalize_database(database, confirm=True)
        assert products(database) == after
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT current_revision FROM revision_counter").fetchone() == revision
            assert db.execute("SELECT * FROM change_log ORDER BY revision").fetchall() == logs
