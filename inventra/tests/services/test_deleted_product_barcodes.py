import asyncio
import json
from datetime import datetime

import pytest
from sqlalchemy import select

from inventra_backend.db.models import Barcode, ChangeLog, Location, Product, RevisionCounter
from inventra_backend.errors import BusinessRuleViolation
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.resolver import resolver_service
from inventra_backend.resolver.source_client import SourceResult
from inventra_backend.resolver.sources import ALL_SOURCES
from inventra_backend.services.product_service import soft_delete_product
from inventra_backend.services.inventory_service import commit_purchase
from inventra_backend.services.sync_service import fetch_sync_page


def seed(db):
    db.add_all([Product(id="old", name="Old", version=1),
                Location(id="loc", name="Location", normalized_name="location", version=1)])
    db.flush()
    db.add_all([Barcode(code="one", product_id="old", version=3),
                Barcode(code="two", product_id="old", version=1),
                Barcode(code="gone", product_id="old", version=5, deleted_at=datetime.utcnow())])
    db.commit()


def purchase(db, new_product):
    return commit_purchase(db, ChangeSet(db), "purchase", "event", "new", new_product,
                           "one", "loc", 1, None, None, None, None, None, None, None,
                           1000, "user", None, "APP")


def test_delete_tombstones_syncs_and_replays_once(db_session):
    db = db_session
    seed(db)
    before = db.get(RevisionCounter, 0).current_revision
    result = soft_delete_product(db, ChangeSet(db), "delete", "old", 1)
    db.commit()
    rows = db.scalars(select(ChangeLog)).all()
    deletes = [r for r in rows if r.entity_type == "Barcode"]
    assert {r.entity_id for r in deletes} == {"one", "two"}
    assert {r.change_kind for r in deletes} == {"DELETE"}
    assert len({r.revision for r in rows}) == 1
    for code, version in [("one", 4), ("two", 2), ("gone", 5)]:
        barcode = db.get(Barcode, code)
        assert barcode.deleted_at is not None
        assert barcode.version == version
    for row in deletes:
        barcode = db.get(Barcode, row.entity_id)
        assert json.loads(row.snapshot) == {"code": barcode.code, "productId": "old",
            "version": barcode.version, "deletedAt": barcode.deleted_at.isoformat()}
    page = fetch_sync_page(db, before, limit=1)
    assert {c["entityId"] for c in page["changes"] if c["changeKind"] == "DELETE"} == {"one", "two"}
    assert soft_delete_product(db, ChangeSet(db), "delete", "old", 1) == result
    db.commit()
    assert len(db.scalars(select(ChangeLog)).all()) == len(rows)
    assert db.get(Barcode, "one").version == 4


@pytest.mark.parametrize("legacy,new_product", [(False, {"name": "New"}),
                                               (True, {"name": "New"}), (True, None)])
def test_purchase_reclaims_deleted_owner_barcode(db_session, legacy, new_product):
    db = db_session
    seed(db)
    if legacy:
        db.get(Product, "old").deleted_at = datetime.utcnow()
        if new_product is None:
            db.add(Product(id="new", name="New", version=1))
    else:
        soft_delete_product(db, ChangeSet(db), "delete", "old", 1)
    db.commit()
    result = purchase(db, new_product)
    db.commit()
    assert result["productId"] == "new"
    barcode = db.get(Barcode, "one")
    assert barcode.product_id == "new"
    assert barcode.deleted_at is None
    assert barcode.version == (4 if legacy else 5)
    updates = db.scalars(select(ChangeLog).where(ChangeLog.entity_type == "Barcode",
                                                ChangeLog.change_kind == "UPDATE")).all()
    assert len(updates) == 1
    assert json.loads(updates[0].snapshot)["productId"] == "new"


def test_purchase_keeps_active_owner_conflict(db_session):
    seed(db_session)
    with pytest.raises(BusinessRuleViolation) as exc:
        purchase(db_session, {"name": "New"})
    assert exc.value.code == "BARCODE_ALREADY_ASSIGNED"
    db_session.rollback()
    assert db_session.get(Barcode, "one").product_id == "old"
    assert db_session.get(Product, "new") is None


@pytest.mark.parametrize("legacy", [False, True])
def test_deleted_owner_resolves_as_unknown(db_session, monkeypatch, legacy):
    db = db_session
    seed(db)
    if legacy:
        db.get(Product, "old").deleted_at = datetime.utcnow()
    else:
        soft_delete_product(db, ChangeSet(db), "delete", "old", 1)
    db.commit()
    monkeypatch.setattr(resolver_service, "get_engine", lambda: db.get_bind())
    calls = []
    async def fetch(barcode, settings):
        calls.append(barcode)
        return {s.source_id: SourceResult(s.source_id, "NOT_FOUND", None) for s in ALL_SOURCES}
    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fetch)
    async def check():
        result = await asyncio.wait_for(resolver_service.resolve("one"), timeout=5)
        assert result.matched_locally is False
        assert result.product is None
        assert result.resolution_id is not None
        assert await asyncio.wait_for(resolver_service.re_resolve("old", "one"), timeout=5) is None
    asyncio.run(check())
    assert calls == ["one"]


@pytest.mark.parametrize("branch", ["absent", "deleted_row", "deleted_owner", "active_foreign"])
def test_existing_product_purchase_barcode_branches(db_session, branch):
    db = db_session
    seed(db)
    db.add(Product(id="new", name="New", version=1))
    barcode = db.get(Barcode, "one")
    if branch == "absent":
        db.delete(barcode)
    elif branch == "deleted_row":
        barcode.deleted_at = datetime.utcnow()
    elif branch == "deleted_owner":
        db.get(Product, "old").deleted_at = datetime.utcnow()
    db.commit()
    assert purchase(db, None)["productId"] == "new"
    db.commit()
    barcode = db.get(Barcode, "one")
    assert barcode.product_id == ("old" if branch == "active_foreign" else "new")
    assert barcode.deleted_at is None
    changes = db.scalars(select(ChangeLog).where(ChangeLog.entity_type == "Barcode")).all()
    assert len(changes) == (0 if branch == "active_foreign" else 1)
    if changes:
        assert changes[0].change_kind == ("CREATE" if branch == "absent" else "UPDATE")
