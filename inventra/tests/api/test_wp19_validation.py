from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from inventra_backend.db.base import get_engine
from inventra_backend.schemas.events import (
    PurchaseCreateRequest, ConsumptionCreateRequest, CorrectionCreateRequest,
    RelocationCreateRequest, NewProductInfo,
)
from inventra_backend.schemas.products import ProductUpdateRequest
from tests.ids import test_uuid


def purchase():
    return dict(operationId=test_uuid("wp19-op"), id=test_uuid("wp19-event"),
                productId=test_uuid("wp19-product"), newProduct={"name": "Milk"},
                barcode="4001", locationId=test_uuid("wp19-location"), quantity=1, timestamp=0)


@pytest.mark.parametrize("field,value", [
    ("quantity", 0), ("quantity", -1), ("quantity", 1_000_001),
    ("barcode", " "), ("barcode", "a" * 65),
    ("pricePerUnitCents", -1), ("pricePerUnitCents", 100_000_001),
    ("minStock", -1), ("minStock", 1_000_001),
    ("mhd", ""), ("mhd", " "), ("mhd", "2025-02-29"),
    ("mhd", "20260201"), ("mhd", "2026-2-01"), ("mhd", "2026-01-01T00:00:00"),
    ("contentUnitLabel", " "), ("contentUnitLabel", "x" * 41),
    ("contentTotal", 0), ("contentTotal", -1), ("contentTotal", 100_000_001),
    ("timestamp", -1),
])
def test_invalid_purchase_schema(field, value):
    payload = purchase() | {"contentUnitLabel": "g", "contentTotal": 1, field: value}
    with pytest.raises(ValidationError):
        PurchaseCreateRequest.model_validate(payload)


@pytest.mark.parametrize("breakdown", ["", "0", "01", "1,0", "1, 2", "[1,2]", "1,", "1\n", "1" * 256])
def test_invalid_breakdown(breakdown):
    with pytest.raises(ValidationError):
        PurchaseCreateRequest.model_validate(purchase() | {
            "contentUnitLabel": "g", "contentTotal": 1, "contentBreakdown": breakdown})


@pytest.mark.parametrize("fields", [
    {"contentTotal": 1}, {"contentUnitLabel": "g"}, {"contentBreakdown": "250,250,500"},
])
def test_content_requires_pair(fields):
    with pytest.raises(ValidationError):
        PurchaseCreateRequest.model_validate(purchase() | fields)


def test_purchase_valid_boundaries_and_legacy_breakdown():
    model = PurchaseCreateRequest.model_validate(purchase() | dict(
        barcode=" 4001 ", quantity=1_000_000, pricePerUnitCents=100_000_000,
        minStock=1_000_000, mhd="2024-02-29", contentUnitLabel=" g ",
        contentTotal=100_000_000, contentBreakdown="250,250,500"))
    assert model.barcode == "4001" and model.content_unit_label == "g"
    assert PurchaseCreateRequest.model_validate(purchase()).mhd is None


@pytest.mark.parametrize("field,value", [("name", " "), ("name", "x" * 201),
    ("brand", "x" * 201), ("variant", "x" * 201), ("category", "x" * 201)])
def test_new_product_constraints(field, value):
    with pytest.raises(ValidationError):
        NewProductInfo.model_validate({"name": "Milk", field: value})
    assert NewProductInfo(name=" Milk ").name == "Milk"


@pytest.mark.parametrize("model", [ConsumptionCreateRequest, CorrectionCreateRequest, RelocationCreateRequest])
@pytest.mark.parametrize("fields", [{"stockKind": "OTHER"}, {"timestamp": -1}])
def test_other_event_constraints(model, fields):
    payload = event_payload(model) | fields
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def event_payload(model):
    payload = purchase() | dict(stockKind="STK", newQuantity=0,
                               fromLocationId=test_uuid("wp19-location"), toLocationId=test_uuid("other"))
    return payload


@pytest.mark.parametrize("model,fields", [
    (ConsumptionCreateRequest, {"quantity": 0}),
    (RelocationCreateRequest, {"quantity": -1}),
    (RelocationCreateRequest, {"toLocationId": test_uuid("wp19-location")}),
    (CorrectionCreateRequest, {"newQuantity": -1}),
    (CorrectionCreateRequest, {"mhdForIncrease": "2026-02-30"}),
    (CorrectionCreateRequest, {"mhdForIncrease": ""}),
    (CorrectionCreateRequest, {"contentUnitLabel": " "}),
    (CorrectionCreateRequest, {"contentUnitLabel": "x" * 41}),
])
def test_other_event_invalid_fields(model, fields):
    with pytest.raises(ValidationError):
        model.model_validate(event_payload(model) | fields)


@pytest.mark.parametrize("model", [ConsumptionCreateRequest, CorrectionCreateRequest, RelocationCreateRequest])
@pytest.mark.parametrize("kind", ["STK", "CONTENT"])
def test_valid_other_events(model, kind):
    assert model.model_validate(event_payload(model) | {"stockKind": kind}).stock_kind == kind


