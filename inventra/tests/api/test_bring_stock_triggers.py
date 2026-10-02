from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def test_correction_that_increases_stock_clears_an_existing_lock(api_client_with_device, monkeypatch):
    client, device = api_client_with_device
    calls = []

    async def _fake_schedule(product_id, increased):
        calls.append((product_id, increased))

    monkeypatch.setattr("inventra_backend.api.events.bring_service.schedule_stock_change", _fake_schedule)

    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("op0"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    client.post("/api/v1/products", json={"operationId": test_uuid("opp"), "id": product_id, "name": "Wasser", "minStock": 3}, headers=_headers(device))
    client.post("/api/v1/purchases", json={
        "operationId": test_uuid("op1"), "id": test_uuid("e1"), "productId": product_id, "newProduct": None,
        "barcode": "4001", "locationId": location_id, "quantity": 1, "storeId": None, "pricePerUnitCents": None,
        "mhd": None, "minStock": 3, "contentUnitLabel": None, "contentTotal": None, "contentBreakdown": None,
        "timestamp": 1000,
    }, headers=_headers(device))
    calls.clear()

    resp = client.post("/api/v1/corrections", json={
        "operationId": test_uuid("op2"), "id": test_uuid("e2"), "productId": product_id, "locationId": location_id,
        "stockKind": "STK", "contentUnitLabel": None, "newQuantity": 5, "mhdForIncrease": None, "timestamp": 2000,
    }, headers=_headers(device))

    assert resp.status_code == 201
    assert calls == [(product_id, True)]


def test_correction_that_decreases_stock_does_not_report_an_increase(api_client_with_device, monkeypatch):
    client, device = api_client_with_device
    calls = []

    async def _fake_schedule(product_id, increased):
        calls.append((product_id, increased))

    monkeypatch.setattr("inventra_backend.api.events.bring_service.schedule_stock_change", _fake_schedule)

    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("op0"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    client.post("/api/v1/purchases", json={
        "operationId": test_uuid("op1"), "id": test_uuid("e1"), "productId": product_id,
        "newProduct": {"name": "Wasser", "imageUrl": None}, "barcode": "4001", "locationId": location_id,
        "quantity": 5, "storeId": None, "pricePerUnitCents": None, "mhd": None, "minStock": 3,
        "contentUnitLabel": None, "contentTotal": None, "contentBreakdown": None, "timestamp": 1000,
    }, headers=_headers(device))
    calls.clear()

    resp = client.post("/api/v1/corrections", json={
        "operationId": test_uuid("op2"), "id": test_uuid("e2"), "productId": product_id, "locationId": location_id,
        "stockKind": "STK", "contentUnitLabel": None, "newQuantity": 2, "mhdForIncrease": None, "timestamp": 2000,
    }, headers=_headers(device))

    assert resp.status_code == 201
    assert calls == [(product_id, False)]


def test_deleting_a_product_schedules_cleanup(api_client_with_device, monkeypatch):
    client, device = api_client_with_device
    calls = []

    async def _fake_on_deleted(product_id):
        calls.append(product_id)

    monkeypatch.setattr("inventra_backend.api.products.bring_service.on_product_deleted_with_cleanup", _fake_on_deleted)

    product_id = test_uuid("p1")
    client.post("/api/v1/products", json={"operationId": test_uuid("opp"), "id": product_id, "name": "Wasser"}, headers=_headers(device))
    resp = client.request("DELETE", f"/api/v1/products/{product_id}", json={"operationId": test_uuid("opd"), "version": 1}, headers=_headers(device))

    assert resp.status_code == 200
    assert calls == [product_id]


def test_product_delete_removes_created_bring_item(api_client_with_device, monkeypatch):
    from sqlalchemy.orm import Session
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import BringWatchState, BringWatchOrigin, BringWatchStateEnum
    from inventra_backend.services import bring_service
    client, device = api_client_with_device
    items = [{"uid": "water", "summary": "Wasser", "status": "needs_action"}]
    class FakeClient:
        async def get_items(self):
            return items
        async def remove_item(self, uid):
            items[:] = [item for item in items if item["uid"] != uid]
    monkeypatch.setattr(bring_service, "BringHaClient", lambda **kwargs: FakeClient())
    product_id = test_uuid("cleanup-product")
    assert client.post("/api/v1/products", json={"operationId": test_uuid("cleanup-create"), "id": product_id, "name": "Wasser"}, headers=_headers(device)).status_code == 201
    with Session(get_engine()) as db:
        db.add(BringWatchState(product_id=product_id, origin=BringWatchOrigin.INVENTRA_CREATED, state=BringWatchStateEnum.ON_LIST_CONFIRMED, bring_item_name="Wasser", bring_uid="water", retry_count=0))
        db.commit()
    response = client.request("DELETE", f"/api/v1/products/{product_id}", json={"operationId": test_uuid("cleanup-delete"), "version": 1}, headers=_headers(device))
    assert response.status_code == 200
    assert items == []
    with Session(get_engine()) as db:
        assert db.get(BringWatchState, product_id) is None
