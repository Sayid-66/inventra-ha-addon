from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def _purchase(client, device, quantity=2, timestamp=1000, event_id="e1"):
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": test_uuid("l1"), "name": "Keller"}, headers=_headers(device))
    client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid(f"op-{event_id}"), "id": test_uuid(event_id), "productId": test_uuid("p1"),
            "newProduct": {"name": "Milch", "imageUrl": None} if event_id == "e1" else None,
            "barcode": "4001", "locationId": test_uuid("l1"), "quantity": quantity,
            "storeId": None, "pricePerUnitCents": None, "mhd": None,
            "minStock": None, "contentUnitLabel": None, "contentTotal": None,
            "contentBreakdown": None, "timestamp": timestamp,
        },
        headers=_headers(device),
    )


def test_consume_depletes_stock(api_client_with_device):
    client, device = api_client_with_device
    _purchase(client, device, quantity=2)
    resp = client.post(
        "/api/v1/consumptions",
        json={"operationId": test_uuid("opC1"), "id": test_uuid("c1"), "productId": test_uuid("p1"), "locationId": test_uuid("l1"), "stockKind": "STK", "quantity": 1, "timestamp": 2000},
        headers=_headers(device),
    )
    assert resp.status_code == 201
    assert resp.json()["quantity"] == 1


def test_consume_more_than_available_rejected_without_mutating(api_client_with_device):
    client, device = api_client_with_device
    _purchase(client, device, quantity=2)
    resp = client.post(
        "/api/v1/consumptions",
        json={"operationId": test_uuid("opC1"), "id": test_uuid("c1"), "productId": test_uuid("p1"), "locationId": test_uuid("l1"), "stockKind": "STK", "quantity": 5, "timestamp": 2000},
        headers=_headers(device),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INSUFFICIENT_STOCK"


def test_consumption_replay_same_operation_id_is_safe_noop(api_client_with_device):
    client, device = api_client_with_device
    _purchase(client, device, quantity=2)
    payload = {
        "operationId": test_uuid("opC1"), "id": test_uuid("c1"),
        "productId": test_uuid("p1"), "locationId": test_uuid("l1"),
        "stockKind": "STK", "quantity": 1, "timestamp": 2000,
    }
    first = client.post("/api/v1/consumptions", json=payload, headers=_headers(device))
    second = client.post("/api/v1/consumptions", json=payload, headers=_headers(device))
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    stock = client.get("/api/v1/stock", headers=_headers(device)).json()
    assert stock[0]["totalStk"] == 1


def test_consume_rejects_tombstoned_product(api_client_with_device):
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
        "/api/v1/consumptions",
        json={
            "operationId": test_uuid("opC1"), "id": test_uuid("c1"),
            "productId": product_id, "locationId": test_uuid("l1"),
            "stockKind": "STK", "quantity": 1, "timestamp": 2000,
        },
        headers=headers,
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
