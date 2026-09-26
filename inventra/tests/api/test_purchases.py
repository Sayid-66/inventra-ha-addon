import uuid
from sqlalchemy import text

from inventra_backend.db.base import get_engine

from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def test_purchase_new_product_creates_product_barcode_event_and_batch(api_client_with_device):
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    product_id = test_uuid("p1")
    resp = client.post(
        "/api/v1/locations", json={"operationId": test_uuid("op0"), "id": location_id, "name": "Keller"}, headers=_headers(device),
    )
    assert resp.status_code == 201

    resp = client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("op1"), "id": test_uuid("e1"), "productId": product_id,
            "newProduct": {"name": "Milch", "imageUrl": None},
            "barcode": "4001", "locationId": location_id, "quantity": 2,
            "storeId": None, "pricePerUnitCents": None, "mhd": None,
            "minStock": None, "contentUnitLabel": None, "contentTotal": None,
            "contentBreakdown": None, "timestamp": 1000,
        },
        headers=_headers(device),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["productId"] == product_id
    assert body["quantity"] == 2

    products = client.get("/api/v1/products", headers=_headers(device)).json()
    assert any(p["id"] == product_id and p["name"] == "Milch" for p in products)

    barcodes = client.post(
        "/api/v1/barcodes", json={"operationId": test_uuid("opX"), "code": "4001", "productId": product_id}, headers=_headers(device),
    )
    assert barcodes.status_code == 409  # already assigned by the purchase — proves the barcode row exists


def test_manual_purchase_persists_fields_and_resolves_barcode_locally(api_client_with_device):
    client, device = api_client_with_device
    headers = _headers(device)
    product_id = test_uuid("manual-product")
    location_id = test_uuid("manual-location")
    barcode = "2990000000123"
    assert client.post(
        "/api/v1/locations",
        json={"operationId": test_uuid("manual-location-op"), "id": location_id, "name": "Keller"},
        headers=headers,
    ).status_code == 201

    response = client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("manual-purchase-op"), "id": test_uuid("manual-event"),
            "productId": product_id,
            "newProduct": {"name": "Haferdrink", "brand": "Nord", "variant": "Barista", "category": "Getränke"},
            "barcode": barcode, "locationId": location_id, "quantity": 2,
            "minStock": 3, "timestamp": 1000,
        },
        headers=headers,
    )
    assert response.status_code == 201

    with get_engine().connect() as connection:
        product = connection.execute(text(
            "SELECT name, brand, variant, category, min_stock FROM products WHERE id = :id"
        ), {"id": product_id}).one()
        linked_id = connection.execute(text(
            "SELECT product_id FROM barcodes WHERE code = :code AND deleted_at IS NULL"
        ), {"code": barcode}).scalar_one()
    assert tuple(product) == ("Haferdrink", "Nord", "Barista", "Getränke", 3)
    assert linked_id == product_id

    resolved = client.post("/api/v1/products/resolve", json={"barcode": barcode}, headers=headers)
    assert resolved.status_code == 200
    assert resolved.json()["matchedLocally"] is True
    assert resolved.json()["product"]["id"] == product_id


def test_purchase_replay_same_operation_id_is_safe_noop(api_client_with_device):
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("op0"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    payload = {
        "operationId": test_uuid("op1"), "id": test_uuid("e1"), "productId": test_uuid("p1"),
        "newProduct": {"name": "Milch", "imageUrl": None},
        "barcode": "4001", "locationId": location_id, "quantity": 2,
        "storeId": None, "pricePerUnitCents": None, "mhd": None,
        "minStock": None, "contentUnitLabel": None, "contentTotal": None,
        "contentBreakdown": None, "timestamp": 1000,
    }
    first = client.post("/api/v1/purchases", json=payload, headers=_headers(device))
    second = client.post("/api/v1/purchases", json=payload, headers=_headers(device))
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    products = client.get("/api/v1/products", headers=_headers(device)).json()
    assert len(products) == 1  # not created twice


def test_commit_purchase_rejects_tombstoned_product(api_client_with_device):
    client, device = api_client_with_device
    product_id = test_uuid("p1")
    location_id = test_uuid("l1")
    headers = _headers(device)
    client.post(
        "/api/v1/products",
        json={"operationId": test_uuid("op-create"), "id": product_id, "name": "Milch"},
        headers=headers,
    )
    client.post(
        "/api/v1/locations",
        json={"operationId": test_uuid("op-location"), "id": location_id, "name": "Keller"},
        headers=headers,
    )
    client.request(
        "DELETE",
        f"/api/v1/products/{product_id}",
        json={"operationId": test_uuid("op-delete"), "version": 1},
        headers=headers,
    )

    resp = client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("op-purchase"), "id": test_uuid("e1"), "productId": product_id,
            "newProduct": None, "barcode": "4001", "locationId": location_id, "quantity": 2,
            "storeId": None, "pricePerUnitCents": None, "mhd": None,
            "minStock": None, "contentUnitLabel": None, "contentTotal": None,
            "contentBreakdown": None, "timestamp": 1000,
        },
        headers=headers,
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


