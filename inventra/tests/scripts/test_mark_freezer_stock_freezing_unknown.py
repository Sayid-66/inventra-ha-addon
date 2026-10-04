import importlib.util
from datetime import datetime
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from inventra_backend.db.base import Base
from inventra_backend.db import models as m
from inventra_backend.services.sync_service import fetch_sync_page

spec = importlib.util.spec_from_file_location(
    "mark_freezing_unknown", Path(__file__).resolve().parents[2] / "scripts/mark_freezer_stock_freezing_unknown.py")
marker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(marker)
CUTOFF = datetime.fromisoformat("2026-10-05T00:00:00+00:00")
CUTOFF_MS = 1791158400000


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "stock.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(m.RevisionCounter(id=0, current_revision=0))
        db.add(m.Product(id="p", name="K\u00e4se"))
        db.add_all([m.Location(id="f", name="Tiefk\u00fchltruhe", normalized_name="freezer"),
                    m.Location(id="k", name="K\u00fchlschrank", normalized_name="fridge")])
        db.flush()
        for id_, location, quantity, timestamp, stored_at in [
            ("match1", "f", 2, CUTOFF_MS - 1, CUTOFF_MS - 1),
            ("match2", "f", 3, 1000, 2000),
            ("fridge", "k", 1, 1000, 1000),
            ("frozen_later", "f", 1, 1000, CUTOFF_MS + 1),
            ("frozen_boundary", "f", 1, 1000, CUTOFF_MS),
            ("boundary", "f", 1, CUTOFF_MS, 1000),
            ("later", "f", 1, CUTOFF_MS + 1, 1000),
            ("unknown", "f", 1, 1000, None),
        ]:
            db.add(m.Batch(id=id_, product_id="p", location_id=location,
                           event_timestamp=timestamp, stored_at=stored_at,
                           remaining_quantity=quantity, is_content_tracked=False))
        db.commit()
    engine.dispose()
    return path


def stored_rows(path):
    with sqlite3.connect(path) as db:
        return dict(db.execute("SELECT id, stored_at FROM batches"))


def test_dry_run_is_read_only_and_reports_unicode(database):
    before = database.read_bytes()
    report = marker.mark_database(database, entered_before=CUTOFF, dry_run=True)
    assert database.read_bytes() == before
    assert "K\u00e4se" in report and "Tiefk\u00fchltruhe" in report
    assert "Matching batches: 2; would change: 2" in report
    assert "match1 |" in report and "match2 |" in report
    assert "| relocated" in report
    assert f"{CUTOFF_MS - 1} | {CUTOFF_MS - 1} | False" in report
    assert "1000 | 2000 | True" in report
    assert "frozen_later |" not in report and "frozen_boundary |" not in report


def test_confirm_selection_sync_and_repeat_noop(database, monkeypatch):
    before = stored_rows(database)
    report = marker.mark_database(database, entered_before=CUTOFF, confirm=True)
    assert "changed: 2" in report
    after = stored_rows(database)
    assert after == {key: None if key in {"match1", "match2"} else value for key, value in before.items()}
    # No instance identity needs to be written to this deliberately minimal fixture.
    monkeypatch.setattr("inventra_backend.services.sync_service.get_instance_id", lambda db: "test")
    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as db:
        changes = fetch_sync_page(db, 0)["changes"]
        assert {row["entityId"] for row in changes} == {"match1", "match2"}
        assert len(changes) == 2
        assert {row["revision"] for row in changes} == {1}
        assert all(row["entityType"] == "Batch" and row["changeKind"] == "UPDATE"
                   and row["snapshot"]["storedAt"] is None for row in changes)
    engine.dispose()
    before_repeat = database.read_bytes()
    assert marker.main([str(database), "--confirm", "--entered-before", CUTOFF.isoformat()]) == 0
    assert database.read_bytes() == before_repeat
    assert "nothing to do" in marker.mark_database(database, entered_before=CUTOFF, dry_run=True)
    assert "nothing to do" in marker.mark_database(database, entered_before=CUTOFF, confirm=True)


@pytest.mark.parametrize("cutoff_args", [[], ["--entered-before", "bad"],
                                        ["--entered-before", "2026-10-05T00:00:00"]])
def test_cli_rejects_missing_invalid_or_naive_cutoff(database, cutoff_args):
    before = database.read_bytes()
    with pytest.raises(SystemExit) as exc:
        marker.main([str(database), "--confirm", *cutoff_args])
    assert exc.value.code == 2
    assert database.read_bytes() == before


def test_foreign_key_failure_rolls_back(database):
    with sqlite3.connect(database) as db:
        db.execute("UPDATE batches SET product_id='missing' WHERE id='fridge'")
    before = stored_rows(database)
    with pytest.raises(ValueError, match="foreign_key_check"):
        marker.mark_database(database, entered_before=CUTOFF, confirm=True)
    assert stored_rows(database) == before
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT current_revision FROM revision_counter").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM change_log").fetchone() == (0,)


def test_submillisecond_cutoff_applies_equally_to_entry_and_freezing(database):
    cutoff = datetime.fromisoformat("2026-10-05T00:00:00.000001+00:00")
    before = stored_rows(database)
    report = marker.mark_database(database, entered_before=cutoff, confirm=True)
    assert "changed: 4" in report
    after = stored_rows(database)
    matches = {"match1", "match2", "boundary", "frozen_boundary"}
    assert after == {key: None if key in matches else value for key, value in before.items()}
