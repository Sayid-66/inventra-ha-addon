import httpx
import pytest

from inventra_backend.resolver.source_client import OpenFactsClient, SourceResult


def _client(handler, timeout=3.0) -> OpenFactsClient:
    transport = httpx.MockTransport(handler)
    client = OpenFactsClient(
        source_id="off", base_url="https://world.openfoodfacts.org",
        timeout_seconds=timeout, user_agent="Inventra/test",
    )
    client._transport_override = transport
    return client


@pytest.mark.anyio
async def test_found_product_maps_to_source_candidate():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/product/4006381333931.json"
        assert request.headers["user-agent"] == "Inventra/test"
        return httpx.Response(200, json={
            "status": 1,
            "product": {
                "product_name_de": "Nutella Nuss-Nougat-Creme",
                "brands": "Nutella,Ferrero",
                "quantity": "450 g",
                "image_url": "https://images.example.org/nutella.jpg",
                "categories": "Brotaufstriche",
            },
        })

    client = _client(handler)
    result = await client.fetch("4006381333931")
    assert result.status == "FOUND"
    assert result.source == "off"
    assert result.candidate.name == "Nutella Nuss-Nougat-Creme"
    assert result.candidate.brand == "Nutella"
    assert result.candidate.quantity_text == "450 g"
    assert result.candidate.image_url == "https://images.example.org/nutella.jpg"


@pytest.mark.anyio
async def test_status_zero_is_not_found_not_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": 0})

    client = _client(handler)
    result = await client.fetch("0000000000000")
    assert result.status == "NOT_FOUND"
    assert result.candidate is None


@pytest.mark.anyio
async def test_http_404_is_not_found_not_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    result = await client.fetch("0000000000000")
    assert result.status == "NOT_FOUND"


@pytest.mark.anyio
async def test_5xx_is_error_not_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _client(handler)
    result = await client.fetch("4006381333931")
    assert result.status == "ERROR"
    assert result.candidate is None


@pytest.mark.anyio
async def test_429_is_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client(handler)
    result = await client.fetch("4006381333931")
    assert result.status == "ERROR"


@pytest.mark.anyio
async def test_timeout_is_error_with_no_retry():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.TimeoutException("timed out", request=request)

    client = _client(handler, timeout=0.01)
    result = await client.fetch("4006381333931")
    assert result.status == "ERROR"
    assert calls["count"] == 1  # no automatic retry within one fetch (spec §4.1)
