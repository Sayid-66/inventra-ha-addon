def test_resolve_requires_device_token(api_client):
    resp = api_client.post("/api/v1/products/resolve", json={"barcode": "4006381333931"})
    assert resp.status_code == 401


def test_resolve_unknown_barcode_with_mocked_sources(api_client_with_device, monkeypatch):
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

    resp = client.post(
        "/api/v1/products/resolve", json={"barcode": "4006381333931"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matchedLocally"] is False
    assert body["resolutionId"] is not None
    assert body["fields"]["name"]["value"] == "Milch"


def test_re_resolve_requires_product_owned_barcode(api_client_with_device):
    client, device = api_client_with_device
    resp = client.post(
        "/api/v1/products/does-not-exist/re-resolve", json={"barcode": "0000000000000"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 404



def test_resolve_raw_sources_are_bounded_and_safe(api_client_with_device, monkeypatch):
    import json
    from inventra_backend.resolver import resolver_service
    from inventra_backend.resolver.source_client import SourceCandidate, SourceResult

    client, device = api_client_with_device
    async def fetch(barcode, settings):
        return {
            "off": SourceResult("off", "FOUND", SourceCandidate(
                "Raw Name " * 30, "Brand", "500 ml", "https://secret/image",
                "Raw Category", "Raw Variant")),
            "obf": SourceResult("obf", "FOUND", SourceCandidate(
                None, None, None, None, None, None, brands=("First", "Second"))),
            "opff": SourceResult("opff", "NOT_FOUND", None),
            "opf": SourceResult("opf", "ERROR", None, error="http_503"),
        }
    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fetch)
    response = client.post("/api/v1/products/resolve", json={"barcode": "raw-contract"},
                           headers={"Authorization": f"Bearer {device.token}"})
    assert response.status_code == 200
    raw = response.json()["rawSources"]
    assert raw == {
        "off": {"status": "FOUND", "error": None, "name": ("Raw Name " * 30)[:200],
                "brand": "Brand", "quantityText": "500 ml", "category": "Raw Category",
                "variant": "Raw Variant", "hasImage": True},
        "obf": {"status": "FOUND", "error": None, "name": None, "brand": "First, Second",
                "quantityText": None, "category": None, "variant": None, "hasImage": False},
        "opff": {"status": "NOT_FOUND", "error": None},
        "opf": {"status": "ERROR", "error": "http_503"},
    }
    assert "https://" not in json.dumps(raw)
    assert "imageUrl" not in json.dumps(raw)


def test_resolve_local_product_has_no_raw_sources(api_client_with_device, monkeypatch):
    from sqlalchemy.orm import Session
    from inventra_backend.db.base import get_engine
    from inventra_backend.db.models import Barcode, Product
    from inventra_backend.resolver import resolver_service
    from tests.ids import test_uuid

    with Session(get_engine()) as db:
        db.add(Product(id=test_uuid("raw-local"), name="Local", version=1))
        db.flush()
        db.add(Barcode(code="raw-local", product_id=test_uuid("raw-local"), version=1))
        db.commit()
    async def unexpected_fetch(*args):
        raise AssertionError("Local resolve must not fetch sources")
    monkeypatch.setattr(resolver_service, "_fetch_all_sources", unexpected_fetch)
    client, device = api_client_with_device
    response = client.post("/api/v1/products/resolve", json={"barcode": "raw-local"},
                           headers={"Authorization": f"Bearer {device.token}"})
    assert response.status_code == 200
    assert response.json()["matchedLocally"] is True
    assert response.json()["rawSources"] is None


def test_raw_source_error_codes_and_all_text_limits():
    from inventra_backend.resolver.resolver_service import _raw_sources
    from inventra_backend.resolver.source_client import SourceCandidate, SourceResult

    text = "x" * 250
    raw = _raw_sources({
        "off": SourceResult("off", "FOUND", SourceCandidate(text, text, text, None, text, text)),
        "obf": SourceResult("obf", "ERROR", None, error="code_" + "x" * 100),
        "opf": SourceResult("opf", "ERROR", None,
                            error="Request https://secret Authorization: Bearer token"),
    })
    for key in ("name", "brand", "quantityText", "category", "variant"):
        assert raw["off"][key] == text[:200]
    assert len(raw["obf"]["error"]) == 64
    assert raw["opf"] == {"status": "ERROR", "error": "source_error"}
