from tests.ids import test_uuid


def test_units_catalog_and_product_reference(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    units = client.get("/api/v1/units", headers=headers)
    assert units.status_code == 200
    assert {u["abbreviation"] for u in units.json()} == {
        "g", "kg", "ml", "l", "Stk.", "Pkg.", "Rolle", "Blatt", "Portion", "Tabl.", "Kaps.", "Beutel",
    }
    assert all(u["isStandard"] for u in units.json())
    response = client.post("/api/v1/units", headers=headers, json={"name": "Dose", "abbreviation": "Ds."})
    assert response.status_code == 201
    unit = response.json()
    assert not unit["isStandard"]
    assert client.post("/api/v1/units", headers=headers, json={"name": "Duplicate", "abbreviation": "DS."}).status_code == 409
    product_id = test_uuid("unit-product")
    payload = {"id": product_id, "operationId": test_uuid("unit-create"), "name": "Soup", "quantity": 2, "unitId": unit["id"]}
    created = client.post("/api/v1/products", headers=headers, json=payload)
    assert created.status_code == 201
    assert created.json()["unit"] == {k: unit[k] for k in ("id", "name", "abbreviation")}
    renamed = client.patch(f"/api/v1/units/{unit['id']}", headers=headers, json={"name": "Dosen", "abbreviation": "Dose"})
    assert renamed.status_code == 200
    product = client.get("/api/v1/products", headers=headers).json()[0]
    assert product["unit"]["name"] == "Dosen"
    assert product["unit"]["abbreviation"] == "Dose"
    assert product["version"] == 1
    assert client.patch(f"/api/v1/units/{unit['id']}", headers=headers, json={"abbreviation": "G"}).status_code == 409
    assert client.delete(f"/api/v1/units/{unit['id']}", headers=headers).status_code == 405
    payload.update(id=test_uuid("bad-unit-product"), operationId=test_uuid("bad-unit-create"), unitId=test_uuid("missing"))
    invalid = client.post("/api/v1/products", headers=headers, json=payload)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "UNKNOWN_UNIT"
    updated = client.patch(f"/api/v1/products/{product_id}", headers=headers, json={
        "operationId": test_uuid("unit-update"), "name": "Soup", "version": 1, "quantity": None, "unitId": None,
    })
    assert updated.status_code == 200
    assert updated.json()["unit"] is None


def test_raw_off_package_size(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    for label, fields, amount, abbreviation in [
        ("raw", {"quantityText": "6x125g"}, 750, "g"),
        ("structured", {"productQuantity": 1.5, "productQuantityUnit": "LITER", "quantityText": "wrong"}, 1.5, "l"),
        ("unknown", {"quantityText": "12 flurbs"}, 12, None),
    ]:
        result = client.post("/api/v1/products", headers=headers, json={
            "id": test_uuid(label), "operationId": test_uuid(label + "op"), "name": label, **fields,
        })
        assert result.status_code == 201
        assert result.json()["quantity"] == amount
        assert (result.json()["unit"]["abbreviation"] if result.json()["unit"] else None) == abbreviation


def test_units_validation_and_auth(api_client_with_device):
    client, device = api_client_with_device
    assert client.get("/api/v1/units").status_code == 401
    headers = {"Authorization": f"Bearer {device.token}"}
    for body in ({"name": " ", "abbreviation": "g"}, {"name": "Test", "abbreviation": " "}):
        assert client.post("/api/v1/units", headers=headers, json=body).status_code == 422
    assert client.patch("/api/v1/units/missing", headers=headers, json={"name": "New"}).status_code == 404


def test_normalization_after_standard_unit_rename(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    gram = next(u for u in client.get("/api/v1/units", headers=headers).json() if u["abbreviation"] == "g")
    assert client.patch(f"/api/v1/units/{gram['id']}", headers=headers, json={"name": "Gram", "abbreviation": "gr"}).status_code == 200
    result = client.post("/api/v1/products", headers=headers, json={
        "id": test_uuid("renamed-normalization"), "operationId": test_uuid("renamed-op"),
        "name": "Flour", "quantityText": "400 gramm",
    })
    assert result.status_code == 201
    assert result.json()["quantity"] == 400
    assert result.json()["unit"] == {"id": gram["id"], "name": "Gram", "abbreviation": "gr"}


def test_resolver_quantity_is_normalized_on_create(api_client_with_device, monkeypatch):
    from inventra_backend.resolver import resolver_service
    from inventra_backend.resolver.source_client import SourceCandidate, SourceResult

    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}

    async def fetch(barcode, settings):
        return {"off": SourceResult("off", "FOUND", SourceCandidate("Yoghurt", None, "6x125g", None, None, None))}

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fetch)
    resolved = client.post("/api/v1/products/resolve", headers=headers, json={"barcode": "4006381333931"})
    assert resolved.status_code == 200
    result = client.post("/api/v1/products", headers=headers, json={
        "id": test_uuid("resolved-size"), "operationId": test_uuid("resolved-size-op"),
        "name": "Yoghurt", "resolutionId": resolved.json()["resolutionId"],
    })
    assert result.status_code == 201
    assert result.json()["quantity"] == 750
    assert result.json()["unit"]["abbreviation"] == "g"
    assert result.json()["fieldProvenance"]["quantity"]["selectedSource"] == "off"
    assert result.json()["fieldProvenance"]["quantity"]["manual"] is False
    updated = client.patch(f"/api/v1/products/{result.json()['id']}", headers=headers, json={
        "operationId": test_uuid("resolved-size-update"), "name": "Yoghurt", "version": 1,
        "resolutionId": resolved.json()["resolutionId"], "quantityText": "1,5 l",
    })
    assert updated.status_code == 200
    assert updated.json()["quantity"] == 1.5
    assert updated.json()["unit"]["abbreviation"] == "l"
