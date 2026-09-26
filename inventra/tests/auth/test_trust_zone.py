import os

import httpx
import pytest

from inventra_backend.config import get_settings, reset_settings_cache
from inventra_backend.main import create_app


@pytest.fixture(autouse=True)
def reset_ingress_proxy_ip():
    os.environ["INVENTRA_INGRESS_PROXY_IP"] = "172.30.32.2"
    reset_settings_cache()
    get_settings().ingress_proxy_ip = "172.30.32.2"


@pytest.mark.anyio
async def test_ingress_zone_rejects_non_proxy_source():
    app = create_app("ingress")
    transport = httpx.ASGITransport(app=app, client=("10.0.0.5", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN_INGRESS_SOURCE"


@pytest.mark.anyio
async def test_ingress_zone_accepts_proxy_source():
    app = create_app("ingress")
    transport = httpx.ASGITransport(app=app, client=("172.30.32.2", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_api_zone_has_no_source_ip_restriction():
    app = create_app("api")
    transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
