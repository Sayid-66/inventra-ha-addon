from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def _purchase(client, device):
    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("opP"), "id": test_uuid("e1"), "productId": product_id,
            "newProduct": {"name": "Milch", "imageUrl": None}, "barcode": "4001",
            "locationId": location_id, "quantity": 2, "storeId": None, "pricePerUnitCents": None,
            "mhd": "2026-09-10", "minStock": 1, "contentUnitLabel": None,
            "contentTotal": None, "contentBreakdown": None, "timestamp": 1000,
        },
        headers=_headers(device),
    )


def test_current_stock_shows_purchased_product(api_client_with_device):
    client, device = api_client_with_device
    _purchase(client, device)
    resp = client.get("/api/v1/stock", headers=_headers(device))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["productId"] == test_uuid("p1")
    assert body[0]["totalStk"] == 2
    assert body[0]["nextMhd"] == "2026-09-10"
    assert body[0]["minStock"] == 1


def test_stock_history_hides_products_with_stock(api_client_with_device):
    client, device = api_client_with_device
    _purchase(client, device)
    resp = client.get("/api/v1/stock?view=history", headers=_headers(device))
    assert resp.json() == []


def test_stock_history_shows_fully_consumed_product(api_client_with_device):
    client, device = api_client_with_device
    _purchase(client, device)
    client.post(
        "/api/v1/consumptions",
        json={"operationId": test_uuid("opC"), "id": test_uuid("c1"), "productId": test_uuid("p1"), "locationId": test_uuid("l1"), "stockKind": "STK", "quantity": 2, "timestamp": 2000},
        headers=_headers(device),
    )
    current = client.get("/api/v1/stock", headers=_headers(device)).json()
    history = client.get("/api/v1/stock?view=history", headers=_headers(device)).json()
    assert current == []
    assert len(history) == 1 and history[0]["productId"] == test_uuid("p1")


def test_product_detail_merges_history_descending(api_client_with_device):
    client, device = api_client_with_device
    _purchase(client, device)
    client.post(
        "/api/v1/consumptions",
        json={"operationId": test_uuid("opC"), "id": test_uuid("c1"), "productId": test_uuid("p1"), "locationId": test_uuid("l1"), "stockKind": "STK", "quantity": 1, "timestamp": 2000},
        headers=_headers(device),
    )
    resp = client.get(f"/api/v1/products/{test_uuid('p1')}/detail", headers=_headers(device))
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalStk"] == 1
    assert [h["type"] for h in body["history"]] == ["CONSUMPTION", "PURCHASE"]
