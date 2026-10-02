import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.ids import test_uuid


def test_pairing_page_contains_generated_code_and_qr(ingress_client):
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import PairingCode

    response = ingress_client.get("/pairing", headers={"X-Remote-User-Id": "dennis"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    with Session(get_engine()) as db:
        generated = db.scalars(select(PairingCode).order_by(PairingCode.created_at.desc())).first()
    assert generated is not None
    assert generated.code in response.text
    assert '<img src="data:image/svg+xml' in response.text


def test_pairing_page_is_not_registered_in_api_zone(api_client):
    response = api_client.get("/pairing", headers={"X-Remote-User-Id": "dennis"})

    assert response.status_code == 404


@pytest.mark.anyio
async def test_pairing_page_rejects_non_ingress_source(ingress_client):
    from inventra_backend.main import create_app

    ingress_app = create_app("ingress")
    transport = httpx.ASGITransport(app=ingress_app, client=("172.30.32.2", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/pairing", headers={"X-Remote-User-Id": "dennis"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_INGRESS_SOURCE"


def test_pairing_page_requires_ingress_identity(ingress_client):
    response = ingress_client.get("/pairing")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_full_pairing_flow(tmp_path, api_client):
    from inventra_backend.main import create_app

    ingress_app = create_app("ingress")
    transport = httpx.ASGITransport(app=ingress_app, client=("172.30.32.2", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ingress_http:
        create_resp = await ingress_http.post(
            "/internal/pairing-codes", headers={"X-Remote-User-Id": "dennis"},
        )
    assert create_resp.status_code == 201
    code = create_resp.json()["code"]

    exchange = api_client.post(
        "/api/v1/pair", json={"operationId": test_uuid("opPair1"), "code": code, "deviceName": "Dennis Pixel"},
    )
    assert exchange.status_code == 201
    token = exchange.json()["token"]
    assert exchange.json()["deviceId"]

    protected = api_client.get("/api/v1/locations", headers={"Authorization": f"Bearer {token}"})
    assert protected.status_code == 200


def test_pairing_code_single_use(api_client):
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import PairingCode
    from datetime import datetime, timedelta
    from sqlalchemy.orm import Session

    with Session(get_engine()) as db:
        db.add(PairingCode(code="fixedcode", user_id="dennis", expires_at=datetime.utcnow() + timedelta(minutes=5)))
        db.commit()

    first = api_client.post("/api/v1/pair", json={"operationId": test_uuid("op1"), "code": "fixedcode", "deviceName": "A"})
    assert first.status_code == 201
    second = api_client.post("/api/v1/pair", json={"operationId": test_uuid("op2"), "code": "fixedcode", "deviceName": "B"})
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "PAIRING_CODE_ALREADY_USED"


def test_pairing_code_expired(api_client):
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import PairingCode
    from datetime import datetime, timedelta
    from sqlalchemy.orm import Session

    with Session(get_engine()) as db:
        db.add(PairingCode(code="oldcode", user_id="dennis", expires_at=datetime.utcnow() - timedelta(minutes=1)))
        db.commit()

    resp = api_client.post("/api/v1/pair", json={"operationId": test_uuid("op1"), "code": "oldcode", "deviceName": "A"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "PAIRING_CODE_EXPIRED"


def test_revoked_device_rejected_everywhere(api_client):
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import PairingCode
    from inventra_backend.services.pairing_service import revoke_device
    from datetime import datetime, timedelta
    from sqlalchemy.orm import Session

    with Session(get_engine()) as db:
        db.add(PairingCode(code="c1", user_id="dennis", expires_at=datetime.utcnow() + timedelta(minutes=5)))
        db.commit()
    exchange = api_client.post("/api/v1/pair", json={"operationId": test_uuid("op1"), "code": "c1", "deviceName": "A"})
    device_id = exchange.json()["deviceId"]
    token = exchange.json()["token"]

    with Session(get_engine()) as db:
        revoke_device(db, device_id)
        db.commit()

    resp = api_client.get("/api/v1/locations", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@pytest.mark.parametrize("device_state", ["active", "seen", "expired", "revoked", "missing"])
def test_pairing_replay_rotates_token_without_persisting_it(api_client, device_state):
    import json
    from datetime import datetime, timedelta
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import Device, PairingCode, ProcessedOperation

    operation_id = test_uuid("redacted-pair")
    payload = {"operationId": operation_id, "code": "replay-code", "deviceName": "Phone"}
    with Session(get_engine()) as db:
        db.add(PairingCode(code=payload["code"], user_id="dennis",
                           expires_at=datetime.utcnow() + timedelta(minutes=5)))
        db.commit()
    first = api_client.post("/api/v1/pair", json=payload)
    assert first.status_code == 201
    result = first.json()
    assert set(result) == {"deviceId", "token"}
    old_token = result["token"]
    if device_state == "seen":
        assert api_client.get("/api/v1/locations", headers={"Authorization": f"Bearer {old_token}"}).status_code == 200
    with Session(get_engine()) as db:
        snapshot = db.get(ProcessedOperation, operation_id).result_snapshot
        assert json.loads(snapshot) == {"deviceId": result["deviceId"]}
        assert old_token not in snapshot
        device = db.get(Device, result["deviceId"])
        if device_state == "expired":
            db.get(PairingCode, payload["code"]).consumed_at = datetime.utcnow() - timedelta(minutes=11)
        if device_state == "revoked":
            device.revoked_at = datetime.utcnow()
        elif device_state == "missing":
            db.delete(device)
        db.commit()
    mismatch = api_client.post("/api/v1/pair", json={**payload, "deviceName": "Other"})
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "OPERATION_ID_PAYLOAD_MISMATCH"
    replay = api_client.post("/api/v1/pair", json=payload)
    if device_state in ("seen", "expired"):
        assert replay.status_code == 422
        assert replay.json()["error"]["code"] == "PAIRING_CODE_ALREADY_USED"
        return
    if device_state != "active":
        assert replay.status_code == 422
        assert replay.json()["error"]["code"] == "DEVICE_REVOKED"
        return
    assert replay.status_code == 201
    assert set(replay.json()) == {"deviceId", "token"}
    assert replay.json()["deviceId"] == result["deviceId"]
    new_token = replay.json()["token"]
    assert new_token != old_token
    assert api_client.get("/api/v1/locations", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert api_client.get("/api/v1/locations", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    with Session(get_engine()) as db:
        assert json.loads(db.get(ProcessedOperation, operation_id).result_snapshot) == {"deviceId": result["deviceId"]}
