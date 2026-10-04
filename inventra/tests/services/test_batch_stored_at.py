import json

import pytest
from sqlalchemy import select

from inventra_backend.db.models import Batch, ChangeLog, Location, Product
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services.inventory_service import commit_purchase, correct_stock, relocate
from inventra_backend.services.location_kind import is_freezer_location_name


@pytest.mark.parametrize("name, expected", [
    ("Gefrierfach", True), ("Tiefkühltruhe", True), ("TK", True),
    ("Gefrierfach im Kühlschrank", True), ("Froster", True),
    ("Kühlschrank", False), ("Vorrat", False), ("Keller", False), ("Tkanyok", False),
    ("Tiefkuhl", True), ("Vorrat-TK 2", True), ("TKé", False),
])
def test_is_freezer_location_name(name, expected):
    assert is_freezer_location_name(name) is expected


@pytest.fixture
def stock(db_session):
    db = db_session
    db.add(Product(id="p", name="Milk"))
    for key, name in [("v", "Vorrat"), ("f", "Gefrierfach"), ("k", "Kühlschrank"), ("t", "Tiefkühltruhe")]:
        db.add(Location(id=key, name=name, normalized_name=name.lower()))
    db.commit()
    return db


def purchase(db, location="v", quantity=3):
    commit_purchase(db, ChangeSet(db), "purchase-op", "purchase", "p", None, "123", location,
                    quantity, None, None, None, None, None, None, None, 1000, "u", None, "ANDROID")
    db.flush()


def move(db, source, destination, timestamp, quantity=3):
    relocate(db, ChangeSet(db), f"op-{timestamp}", f"event-{timestamp}", "p", source,
             destination, "STK", quantity, timestamp, "u", None, "ANDROID")
    db.flush()
    return db.scalar(select(Batch).where(Batch.location_id == destination))


def assert_logged(db, batch, kind):
    row = db.scalars(select(ChangeLog).where(ChangeLog.entity_id == batch.id).order_by(ChangeLog.id.desc())).first()
    assert row.change_kind == kind
    assert json.loads(row.snapshot)["storedAt"] == batch.stored_at


def test_purchase_stored_at_and_change_log(stock):
    purchase(stock)
    batch = stock.scalar(select(Batch))
    assert batch.stored_at == batch.event_timestamp == 1000
    assert_logged(stock, batch, "CREATE")
    from inventra_backend.services.snapshot_service import fetch_snapshot_page
    from inventra_backend.services.sync_service import fetch_sync_page
    snapshot = fetch_snapshot_page(stock, None, None, None)
    delta = fetch_sync_page(stock, 0)
    for entities in (snapshot["entities"], delta["changes"]):
        assert next(e["snapshot"]["storedAt"] for e in entities if e["entityType"] == "Batch") == 1000


def test_correction_increase_stored_at(stock):
    correct_stock(stock, ChangeSet(stock), "op", "correction", "p", "v", "STK", None,
                  3, None, 2000, "u", None, "ANDROID")
    stock.flush()
    batch = stock.scalar(select(Batch))
    assert batch.stored_at == batch.event_timestamp == 2000
    assert_logged(stock, batch, "CREATE")


def test_relocation_into_freezer_and_refreezing(stock):
    purchase(stock)
    batch = move(stock, "v", "f", 2000)
    assert batch.stored_at == 2000
    assert batch.event_timestamp == 1000
    assert_logged(stock, batch, "CREATE")
    assert move(stock, "f", "k", 3000).stored_at == 3000
    batch = move(stock, "k", "f", 4000)
    assert batch.stored_at == 4000
    assert batch.event_timestamp == 1000


@pytest.mark.parametrize("initial", [1000, None])
def test_freezer_to_freezer_preserves_known_date(stock, initial):
    purchase(stock, "f")
    stock.scalar(select(Batch)).stored_at = initial
    batch = move(stock, "f", "t", 2000)
    assert batch.stored_at == (initial if initial is not None else 2000)
    assert batch.event_timestamp == 1000
    assert_logged(stock, batch, "CREATE")


@pytest.mark.parametrize("existing_date", [1500, 5000, None])
def test_relocation_merge_keeps_oldest_non_null_date(stock, existing_date):
    purchase(stock)
    destination = move(stock, "v", "f", 2000, quantity=1)
    destination.stored_at = existing_date
    batch = move(stock, "v", "f", 3000, quantity=2)
    assert batch.id == destination.id
    assert batch.remaining_quantity == 3
    assert batch.stored_at == (min(existing_date, 3000) if existing_date is not None else 3000)
    assert_logged(stock, batch, "UPDATE")
