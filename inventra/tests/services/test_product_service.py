import json

from datetime import datetime

from sqlalchemy import func, select

from inventra_backend.db.models import (
    Barcode,
    Batch,
    ChangeKind,
    ChangeLog,
    ConsumptionEvent,
    CorrectionEvent,
    Location,
    Product,
    PurchaseEvent,
    RelocationEvent,
    Source,
)
from inventra_backend.revision.change_log import change_set
from inventra_backend.services import product_service
from inventra_backend.services.snapshot_service import fetch_snapshot_page


DEPENDENT_TYPES = {
    "Batch": Batch,
    "PurchaseEvent": PurchaseEvent,
    "ConsumptionEvent": ConsumptionEvent,
    "CorrectionEvent": CorrectionEvent,
    "RelocationEvent": RelocationEvent,
    "Barcode": Barcode,
}


def _seed_product_graph(db, suffix: str, *, deleted: bool = False) -> dict[str, str]:
    product_id = f"product-{suffix}"
    location_id = f"location-{suffix}"
    ids = {
        "Product": product_id,
        "Batch": f"batch-{suffix}",
        "PurchaseEvent": f"purchase-{suffix}",
        "ConsumptionEvent": f"consumption-{suffix}",
        "CorrectionEvent": f"correction-{suffix}",
        "RelocationEvent": f"relocation-{suffix}",
        "Barcode": f"barcode-{suffix}",
    }
    db.add(Product(
        id=product_id,
        name=f"Product {suffix}",
        version=2 if deleted else 1,
        deleted_at=datetime.utcnow() if deleted else None,
    ))
    db.add(Location(
        id=location_id,
        name=f"Location {suffix}",
        normalized_name=f"location-{suffix}",
        version=1,
    ))
    db.add(PurchaseEvent(
        id=ids["PurchaseEvent"], product_id=product_id, timestamp=1,
        barcode=ids["Barcode"], location_id=location_id, quantity=1,
        user_id="user", source=Source.ANDROID,
    ))
    db.add(ConsumptionEvent(
        id=ids["ConsumptionEvent"], product_id=product_id, timestamp=2,
        location_id=location_id, stock_kind="UNIT", quantity=1,
        user_id="user", source=Source.ANDROID,
    ))
    db.add(CorrectionEvent(
        id=ids["CorrectionEvent"], product_id=product_id, timestamp=3,
        location_id=location_id, stock_kind="UNIT", old_quantity=1,
        new_quantity=2, user_id="user", source=Source.ANDROID,
    ))
    db.add(RelocationEvent(
        id=ids["RelocationEvent"], product_id=product_id, timestamp=4,
        from_location_id=location_id, to_location_id=location_id,
        stock_kind="UNIT", quantity=1, user_id="user", source=Source.ANDROID,
    ))
    db.add(Batch(
        id=ids["Batch"], product_id=product_id,
        purchase_event_id=ids["PurchaseEvent"], location_id=location_id,
        event_timestamp=1, is_content_tracked=False, remaining_quantity=1,
    ))
    db.add(Barcode(code=ids["Barcode"], product_id=product_id, version=1))
    db.flush()

    with change_set(db) as cs:
        cs.record("Product", product_id, ChangeKind.CREATE, {"id": product_id})
        for entity_type, entity_id in ids.items():
            if entity_type != "Product":
                kind = ChangeKind.CREATE if entity_type in {"Batch", "Barcode"} else ChangeKind.EVENT
                cs.record(
                    entity_type,
                    entity_id,
                    kind,
                    {"id": entity_id, "productId": product_id},
                )
    if deleted:
        with change_set(db) as cs:
            cs.record("Product", product_id, ChangeKind.DELETE, {"id": product_id})
    db.flush()
    return ids


def _change_rows(db, *, minimum_id: int = 0) -> list[ChangeLog]:
    return list(
        db.execute(
            select(ChangeLog).where(ChangeLog.id > minimum_id).order_by(ChangeLog.id)
        ).scalars().all()
    )


