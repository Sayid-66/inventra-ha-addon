from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def _purchase(client, device, quantity=2):
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": test_uuid("l1"), "name": "Keller"}, headers=_headers(device))
    client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("opP"), "id": test_uuid("e1"), "productId": test_uuid("p1"),
            "newProduct": {"name": "Milch", "imageUrl": None}, "barcode": "4001",
            "locationId": test_uuid("l1"), "quantity": quantity, "storeId": None,
            "pricePerUnitCents": None, "mhd": None, "minStock": None,
            "contentUnitLabel": None, "contentTotal": None, "contentBreakdown": None, "timestamp": 1000,
        },
        headers=_headers(device),
    )


def test_correction_upward_creates_new_batch(api_client_with_device):
    client, device = api_client_with_device
    correction_id = test_uuid("c1")
    product_id = test_uuid("p1")
    location_id = test_uuid("l1")
    _purchase(client, device, quantity=2)
    resp = client.post(
        "/api/v1/corrections",
        json={
            "operationId": test_uuid("opC1"), "id": correction_id, "productId": product_id, "locationId": location_id,
            "stockKind": "STK", "contentUnitLabel": None, "newQuantity": 5,
            "mhdForIncrease": None, "timestamp": 2000,
        },
        headers=_headers(device),
    )
    assert resp.status_code == 201
    assert resp.json() == {
        "eventId": correction_id, "productId": product_id, "timestamp": 2000, "locationId": location_id,
        "stockKind": "STK", "contentUnitLabel": None, "oldQuantity": 2, "newQuantity": 5,
        "mhdForIncrease": None, "userId": device.user_id, "sourceDeviceId": device.device_id, "source": "ANDROID",
    }


def test_correction_no_op_rejected(api_client_with_device):
    client, device = api_client_with_device
    _purchase(client, device, quantity=2)
    resp = client.post(
        "/api/v1/corrections",
        json={
            "operationId": test_uuid("opC1"), "id": test_uuid("c1"), "productId": test_uuid("p1"), "locationId": test_uuid("l1"),
            "stockKind": "STK", "contentUnitLabel": None, "newQuantity": 2,
            "mhdForIncrease": None, "timestamp": 2000,
        },
        headers=_headers(device),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "NO_OP_CORRECTION"


def test_correction_replay_same_operation_id_is_safe_noop(api_client_with_device):
    client, device = api_client_with_device
    _purchase(client, device, quantity=2)
    payload = {
        "operationId": test_uuid("opC1"), "id": test_uuid("c1"),
        "productId": test_uuid("p1"), "locationId": test_uuid("l1"),
        "stockKind": "STK", "contentUnitLabel": None, "newQuantity": 5,
        "mhdForIncrease": None, "timestamp": 2000,
    }
    first = client.post("/api/v1/corrections", json=payload, headers=_headers(device))
    second = client.post("/api/v1/corrections", json=payload, headers=_headers(device))
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


def test_correct_stock_rejects_tombstoned_product(api_client_with_device):
    client, device = api_client_with_device
    headers = _headers(device)
    product_id = test_uuid("p1")
    _purchase(client, device, quantity=2)
    client.request(
        "DELETE",
        f"/api/v1/products/{product_id}",
        json={"operationId": test_uuid("op-delete"), "version": 1},
        headers=headers,
    )

    resp = client.post(
        "/api/v1/corrections",
        json={
            "operationId": test_uuid("opC1"), "id": test_uuid("c1"),
            "productId": product_id, "locationId": test_uuid("l1"),
            "stockKind": "STK", "contentUnitLabel": None, "newQuantity": 5,
            "mhdForIncrease": None, "timestamp": 2000,
        },
        headers=headers,
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
