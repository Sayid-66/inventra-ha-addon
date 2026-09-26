from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def _setup_product(client, headers, product_id, name, location_id, quantity, content_unit_label=None):
    client.post("/api/v1/locations", json={"operationId": test_uuid("op-loc-" + location_id), "id": location_id, "name": "Küche"}, headers=headers)
    client.post("/api/v1/purchases", json={
        "operationId": test_uuid("op-p-" + product_id), "id": test_uuid("e-" + product_id), "productId": product_id,
        "newProduct": {"name": name, "imageUrl": None}, "barcode": f"bc-{product_id}", "locationId": location_id,
        "quantity": quantity, "storeId": None, "pricePerUnitCents": None, "mhd": None, "minStock": None,
        "contentUnitLabel": content_unit_label, "contentTotal": quantity if content_unit_label else None,
        "contentBreakdown": None, "timestamp": 1000,
    }, headers=headers)


def test_by_name_returns_404_when_no_product_matches(api_client_with_device):
    client, device = api_client_with_device
    resp = client.post(
        "/api/v1/consumptions/by-name",
        json={"productName": "Nichtvorhanden", "quantity": 1, "operationId": test_uuid("opx")},
        headers=_headers(device),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PRODUCT_NAME_NOT_FOUND"


def test_by_name_returns_409_when_multiple_products_match(api_client_with_device):
    client, device = api_client_with_device
    headers = _headers(device)
    location_id = test_uuid("l1")
    _setup_product(client, headers, test_uuid("p1"), "Wasser", location_id, 3)
    _setup_product(client, headers, test_uuid("p2"), "Wasser", location_id, 2)
    resp = client.post(
        "/api/v1/consumptions/by-name",
        json={"productName": "Wasser", "quantity": 1, "operationId": test_uuid("opy")},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "AMBIGUOUS_PRODUCT_NAME"
    assert len(resp.json()["candidates"]) == 2


def test_by_name_returns_422_when_no_default_location_configured(api_client_with_device):
    client, device = api_client_with_device
    headers = _headers(device)
    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    _setup_product(client, headers, product_id, "Wasser", location_id, 5)
    resp = client.post(
        "/api/v1/consumptions/by-name",
        json={"productName": "Wasser", "quantity": 1, "operationId": test_uuid("opz")},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "NO_DEFAULT_LOCATION_CONFIGURED"


def test_by_name_rejects_content_tracked_products(api_client_with_device):
    client, device = api_client_with_device
    headers = _headers(device)
    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    _setup_product(client, headers, product_id, "Olivenoel", location_id, 500, content_unit_label="ml")
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import Device as DeviceModel
    from sqlalchemy.orm import Session
    with Session(get_engine()) as db:
        d = db.get(DeviceModel, device.device_id)
        d.default_location_id = location_id
        db.commit()

    resp = client.post(
        "/api/v1/consumptions/by-name",
        json={"productName": "Olivenoel", "quantity": 1, "operationId": test_uuid("opw")},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNIT_NOT_SUPPORTED_VIA_VOICE"


def test_by_name_succeeds_with_default_location_and_triggers_reconcile(api_client_with_device, monkeypatch):
    client, device = api_client_with_device
    headers = _headers(device)
    calls = []
    async def _fake_schedule(product_id, increased):
        calls.append((product_id, increased))
    monkeypatch.setattr("inventra_backend.api.consume_by_name.bring_service.schedule_stock_change", _fake_schedule)

    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    _setup_product(client, headers, product_id, "Wasser", location_id, 5)
    calls.clear()
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import Device as DeviceModel
    from sqlalchemy.orm import Session
    with Session(get_engine()) as db:
        d = db.get(DeviceModel, device.device_id)
        d.default_location_id = location_id
        db.commit()

    resp = client.post(
        "/api/v1/consumptions/by-name",
        json={"productName": "Wasser", "quantity": 2, "operationId": test_uuid("opok")},
        headers=headers,
    )

    assert resp.status_code == 201
    assert resp.json()["quantity"] == 2
    assert calls == [(product_id, False)]


def test_by_name_replay_same_operation_id_is_safe_noop(api_client_with_device, monkeypatch):
    client, device = api_client_with_device
    headers = _headers(device)
    async def _fake_schedule(product_id, increased):
        pass
    monkeypatch.setattr("inventra_backend.api.consume_by_name.bring_service.schedule_stock_change", _fake_schedule)

    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    _setup_product(client, headers, product_id, "Wasser", location_id, 5)
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import Device as DeviceModel
    from sqlalchemy.orm import Session
    with Session(get_engine()) as db:
        d = db.get(DeviceModel, device.device_id)
        d.default_location_id = location_id
        db.commit()

    payload = {"productName": "Wasser", "quantity": 2, "operationId": test_uuid("op-replay")}
    first = client.post("/api/v1/consumptions/by-name", json=payload, headers=headers)
    second = client.post("/api/v1/consumptions/by-name", json=payload, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    stock = client.get("/api/v1/stock", headers=headers).json()
    assert stock[0]["totalStk"] == 3


def test_by_name_insufficient_stock_returns_422(api_client_with_device):
    client, device = api_client_with_device
    headers = _headers(device)
    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    _setup_product(client, headers, product_id, "Wasser", location_id, 1)
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import Device as DeviceModel
    from sqlalchemy.orm import Session
    with Session(get_engine()) as db:
        d = db.get(DeviceModel, device.device_id)
        d.default_location_id = location_id
        db.commit()

    resp = client.post(
        "/api/v1/consumptions/by-name",
        json={"productName": "Wasser", "quantity": 5, "operationId": test_uuid("opins")},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INSUFFICIENT_STOCK"
