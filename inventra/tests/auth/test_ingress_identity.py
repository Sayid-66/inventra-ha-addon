from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from inventra_backend.auth.ingress_identity import require_ingress_identity


def _make_app(zone: str):
    app = FastAPI()

    @app.middleware("http")
    async def set_zone(request, call_next):
        request.state.trust_zone = zone
        return await call_next(request)

    @app.get("/whoami")
    def whoami(user_id: str = Depends(require_ingress_identity)):
        return {"userId": user_id}

    return app


def test_ingress_zone_with_header_returns_user_id():
    client = TestClient(_make_app("ingress"))
    resp = client.get("/whoami", headers={"X-Remote-User-Id": "dennis"})
    assert resp.status_code == 200
    assert resp.json() == {"userId": "dennis"}


def test_ingress_zone_without_header_rejected():
    client = TestClient(_make_app("ingress"))
    resp = client.get("/whoami")
    assert resp.status_code == 401


def test_api_zone_never_honors_the_header_even_if_a_route_tried():
    """Proves the isolation the spec requires: this dependency refuses to
    run at all outside the ingress zone, so an API-zone route can never
    be tricked into trusting a spoofed X-Remote-User-Id header."""
    client = TestClient(_make_app("api"))
    resp = client.get("/whoami", headers={"X-Remote-User-Id": "dennis"})
    assert resp.status_code == 403