def test_patch_fields_set_preserved():
    model = ProductUpdateRequest(operationId=test_uuid("patch"), version=1, minStock=None)
    assert model.model_fields_set == {"operation_id", "version", "min_stock"}


@pytest.mark.parametrize("path,fields", [
    ("purchases", {"mhd": "bad"}), ("purchases", {"quantity": 0}),
    ("consumptions", {"stockKind": "OTHER"}),
    ("corrections", {"mhdForIncrease": "bad"}),
    ("relocations", {"quantity": 0}),
])
def test_http_validation_no_writes(api_client_with_device, path, fields):
    client, device = api_client_with_device
    payload = event_payload(RelocationCreateRequest) | fields
    response = client.post("/api/v1/" + path, json=payload,
                           headers={"Authorization": f"Bearer {device.token}"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_no_purchase_rows()


def assert_no_purchase_rows():
    with get_engine().connect() as conn:
        for table in ["products", "barcodes", "purchase_events", "batches", "change_log", "processed_operations"]:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


@pytest.mark.parametrize("entity,deleted", [("locations", False), ("locations", True),
                                           ("stores", False), ("stores", True)])
def test_purchase_reference_rejected_before_writes(api_client_with_device, entity, deleted):
    client, device = api_client_with_device
    payload = purchase()
    if entity == "stores":
        payload["storeId"] = test_uuid("wp19-store")
    with get_engine().begin() as conn:
        if entity == "stores":
            conn.execute(text("INSERT INTO locations (id,name,normalized_name,version) VALUES (:id,'Kitchen','kitchen',1)"),
                         {"id": payload["locationId"]})
        if deleted:
            ident = payload["locationId"] if entity == "locations" else payload["storeId"]
            conn.execute(text(f"INSERT INTO {entity} (id,name,normalized_name,version,deleted_at) VALUES (:id,'Old','old',1,:deleted)"),
                         {"id": ident, "deleted": datetime.now()})
    response = client.post("/api/v1/purchases", json=payload,
                           headers={"Authorization": f"Bearer {device.token}"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ("LOCATION_NOT_FOUND" if entity == "locations" else "STORE_NOT_FOUND")
    assert_no_purchase_rows()


def test_replay_returns_snapshot_after_reference_soft_delete(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    payload = purchase()
    client.post("/api/v1/locations", json={"operationId": test_uuid("loc-op"),
                "id": payload["locationId"], "name": "Kitchen"}, headers=headers)
    first = client.post("/api/v1/purchases", json=payload, headers=headers)
    assert first.status_code == 201
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE locations SET deleted_at=:deleted"), {"deleted": datetime.now()})
        conn.execute(text("UPDATE products SET deleted_at=:deleted"), {"deleted": datetime.now()})
    replay = client.post("/api/v1/purchases", json=payload, headers=headers)
    assert replay.status_code == 201 and replay.json() == first.json()


@pytest.mark.parametrize("module,prefix", [("locations", "Location"), ("stores", "Store")])
@pytest.mark.parametrize("suffix", ["CreateRequest", "UpdateRequest"])
@pytest.mark.parametrize("name", [" ", "x" * 201])
def test_master_names(module, prefix, suffix, name):
    from importlib import import_module
    model = getattr(import_module("inventra_backend.schemas." + module), prefix + suffix)
    with pytest.raises(ValidationError):
        model.model_validate({"operationId": test_uuid("op"), "id": test_uuid("id"), "version": 1, "name": name})


@pytest.mark.parametrize("fields", [{"name": " "}, {"minStock": -1}, {"quantity": -1},
    {"quantity": float("inf")}, {"productQuantity": -1}, {"contentUnitLabel": " "},
    {"brand": "x" * 201}, {"imageUrl": "x" * 1025}])
def test_product_write_constraints(fields):
    from inventra_backend.schemas.products import ProductCreateRequest
    for model in [ProductCreateRequest, ProductUpdateRequest]:
        with pytest.raises(ValidationError):
            model.model_validate({"operationId": test_uuid("op"), "id": test_uuid("id"), "version": 1, "name": "Milk"} | fields)


@pytest.mark.parametrize("code", [" ", "x" * 65])
def test_barcode_constraints(code):
    from inventra_backend.schemas.barcodes import BarcodeAssignRequest
    with pytest.raises(ValidationError):
        BarcodeAssignRequest(operationId=test_uuid("op"), productId=test_uuid("p"), code=code)


@pytest.mark.parametrize("fields", [{"name": " "}, {"name": "x" * 256},
                                   {"abbreviation": " "}, {"abbreviation": "x" * 33}])
def test_unit_constraints(fields):
    from inventra_backend.schemas.units import UnitCreateRequest, UnitUpdateRequest
    for model in [UnitCreateRequest, UnitUpdateRequest]:
        with pytest.raises(ValidationError):
            model.model_validate({"name": "Gram", "abbreviation": "g"} | fields)
