from datetime import date

from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def test_mhd_warning_level_thresholds():
    from inventra_backend.services.mhd_service import mhd_warning_level

    today = date(2026, 9, 4)
    assert mhd_warning_level(None, today) == "NONE"
    assert mhd_warning_level("2026-09-06", today) == "RED"   # 2 days
    assert mhd_warning_level("2026-09-10", today) == "YELLOW"  # 6 days
    assert mhd_warning_level("2026-09-20", today) == "NONE"


def test_ack_state_starts_empty_and_records_current_signature(api_client_with_device):
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    resp = client.get("/api/v1/mhd-warning-state", headers=_headers(device))
    assert resp.json() == {"acknowledgedSignature": ""}

    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("opP"), "id": test_uuid("e1"), "productId": product_id, "newProduct": {"name": "Milch", "imageUrl": None},
            "barcode": "4001", "locationId": location_id, "quantity": 1, "storeId": None, "pricePerUnitCents": None,
            "mhd": "2020-01-01", "minStock": None, "contentUnitLabel": None, "contentTotal": None,
            "contentBreakdown": None, "timestamp": 1000,
        },
        headers=_headers(device),
    )
    ack_resp = client.post("/api/v1/mhd-warning-state/ack", json={"operationId": test_uuid("opAck")}, headers=_headers(device))
    assert ack_resp.status_code == 200
    assert ack_resp.json()["acknowledgedSignature"] == f"{product_id}:2020-01-01"
