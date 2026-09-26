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

    def _fake_on_deleted(product_id):
        calls.append(product_id)

    monkeypatch.setattr("inventra_backend.api.products.bring_service.on_product_deleted_sync", _fake_on_deleted)

    product_id = test_uuid("p1")
    client.post("/api/v1/products", json={"operationId": test_uuid("opp"), "id": product_id, "name": "Wasser"}, headers=_headers(device))
    resp = client.request("DELETE", f"/api/v1/products/{product_id}", json={"operationId": test_uuid("opd"), "version": 1}, headers=_headers(device))

    assert resp.status_code == 200
    assert calls == [product_id]
