import httpx
import pytest

from inventra_backend.services.bring_ha_client import (
    BringHaClient,
    HomeAssistantApiError,
)


def _client(handler) -> BringHaClient:
    transport = httpx.MockTransport(handler)
    client = BringHaClient(
        base_url="http://supervisor/core/api",
        token="tok",
        todo_entity_id="todo.zuhause",
    )
    client._transport_override = transport
    return client


@pytest.mark.anyio
async def test_get_items_returns_items_from_service_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/core/api/services/todo/get_items"
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(
            200,
            json={
                "changed_states": [],
                "service_response": {
                    "todo.zuhause": {
                        "items": [
                            {
                                "summary": "Wasser",
                                "uid": "u1",
                                "status": "needs_action",
                            },
                        ]
                    }
                },
            },
        )

    client = _client(handler)
    items = await client.get_items()
    assert items == [
        {"summary": "Wasser", "uid": "u1", "status": "needs_action"}
    ]


@pytest.mark.anyio
async def test_add_item_posts_expected_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = httpx.Request(
            "POST", request.url, content=request.content
        ).content
        return httpx.Response(200, json={"changed_states": []})

    client = _client(handler)
    await client.add_item("Wasser")
    assert b'"item":"Wasser"' in seen["json"] or b'"item": "Wasser"' in seen["json"]


@pytest.mark.anyio
async def test_get_items_raises_homeassistant_api_error_on_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="supervisor unavailable")

    client = _client(handler)
    with pytest.raises(HomeAssistantApiError):
        await client.get_items()


@pytest.mark.anyio
async def test_add_item_raises_homeassistant_api_error_on_connection_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)
    with pytest.raises(HomeAssistantApiError):
        await client.add_item("Wasser")
