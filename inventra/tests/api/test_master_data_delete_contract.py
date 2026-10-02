import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventra_backend.db.base import get_engine
from inventra_backend.db.models import (
    Batch, ChangeLog, ConsumptionEvent, CorrectionEvent, Location, Product,
    PurchaseEvent, RelocationEvent, Store,
)
from tests.ids import test_uuid


@pytest.mark.parametrize("reference", ["batch", "purchase", "consumption", "correction", "relocation_from", "relocation_to", "store_purchase"])
def test_used_master_data_delete_is_422_without_changes(api_client_with_device, reference):
    client, device = api_client_with_device
    location_id, other_id, store_id, product_id = map(test_uuid, ["location", "other", "store", "product"])
    common = dict(id=test_uuid("event"), product_id=product_id, timestamp=1, user_id="user", source="ANDROID")
    with Session(get_engine()) as db:
        db.add_all([Location(id=location_id, name="Keller", normalized_name="keller"),
                    Location(id=other_id, name="Other", normalized_name="other"),
                    Store(id=store_id, name="Shop", normalized_name="shop"),
                    Product(id=product_id, name="Product")])
        db.flush()
        if reference == "batch":
            row = Batch(id=test_uuid("batch"), product_id=product_id, location_id=location_id,
                        event_timestamp=1, is_content_tracked=False, remaining_quantity=0)
        elif reference in ("purchase", "store_purchase"):
            row = PurchaseEvent(**common, location_id=location_id, store_id=store_id, barcode="123", quantity=1)
        elif reference == "consumption":
            row = ConsumptionEvent(**common, location_id=location_id, stock_kind="PIECE", quantity=1)
        elif reference == "correction":
            row = CorrectionEvent(**common, location_id=location_id, stock_kind="PIECE", old_quantity=1, new_quantity=0)
        else:
            row = RelocationEvent(**common, from_location_id=location_id if reference == "relocation_from" else other_id,
                                  to_location_id=location_id if reference == "relocation_to" else other_id,
                                  stock_kind="PIECE", quantity=1)
        db.add(row)
        db.commit()
        before = db.scalar(select(func.count()).select_from(ChangeLog))
    is_store = reference == "store_purchase"
    entity_id = store_id if is_store else location_id
    response = client.request("DELETE", f"/api/v1/{'stores' if is_store else 'locations'}/{entity_id}",
                              json={"operationId": test_uuid("delete"), "version": 1},
                              headers={"Authorization": f"Bearer {device.token}"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ("STORE_IN_USE" if is_store else "LOCATION_IN_USE")
    with Session(get_engine()) as db:
        assert db.scalar(select(func.count()).select_from(ChangeLog)) == before
        entity = db.get(Store if is_store else Location, entity_id)
        assert entity.version == 1
        assert entity.deleted_at is None



@pytest.mark.parametrize("kind", ["locations", "stores"])
def test_deleted_names_can_be_reused(api_client_with_device, kind):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    def create(label, name):
        return client.post(f"/api/v1/{kind}", json={"operationId": test_uuid(label + "-create"), "id": test_uuid(label), "name": name}, headers=headers)
    def delete(label):
        response = client.request("DELETE", f"/api/v1/{kind}/{test_uuid(label)}", json={"operationId": test_uuid(label + "-delete"), "version": 1}, headers=headers)
        assert response.status_code == 200
        assert response.json()["name"] == "Keller"
        assert "normalized_name" not in response.json()
    assert create("original", "Keller").status_code == 201
    delete("original")
    assert create("replacement", "Keller").status_code == 201
    assert create("duplicate", " KELLER ").status_code == 409
    delete("replacement")
    assert create("other", "Other").status_code == 201
    renamed = client.patch(f"/api/v1/{kind}/{test_uuid('other')}", json={"operationId": test_uuid("rename"), "name": "KELLER", "version": 1}, headers=headers)
    assert renamed.status_code == 200
    assert create("live-clash", "Keller").status_code == 409
    assert create("another", "Another").status_code == 201
    assert client.patch(f"/api/v1/{kind}/{test_uuid('another')}", json={"operationId": test_uuid("clash-rename"), "name": "Keller", "version": 1}, headers=headers).status_code == 409
    with Session(get_engine()) as db:
        model = Location if kind == "locations" else Store
        for label in ("original", "replacement"):
            row = db.get(model, test_uuid(label))
            assert row.name == "Keller"
            assert row.normalized_name == f"~deleted~{row.id}~keller"
        import json
        snapshots = [json.loads(row.snapshot) for row in db.scalars(select(ChangeLog)).all()]
        assert all("normalized_name" not in row and "normalizedName" not in row for row in snapshots)



@pytest.mark.parametrize("kind", ["locations", "stores"])
def test_repeated_delete_refuses_tombstone(db_session, kind):
    from inventra_backend.errors import DuplicateEntityError
    from inventra_backend.revision.change_log import ChangeSet
    from inventra_backend.services.location_service import soft_delete_location
    from inventra_backend.services.store_service import soft_delete_store
    model, delete = (Location, soft_delete_location) if kind == "locations" else (Store, soft_delete_store)
    row = model(id=test_uuid(kind), name="Keller", normalized_name="keller")
    db_session.add(row)
    db_session.flush()
    delete(db_session, ChangeSet(db_session), test_uuid("first-delete"), row.id, row.version)
    before = (row.normalized_name, row.version, row.deleted_at)
    with pytest.raises(DuplicateEntityError):
        delete(db_session, ChangeSet(db_session), test_uuid("second-delete"), row.id, row.version)
    assert (row.normalized_name, row.version, row.deleted_at) == before
