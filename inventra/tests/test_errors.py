from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from inventra_backend.errors import (
    AmbiguousProductNameError,
    DuplicateEntityError,
    ProductNameNotFoundError,
    StaleVersionError,
    install_error_handlers,
)
from inventra_backend.idempotency.operations import OperationPayloadMismatch


def _app_raising(exc):
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_product_name_not_found_maps_to_404():
    client = _app_raising(ProductNameNotFoundError("Wasser"))
    resp = client.get("/boom")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PRODUCT_NAME_NOT_FOUND"


def test_ambiguous_product_name_maps_to_409_with_candidates():
    client = _app_raising(AmbiguousProductNameError("Wasser", [{"id": "p1", "name": "Wasser"}, {"id": "p2", "name": "Wasser"}]))
    resp = client.get("/boom")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "AMBIGUOUS_PRODUCT_NAME"
    assert len(body["candidates"]) == 2


def test_stale_version_returns_409_with_current_state():
    client = _app_raising(StaleVersionError("Location", "l1", {"id": "l1", "version": 6, "name": "Vorratskeller"}))
    resp = client.get("/boom")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "STALE_VERSION"
    assert body["current"] == {"id": "l1", "version": 6, "name": "Vorratskeller"}


def test_duplicate_entity_returns_409():
    client = _app_raising(DuplicateEntityError("Location", "normalizedName", "keller"))
    resp = client.get("/boom")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_ENTITY"


def test_operation_payload_mismatch_returns_409():
    client = _app_raising(OperationPayloadMismatch("op1"))
    resp = client.get("/boom")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "OPERATION_ID_PAYLOAD_MISMATCH"


def test_request_validation_error_uses_error_envelope(api_client_with_device):
    client, device = api_client_with_device
    resp = client.post(
        "/api/v1/products", json={"name": "Milch"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(resp.json()["error"]["message"], str)
    assert resp.json()["error"]["message"]


def test_http_exception_uses_error_envelope_and_status():
    resp = _app_raising(HTTPException(status_code=418, detail="teapot unavailable")).get("/boom")
    assert resp.status_code == 418
    assert resp.json() == {"error": {"code": "HTTP_ERROR", "message": "teapot unavailable"}}


def test_unhandled_exception_uses_safe_error_envelope():
    resp = _app_raising(RuntimeError("secret internal detail")).get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}}
