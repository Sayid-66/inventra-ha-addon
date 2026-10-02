import pytest
from sqlalchemy import text

from inventra_backend.db.base import get_engine
from inventra_backend.services.unit_normalizer import STANDARD_UNIT_IDS
from tests.ids import test_uuid


@pytest.fixture
def purchase(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    product_id, location_id = test_uuid("wp22-product"), test_uuid("wp22-location")
    assert client.post("/api/v1/locations", headers=headers, json={
        "operationId": test_uuid("wp22-location-op"), "id": location_id, "name": "Keller",
    }).status_code == 201
    assert client.post("/api/v1/products", headers=headers, json={
        "operationId": test_uuid("wp22-product-op"), "id": product_id, "name": "Milch",
        "brand": "Nord", "variant": "Original", "category": "Food", "minStock": 7,
        "quantity": 1, "unitId": STANDARD_UNIT_IDS["l"],
    }).status_code == 201
    payload = {
        "operationId": test_uuid("wp22-purchase-op"), "id": test_uuid("wp22-event"),
        "productId": product_id, "barcode": "4001", "locationId": location_id,
        "quantity": 2, "minStock": 1, "timestamp": 1000,
    }
    return client, headers, payload


def product(purchase):
    client, headers, payload = purchase
    return next(p for p in client.get("/api/v1/products", headers=headers).json()
                if p["id"] == payload["productId"])


def post(purchase, update):
    client, headers, payload = purchase
    return client.post("/api/v1/purchases", headers=headers,
                       json={**payload, "productUpdate": update})


def test_atomic_update_replay_mismatch_and_sync(purchase):
    client, headers, payload = purchase
    since = client.get("/api/v1/sync?since_revision=0", headers=headers).json()["nextRevision"]
    update = {"name": "Haferdrink", "quantity": 500, "unitId": STANDARD_UNIT_IDS["ml"], "minStock": 4}
    payload.update(contentUnitLabel="ml", contentTotal=1000)
    first = post(purchase, update)
    assert first.status_code == 201
    current = product(purchase)
    assert (current["name"], current["quantity"], current["unit"]["id"], current["minStock"], current["version"]) == (
        "Haferdrink", 500, STANDARD_UNIT_IDS["ml"], 4, 2)
    assert current["fieldProvenance"]["name"]["manual"] is True
    assert post(purchase, update).json() == first.json()
    assert product(purchase)["version"] == 2
    mismatch = post(purchase, {**update, "quantity": 600})
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "OPERATION_ID_PAYLOAD_MISMATCH"
    changes = client.get(f"/api/v1/sync?since_revision={since}&limit=1", headers=headers).json()["changes"]
    updates = [c for c in changes if c["entityType"] == "Product"]
    assert len(updates) == 1
    assert updates[0]["changeKind"] == "UPDATE"
    assert updates[0]["snapshot"] == current
    assert {c["entityType"] for c in changes} == {"Product", "Barcode", "PurchaseEvent", "Batch"}
    assert len({c["revision"] for c in changes}) == 1
    with get_engine().connect() as db:
        assert db.execute(text("SELECT count(*) FROM purchase_events")).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM batches")).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM change_log WHERE entity_type = 'Product' AND change_kind = 'UPDATE'")).scalar_one() == 1


@pytest.mark.parametrize("update", [{}, {"brand": None}, {"quantity": 2}, {"unitId": None}, {"minStock": None},
                                    {"brand": None, "variant": None, "category": None, "quantity": None, "unitId": None}])
def test_only_provided_fields_change(purchase, update):
    before = product(purchase)
    assert post(purchase, update).status_code == 201
    after = product(purchase)
    for field in ("name", "brand", "variant", "category", "quantity", "minStock"):
        assert after[field] == update.get(field, before[field])
    assert after["unit"] == (None if "unitId" in update else before["unit"])
    if update.get("brand", "absent") is None:
        assert after["fieldProvenance"]["brand"]["manual"] is True
    assert after["version"] == (1 if not update else 2)


def test_legacy_min_stock_and_ignored_update_for_new_product(purchase):
    client, headers, payload = purchase
    assert client.post("/api/v1/purchases", headers=headers, json=payload).status_code == 201
    assert product(purchase)["minStock"] == 1
    payload.update(operationId=test_uuid("wp22-new-op"), id=test_uuid("wp22-new-event"),
                   productId=test_uuid("wp22-new-product"), barcode="4002", newProduct={"name": "New"},
                   minStock=9)
    assert post(purchase, {"name": "Ignored", "minStock": 3, "unitId": "unknown"}).status_code == 201
    assert product(purchase)["name"] == "New"
    assert product(purchase)["minStock"] == 9


@pytest.mark.parametrize("update,code", [({"name": "  "}, None), ({"name": None}, None),
    ({"quantity": 0}, None), ({"quantity": 100_000_001}, None), ({"brand": "x" * 201}, None),
    ({"name": "Changed", "unitId": "unknown"}, "UNKNOWN_UNIT")])
def test_invalid_update_rolls_back_everything(purchase, update, code):
    before = product(purchase)
    response = post(purchase, update)
    assert response.status_code == 422
    if code:
        assert response.json()["error"]["code"] == code
    assert product(purchase) == before
    with get_engine().connect() as db:
        for table in ("purchase_events", "batches", "barcodes"):
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