def _resolve_name(client, device, monkeypatch, name):
    from inventra_backend.resolver import resolver_service
    from inventra_backend.resolver.source_client import SourceCandidate, SourceResult

    async def fake_fetch_all_sources(barcode, settings):
        return {
            "off": SourceResult("off", "FOUND", SourceCandidate(name, "Marke", "1 l", None, None, None)),
            "obf": SourceResult("obf", "NOT_FOUND", None),
            "opff": SourceResult("opff", "NOT_FOUND", None),
            "opf": SourceResult("opf", "NOT_FOUND", None),
        }

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fake_fetch_all_sources)
    response = client.post(
        "/api/v1/products/resolve",
        json={"barcode": "4006381333931"},
        headers=_headers(device),
    )
    assert response.status_code == 200
    return response.json()["resolutionId"]


def _purchase_new_product(client, device, name, resolution_id=None):
    location_id = test_uuid("provenance-location")
    location_response = client.post(
        "/api/v1/locations",
        json={"operationId": test_uuid("provenance-location-op"), "id": location_id, "name": "Keller"},
        headers=_headers(device),
    )
    assert location_response.status_code == 201

    new_product = {"name": name, "imageUrl": None}
    if resolution_id is not None:
        new_product["resolutionId"] = resolution_id

    product_id = test_uuid("provenance-product")
    response = client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("provenance-purchase-op"),
            "id": test_uuid("provenance-event"),
            "productId": product_id,
            "newProduct": new_product,
            "barcode": "4001",
            "locationId": location_id,
            "quantity": 2,
            "storeId": None,
            "pricePerUnitCents": None,
            "mhd": None,
            "minStock": None,
            "contentUnitLabel": None,
            "contentTotal": None,
            "contentBreakdown": None,
            "timestamp": 1000,
        },
        headers=_headers(device),
    )
    assert response.status_code == 201

    products_response = client.get("/api/v1/products", headers=_headers(device))
    assert products_response.status_code == 200
    return next(product for product in products_response.json() if product["id"] == product_id)


def test_purchase_new_product_with_matching_resolution_credits_resolver_provenance(
    api_client_with_device, monkeypatch,
):
    client, device = api_client_with_device
    resolution_id = _resolve_name(client, device, monkeypatch, "Milch")

    product = _purchase_new_product(client, device, "Milch", resolution_id)

    assert product["fieldProvenance"]["name"]["manual"] is False
    assert product["fieldProvenance"]["name"]["selectedSource"] == "off"


def test_purchase_new_product_with_edited_resolved_name_is_manual(api_client_with_device, monkeypatch):
    client, device = api_client_with_device
    resolution_id = _resolve_name(client, device, monkeypatch, "Milch")

    product = _purchase_new_product(client, device, "Haferdrink", resolution_id)

    assert product["fieldProvenance"]["name"]["manual"] is True


def test_purchase_new_product_without_resolution_id_is_manual(api_client_with_device):
    client, device = api_client_with_device

    product = _purchase_new_product(client, device, "Handgetippt")

    assert product["fieldProvenance"]["name"]["manual"] is True


def test_purchase_new_product_with_unknown_resolution_id_is_manual(api_client_with_device):
    client, device = api_client_with_device

    product = _purchase_new_product(client, device, "Handgetippt", str(uuid.uuid4()))

    assert product["fieldProvenance"]["name"]["manual"] is True
