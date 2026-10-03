import json
from datetime import datetime

import pytest

from tests.ids import test_uuid


@pytest.mark.parametrize("method", ["create", "update"])
def test_product_retry_ignores_new_provenance_timestamp(api_client_with_device, monkeypatch, method):
    from inventra_backend.resolver import provenance

    class ChangingClock(datetime):
        calls = 0

        @classmethod
        def utcnow(cls):
            cls.calls += 1
            return datetime(2026, 1, 1, 0, 0, cls.calls)

    monkeypatch.setattr(provenance, "datetime", ChangingClock)
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    product_id = test_uuid("retry-product")
    if method == "create":
        body = {"operationId": test_uuid("retry-create"), "id": product_id, "name": "Milch"}
        send = lambda: client.post("/api/v1/products", json=body, headers=headers)
        expected_status = 201
    else:
        created = client.post(
            "/api/v1/products",
            json={"operationId": test_uuid("setup-create"), "id": product_id, "name": "Milch"},
            headers=headers,
        )
        assert created.status_code == 201
        body = {"operationId": test_uuid("retry-update"), "name": "Milch neu", "version": 1}
        send = lambda: client.patch(f"/api/v1/products/{product_id}", json=body, headers=headers)
        expected_status = 200

    first = send()
    second = send()

    assert ChangingClock.calls >= (3 if method == "update" else 2)
    assert first.status_code == expected_status
    assert second.status_code == expected_status
    assert second.json() == first.json()
    assert first.json()["fieldProvenance"]["name"]["modifiedAt"]


@pytest.mark.parametrize("method", ["create", "update"])
def test_product_retry_rejects_changed_name(api_client_with_device, method):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    product_id = test_uuid("changed-product")
    if method == "create":
        body = {"operationId": test_uuid("changed-create"), "id": product_id, "name": "Milch"}
        send = lambda: client.post("/api/v1/products", json=body, headers=headers)
        expected_status = 201
    else:
        created = client.post(
            "/api/v1/products",
            json={"operationId": test_uuid("changed-setup"), "id": product_id, "name": "Milch"},
            headers=headers,
        )
        assert created.status_code == 201
        body = {"operationId": test_uuid("changed-update"), "name": "Milch neu", "version": 1}
        send = lambda: client.patch(f"/api/v1/products/{product_id}", json=body, headers=headers)
        expected_status = 200

    first = send()
    body["name"] = "Andere Milch"
    second = send()

    assert first.status_code == expected_status
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "OPERATION_ID_PAYLOAD_MISMATCH"


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
            "operationId": test_uuid("op1"), "id": product_id, "name": "Milch 1 l",
            "resolutionId": resolution_id,
        },
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Milch 1 l"
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


@pytest.fixture
def resolved_product(api_client_with_device, monkeypatch):
    from inventra_backend.resolver import resolver_service
    from inventra_backend.resolver.source_client import SourceCandidate, SourceResult

    async def fetch(barcode, settings):
        return {"off": SourceResult("off", "FOUND", SourceCandidate(
            "Milch", "Marke", "1 l", "https://example.com/milk.jpg", "Dairy", "Whole",
        ))}

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fetch)
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    resolved = client.post("/api/v1/products/resolve", json={"barcode": "4006381333931"}, headers=headers)
    assert resolved.status_code == 200
    resolution_id = resolved.json()["resolutionId"]
    created = client.post("/api/v1/products", headers=headers, json={
        "operationId": test_uuid("partial-create"), "id": test_uuid("partial-product"),
        "name": "Milch 1 l", "brand": "Marke", "quantityText": "1 l",
        "imageUrl": "https://example.com/milk.jpg", "category": "Dairy", "variant": "Whole",
        "minStock": 1, "contentUnitLabel": "Glass", "resolutionId": resolution_id,
    })
    assert created.status_code == 201
    return client, headers, created.json(), resolution_id


def _get_product(client, headers, product_id):
    response = client.get("/api/v1/products", headers=headers)
    assert response.status_code == 200
    return next(product for product in response.json() if product["id"] == product_id)


