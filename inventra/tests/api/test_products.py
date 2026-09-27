import json

from tests.ids import test_uuid


def test_create_product_minimal(api_client_with_device):
    client, device = api_client_with_device
    product_id = test_uuid("p1")
    resp = client.post(
        "/api/v1/products",
        json={"operationId": test_uuid("op1"), "id": product_id, "name": "Milch"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == product_id
    assert body["name"] == "Milch"
    assert body["imageUrl"] is None
    assert body["minStock"] is None
    assert body["contentUnitLabel"] is None
    assert body["version"] == 1
    assert body["deletedAt"] is None
    assert body["brand"] is None
    assert body["quantity"] is None
    assert body["unit"] is None
    assert body["category"] is None
    assert body["variant"] is None
    assert body["fieldProvenance"]["name"]["manual"] is True
    assert body["fieldProvenance"]["name"]["selectedSource"] == "manual"


def test_update_product_min_stock_bumps_version(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    product_id = test_uuid("p1")
    client.post("/api/v1/products", json={"operationId": test_uuid("op1"), "id": product_id, "name": "Milch"}, headers=headers)
    resp = client.patch(
        f"/api/v1/products/{product_id}",
        json={"operationId": test_uuid("op2"), "name": "Milch", "imageUrl": None, "minStock": 2, "contentUnitLabel": None, "version": 1},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["minStock"] == 2
    assert resp.json()["version"] == 2


def test_update_product_stale_version_returns_409(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    product_id = test_uuid("p1")
    client.post("/api/v1/products", json={"operationId": test_uuid("op1"), "id": product_id, "name": "Milch"}, headers=headers)
    client.patch(
        f"/api/v1/products/{product_id}",
        json={"operationId": test_uuid("op2"), "name": "Milch", "imageUrl": None, "minStock": 2, "contentUnitLabel": None, "version": 1},
        headers=headers,
    )
    resp = client.patch(
        f"/api/v1/products/{product_id}",
        json={"operationId": test_uuid("op3"), "name": "Milch", "imageUrl": None, "minStock": 5, "contentUnitLabel": None, "version": 1},
        headers=headers,
    )
    assert resp.status_code == 409


def test_create_product_with_resolution_id_credits_resolver_provenance(api_client_with_device, monkeypatch):
    client, device = api_client_with_device
    from inventra_backend.resolver import resolver_service
    from inventra_backend.resolver.source_client import SourceResult, SourceCandidate

    async def fake_fetch_all_sources(barcode, settings):
        return {
            "off": SourceResult("off", "FOUND", SourceCandidate("Milch", "Marke", "1 l", None, None, None)),
            "obf": SourceResult("obf", "NOT_FOUND", None),
            "opff": SourceResult("opff", "NOT_FOUND", None),
            "opf": SourceResult("opf", "NOT_FOUND", None),
        }
    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fake_fetch_all_sources)

    resolve_resp = client.post(
        "/api/v1/products/resolve", json={"barcode": "4006381333931"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    resolution_id = resolve_resp.json()["resolutionId"]

    product_id = test_uuid("p1")
    resp = client.post(
        "/api/v1/products",
        json={
            "operationId": test_uuid("op1"), "id": product_id, "name": "Milch",
            "resolutionId": resolution_id,
        },
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["fieldProvenance"]["name"]["manual"] is False
    assert body["fieldProvenance"]["name"]["selectedSource"] == "off"


def test_create_product_without_resolution_id_is_always_manual(api_client_with_device):
    client, device = api_client_with_device
    product_id = test_uuid("p2")
    resp = client.post(
        "/api/v1/products",
        json={"operationId": test_uuid("op2"), "id": product_id, "name": "Handgetippt"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["fieldProvenance"]["name"]["manual"] is True