def test_soft_delete_preserves_dependents_in_snapshot_and_delta(db_session):
    from inventra_backend.services.sync_service import fetch_sync_page

    ids = _seed_product_graph(db_session, "preserved")
    before = _change_rows(db_session)
    with change_set(db_session) as cs:
        product_service.soft_delete_product(db_session, cs, "delete", ids["Product"], 1)
    db_session.flush()
    added = _change_rows(db_session, minimum_id=before[-1].id)
    assert len(added) == 1
    assert added[0].entity_type == "Product"
    assert added[0].change_kind == "UPDATE"
    assert json.loads(added[0].snapshot)["deletedAt"] is not None
    page = fetch_snapshot_page(db_session, None, None, None, limit=100)
    latest = {e["entityType"]: e for e in page["entities"]}
    assert latest["Product"]["changeKind"] == "UPDATE"
    assert latest["Product"]["snapshot"]["deletedAt"] is not None
    for old in before:
        if old.entity_type != "Product":
            assert latest[old.entity_type]["changeKind"] == old.change_kind
            assert latest[old.entity_type]["snapshot"] == json.loads(old.snapshot)
    delta = fetch_sync_page(db_session, 0, limit=100)["changes"]
    assert [e for e in delta if e["entityType"] != "Product"] == [
        {"revision": r.revision, "entityType": r.entity_type, "entityId": r.entity_id,
         "changeKind": r.change_kind, "snapshot": json.loads(r.snapshot)}
        for r in before if r.entity_type != "Product"
    ]
    assert delta[-1]["changeKind"] == "UPDATE"
    assert delta[-1]["snapshot"]["deletedAt"] is not None


def test_backfill_only_product_when_dependents_are_live_and_second_run_is_noop(db_session):
    ids = _seed_product_graph(db_session, "live-history", deleted=True)
    before = _change_rows(db_session)[-1]
    assert product_service.backfill_deleted_product_history(db_session) == 1
    added = _change_rows(db_session, minimum_id=before.id)
    assert [(r.entity_type, r.entity_id, r.change_kind) for r in added] == [
        ("Product", ids["Product"], "UPDATE")
    ]
    assert json.loads(added[0].snapshot)["deletedAt"] is not None
    revision = added[-1].revision
    assert product_service.backfill_deleted_product_history(db_session) == 0
    from inventra_backend.db.models import RevisionCounter
    assert db_session.get(RevisionCounter, 0).current_revision == revision
    assert _change_rows(db_session, minimum_id=added[-1].id) == []


def test_backfill_repairs_event_tombstones_from_current_data_but_skips_batch_and_barcode(db_session):
    ids = _seed_product_graph(db_session, "deleted-history", deleted=True)
    active = _seed_product_graph(db_session, "active")
    with change_set(db_session) as cs:
        for graph in (ids, active):
            for entity_type, entity_id in graph.items():
                if entity_type != "Product":
                    cs.record(entity_type, entity_id, ChangeKind.DELETE, {"id": entity_id})
    db_session.get(PurchaseEvent, ids["PurchaseEvent"]).quantity = 7
    db_session.flush()
    before = _change_rows(db_session)[-1].id
    assert product_service.backfill_deleted_product_history(db_session) == 5
    added = _change_rows(db_session, minimum_id=before)
    assert {r.entity_type for r in added} == {
        "Product", "PurchaseEvent", "ConsumptionEvent", "CorrectionEvent", "RelocationEvent"
    }
    assert all(r.change_kind == "UPDATE" for r in added)
    assert len({r.revision for r in added}) == 1
    payloads = {r.entity_type: json.loads(r.snapshot) for r in added}
    assert payloads["PurchaseEvent"]["quantity"] == 7
    for entity_type in ("PurchaseEvent", "ConsumptionEvent", "CorrectionEvent", "RelocationEvent"):
        assert payloads[entity_type]["eventId"] == ids[entity_type]
        assert payloads[entity_type]["productId"] == ids["Product"]
        assert payloads[entity_type]["source"] == "ANDROID"
    assert payloads["ConsumptionEvent"]["quantity"] == 1
    assert payloads["CorrectionEvent"]["newQuantity"] == 2
    assert payloads["RelocationEvent"]["toLocationId"] == "location-deleted-history"
    page = fetch_snapshot_page(db_session, None, None, None, limit=100)
    latest = {(e["entityType"], e["entityId"]): e for e in page["entities"]}
    for entity_type in ("Batch", "Barcode"):
        assert latest[(entity_type, ids[entity_type])]["changeKind"] == "DELETE"
    assert latest[("PurchaseEvent", active["PurchaseEvent"])]["changeKind"] == "DELETE"
    assert product_service.backfill_deleted_product_history(db_session) == 0
    assert _change_rows(db_session, minimum_id=added[-1].id) == []
