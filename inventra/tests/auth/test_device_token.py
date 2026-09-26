from datetime import datetime

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from inventra_backend.auth.device_token import require_device, hash_token
from inventra_backend.db.base import Base, configure_engine, get_db, init_engine
from inventra_backend.db.models import Device


def _make_app(tmp_path):
    db_path = str(tmp_path / "t.db")
    init_engine(db_path)
    engine = configure_engine(db_path)
    Base.metadata.create_all(engine)

    app = FastAPI()

    @app.middleware("http")
    async def set_zone(request, call_next):
        request.state.trust_zone = "api"
        return await call_next(request)

    @app.get("/protected")
    def protected(device: Device = Depends(require_device)):
        return {"deviceId": device.device_id}

    return app, engine


def test_valid_token_authenticates(tmp_path):
    app, engine = _make_app(tmp_path)
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        device = Device(device_id="d1", user_id="u1", device_name="Pixel", token_hash=hash_token("secret"))
        db.add(device)
        db.commit()
        assert device.last_seen_at is None
    client = TestClient(app)
    before_request = datetime.utcnow()
    resp = client.get("/protected", headers={"Authorization": "Bearer secret"})
    after_request = datetime.utcnow()
    assert resp.status_code == 200
    assert resp.json() == {"deviceId": "d1"}
    with Session(engine) as db:
        last_seen_at = db.get(Device, "d1").last_seen_at
        assert last_seen_at is not None
        assert before_request <= last_seen_at <= after_request


def test_missing_token_rejected(tmp_path):
    app, engine = _make_app(tmp_path)
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        db.add(Device(device_id="d1", user_id="u1", device_name="Pixel", token_hash=hash_token("secret")))
        db.commit()
    resp = TestClient(app).get("/protected")
    assert resp.status_code == 401
    with Session(engine) as db:
        assert db.get(Device, "d1").last_seen_at is None


def test_invalid_token_rejected_without_updating_device(tmp_path):
    app, engine = _make_app(tmp_path)
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        db.add(Device(device_id="d1", user_id="u1", device_name="Pixel", token_hash=hash_token("secret")))
        db.commit()
    resp = TestClient(app).get("/protected", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    with Session(engine) as db:
        assert db.get(Device, "d1").last_seen_at is None


def test_revoked_token_rejected(tmp_path):
    app, engine = _make_app(tmp_path)
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        db.add(Device(
            device_id="d1", user_id="u1", device_name="Pixel",
            token_hash=hash_token("secret"), revoked_at=datetime.utcnow(),
        ))
        db.commit()
    resp = TestClient(app).get("/protected", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 401
    with Session(engine) as db:
        assert db.get(Device, "d1").last_seen_at is None
