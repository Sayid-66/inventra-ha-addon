from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def test_events_stream_merges_all_four_types_chronologically(api_client_with_device):
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    event_id = test_uuid("e1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("opP"), "id": event_id, "productId": product_id, "newProduct": {"name": "Milch", "imageUrl": None},
            "barcode": "4001", "locationId": location_id, "quantity": 2, "storeId": None, "pricePerUnitCents": None,
            "mhd": None, "minStock": None, "contentUnitLabel": None, "contentTotal": None,
            "contentBreakdown": None, "timestamp": 1000,
        },
        headers=_headers(device),
    )
    client.post(
        "/api/v1/consumptions",
        json={"operationId": test_uuid("opC"), "id": test_uuid("c1"), "productId": product_id, "locationId": location_id, "stockKind": "STK", "quantity": 1, "timestamp": 2000},
        headers=_headers(device),
    )
    resp = client.get(f"/api/v1/events?productId={product_id}", headers=_headers(device))
    assert resp.status_code == 200
    body = resp.json()
    assert [e["type"] for e in body] == ["PURCHASE", "CONSUMPTION"]
    assert body[0]["eventId"] == event_id
    assert body[0]["payload"]["barcode"] == "4001"
    assert body[1]["payload"]["quantity"] == 1
