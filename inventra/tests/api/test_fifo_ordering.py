from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def test_consume_depletes_earliest_mhd_batch_first(api_client_with_device):
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("opP1"), "id": test_uuid("e1"), "productId": product_id, "newProduct": {"name": "Milch", "imageUrl": None},
            "barcode": "4001", "locationId": location_id, "quantity": 2, "storeId": None, "pricePerUnitCents": None,
            "mhd": "2026-09-20", "minStock": None, "contentUnitLabel": None, "contentTotal": None,
            "contentBreakdown": None, "timestamp": 1000,
        },
        headers=_headers(device),
    )
    client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("opP2"), "id": test_uuid("e2"), "productId": product_id, "newProduct": None,
            "barcode": "4001", "locationId": location_id, "quantity": 2, "storeId": None, "pricePerUnitCents": None,
            "mhd": "2026-09-10", "minStock": None, "contentUnitLabel": None, "contentTotal": None,
            "contentBreakdown": None, "timestamp": 2000,
        },
        headers=_headers(device),
    )
    client.post(
        "/api/v1/consumptions",
        json={"operationId": test_uuid("opC1"), "id": test_uuid("c1"), "productId": product_id, "locationId": location_id, "stockKind": "STK", "quantity": 2, "timestamp": 3000},
        headers=_headers(device),
    )
    detail = client.get(f"/api/v1/products/{product_id}/detail", headers=_headers(device)).json()
    # only the later-MHD (2026-09-20) batch should remain — the earlier-MHD batch was consumed first
    assert detail["nextMhd"] == "2026-09-20"
