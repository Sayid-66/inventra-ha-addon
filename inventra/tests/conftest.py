import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from inventra_backend.auth.device_token import hash_token
from inventra_backend.config import get_settings, reset_settings_cache
from inventra_backend.db.base import get_engine
from inventra_backend.db.models import Device
from inventra_backend.main import create_app


ADDON_ROOT = Path(__file__).resolve().parents[1]


def _migrate(db_path: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ADDON_ROOT,
        env={**os.environ, "INVENTRA_DB_PATH": db_path},
        check=True,
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db_session(tmp_path):
    from sqlalchemy.orm import Session
    from inventra_backend.db.base import configure_engine
    db_path = str(tmp_path / "unit.db")
    _migrate(db_path)
    engine = configure_engine(db_path)
    with Session(engine) as session:
        yield session


@pytest.fixture
def api_client(tmp_path, monkeypatch) -> TestClient:
    db_path = str(tmp_path / "api.db")
    _migrate(db_path)
    monkeypatch.setenv("INVENTRA_DB_PATH", db_path)
    reset_settings_cache()
    get_settings().db_path = db_path
    return TestClient(create_app("api"))


@pytest.fixture
def ingress_client(tmp_path, monkeypatch) -> TestClient:
    db_path = str(tmp_path / "ingress.db")
    _migrate(db_path)
    monkeypatch.setenv("INVENTRA_DB_PATH", db_path)
    monkeypatch.setenv("INVENTRA_INGRESS_PROXY_IP", "testclient")
    reset_settings_cache()
    get_settings().db_path = db_path
    get_settings().ingress_proxy_ip = "testclient"
    return TestClient(create_app("ingress"))


@pytest.fixture
def api_client_with_device(api_client):
    try:
        from sqlalchemy.orm import Session
        with Session(get_engine()) as db:
            db.add(Device(device_id="d1", user_id="u1", device_name="Test", token_hash=hash_token("secrettoken")))
            db.commit()

        class _D:
            token = "secrettoken"
            device_id = "d1"
            user_id = "u1"

        yield api_client, _D()
    finally:
        os.environ.pop("INVENTRA_DB_PATH", None)
        reset_settings_cache()