def test_partial_min_stock_preserves_other_values_and_retries(resolved_product):
    client, headers, before, _ = resolved_product
    body = {"operationId": test_uuid("partial-stock"), "version": 1, "minStock": 5}
    response = client.patch(f"/api/v1/products/{before['id']}", headers=headers, json=body)
    assert response.status_code == 200
    expected = {**before, "minStock": 5, "version": 2}
    assert response.json() == expected
    assert _get_product(client, headers, before["id"]) == expected
    retry = client.patch(f"/api/v1/products/{before['id']}", headers=headers, json=body)
    assert retry.status_code == 200
    assert retry.json() == expected
    # Explicit null must produce a different idempotency payload from omission.
    changed = client.patch(f"/api/v1/products/{before['id']}", headers=headers, json={**body, "brand": None})
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "OPERATION_ID_PAYLOAD_MISMATCH"


def test_partial_min_stock_preserves_resolver_provenance(resolved_product):
    client, headers, before, _ = resolved_product
    for field in ("name", "brand", "category", "variant", "quantity"):
        assert before["fieldProvenance"][field]["manual"] is False
        assert before["fieldProvenance"][field]["selectedSource"] == "off"
    provenance_bytes = json.dumps(before["fieldProvenance"]).encode()
    response = client.patch(f"/api/v1/products/{before['id']}", headers=headers, json={
        "operationId": test_uuid("partial-provenance"), "version": 1, "minStock": 5,
    })
    assert response.status_code == 200
    after = _get_product(client, headers, before["id"])
    assert json.dumps(after["fieldProvenance"]).encode() == provenance_bytes


def test_full_android_update_preserves_existing_behavior(resolved_product):
    client, headers, before, _ = resolved_product
    # Every field declared by ProductUpdateRequestDto, including nullable defaults.
    body = {
        "operationId": test_uuid("full-android"), "version": 1,
        "name": before["name"], "imageUrl": before["imageUrl"], "minStock": 5,
        "contentUnitLabel": before["contentUnitLabel"], "resolutionId": None,
        "quantity": before["quantity"], "unitId": before["unit"]["id"],
        "productQuantity": None, "productQuantityUnit": None, "quantityText": None,
        "brand": before["brand"], "variant": before["variant"], "category": before["category"],
    }
    response = client.patch(f"/api/v1/products/{before['id']}", headers=headers, json=body)
    assert response.status_code == 200
    after = response.json()
    assert {k: v for k, v in after.items() if k != "fieldProvenance"} == {
        k: v for k, v in {**before, "minStock": 5, "version": 2}.items() if k != "fieldProvenance"
    }
    assert set(after["fieldProvenance"]) == set(before["fieldProvenance"])
    for entry in after["fieldProvenance"].values():
        assert entry == {"selectedSource": "manual", "manual": True, "modifiedAt": entry["modifiedAt"]}
        assert entry["modifiedAt"]
    assert _get_product(client, headers, before["id"]) == after
    retry = client.patch(f"/api/v1/products/{before['id']}", headers=headers, json=body)
    assert retry.status_code == 200
    assert retry.json() == after


def test_partial_explicit_null_clears_brand(resolved_product):
    client, headers, before, _ = resolved_product
    response = client.patch(f"/api/v1/products/{before['id']}", headers=headers, json={
        "operationId": test_uuid("clear-brand"), "version": 1, "brand": None,
    })
    assert response.status_code == 200
    assert _get_product(client, headers, before["id"]) == {**before, "brand": None, "version": 2}


@pytest.mark.parametrize("fields, amount, abbreviation", [
    ({}, 1, "l"),
    ({"quantityText": None, "unitId": None}, None, None),
    ({"quantity": None, "unitId": None}, None, None),
    ({"quantityText": "500 g"}, 500, "g"),
    ({"productQuantity": 250, "productQuantityUnit": "g"}, 250, "g"),
])
def test_partial_package_size_omission_and_explicit_updates(resolved_product, fields, amount, abbreviation):
    client, headers, before, resolution_id = resolved_product
    response = client.patch(f"/api/v1/products/{before['id']}", headers=headers, json={
        "operationId": test_uuid("partial-package"), "version": 1, "minStock": 5,
        "resolutionId": resolution_id, **fields,
    })
    assert response.status_code == 200
    after = _get_product(client, headers, before["id"])
    assert after["quantity"] == amount
    assert (after["unit"]["abbreviation"] if after["unit"] else None) == abbreviation
    for field in ("name", "brand", "category", "variant", "imageUrl", "contentUnitLabel"):
        assert after[field] == before[field]
    if not fields:
        assert after["fieldProvenance"] == before["fieldProvenance"]
