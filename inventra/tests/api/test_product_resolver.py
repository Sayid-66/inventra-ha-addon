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
    assert body["fields"]["name"]["value"] == "Milch 1 l"


def test_re_resolve_requires_product_owned_barcode(api_client_with_device):
    client, device = api_client_with_device
    resp = client.post(
        "/api/v1/products/does-not-exist/re-resolve", json={"barcode": "0000000000000"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 404
