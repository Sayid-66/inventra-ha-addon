from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def _setup(client, device):
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL1"), "id": test_uuid("l1"), "name": "Keller"}, headers=_headers(device))
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL2"), "id": test_uuid("l2"), "name": "Speisekammer"}, headers=_headers(device))
    client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("opP"), "id": test_uuid("e1"), "productId": test_uuid("p1"),
            "newProduct": {"name": "Milch", "imageUrl": None}, "barcode": "4001",
            "locationId": test_uuid("l1"), "quantity": 3, "storeId": None,
            "pricePerUnitCents": None, "mhd": None, "minStock": None,
            "contentUnitLabel": None, "contentTotal": None, "contentBreakdown": None, "timestamp": 1000,
        },
        headers=_headers(device),
    )


def test_relocate_moves_quantity_between_locations(api_client_with_device):
    client, device = api_client_with_device
    _setup(client, device)
    resp = client.post(
        "/api/v1/relocations",
        json={
            "operationId": test_uuid("opR1"), "id": test_uuid("r1"), "productId": test_uuid("p1"), "fromLocationId": test_uuid("l1"),
            "toLocationId": test_uuid("l2"), "stockKind": "STK", "quantity": 2, "timestamp": 2000,
        },
        headers=_headers(device),
    )
    assert resp.status_code == 201
    assert resp.json()["quantity"] == 2


def test_relocate_same_location_rejected(api_client_with_device):
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    _setup(client, device)
    resp = client.post(
        "/api/v1/relocations",
        json={
            "operationId": test_uuid("opR1"), "id": test_uuid("r1"), "productId": test_uuid("p1"), "fromLocationId": location_id,
            "toLocationId": location_id, "stockKind": "STK", "quantity": 1, "timestamp": 2000,
        },
        headers=_headers(device),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_relocation_replay_same_operation_id_is_safe_noop(api_client_with_device):
    client, device = api_client_with_device
    _setup(client, device)
    payload = {
        "operationId": test_uuid("opR1"), "id": test_uuid("r1"),
        "productId": test_uuid("p1"), "fromLocationId": test_uuid("l1"),
        "toLocationId": test_uuid("l2"), "stockKind": "STK",
        "quantity": 2, "timestamp": 2000,
    }
    first = client.post("/api/v1/relocations", json=payload, headers=_headers(device))
    second = client.post("/api/v1/relocations", json=payload, headers=_headers(device))
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


def test_relocate_rejects_tombstoned_product(api_client_with_device):
    client, device = api_client_with_device
    headers = _headers(device)
    product_id = test_uuid("p1")
    _setup(client, device)
    client.request(
        "DELETE",
        f"/api/v1/products/{product_id}",
        json={"operationId": test_uuid("op-delete"), "version": 1},
        headers=headers,
    )

    resp = client.post(
        "/api/v1/relocations",
        json={
            "operationId": test_uuid("opR1"), "id": test_uuid("r1"),
            "productId": product_id, "fromLocationId": test_uuid("l1"),
            "toLocationId": test_uuid("l2"), "stockKind": "STK",
            "quantity": 2, "timestamp": 2000,
        },
        headers=headers,
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
