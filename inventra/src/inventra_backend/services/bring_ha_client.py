from __future__ import annotations

import httpx


class HomeAssistantApiError(Exception):
    """Raised whenever the Supervisor-proxied HA Core API is unreachable,
    times out, or returns a non-2xx response. Callers (bring_service.py)
    treat this uniformly as 'try again next reconcile cycle' — it must
    never be interpreted as a reason to mutate bring_watch_state.
    """


class BringHaClient:
    """Thin, mockable wrapper around the two HA Core ``todo.*`` service
    calls TP5 needs, called through the Supervisor proxy with the
    auto-injected SUPERVISOR_TOKEN (spec §3.1, §7). Test-only hook:
    set ``_transport_override`` on an instance to inject an
    httpx.MockTransport instead of a real connection.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        todo_entity_id: str,
        timeout_seconds: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._todo_entity_id = todo_entity_id
        self._timeout = timeout_seconds
        self._transport_override: httpx.BaseTransport | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport_override,
        )

    async def get_items(self) -> list[dict]:
        async with self._client() as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/services/todo/get_items",
                    params={"return_response": ""},
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"entity_id": self._todo_entity_id},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise HomeAssistantApiError(
                    f"todo.get_items failed: {exc}"
                ) from exc
            body = resp.json()
        try:
            return body["service_response"][self._todo_entity_id]["items"]
        except (KeyError, TypeError) as exc:
            raise HomeAssistantApiError(
                f"unexpected todo.get_items response shape: {body}"
            ) from exc

    async def add_item(self, name: str) -> None:
        async with self._client() as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/services/todo/add_item",
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"entity_id": self._todo_entity_id, "item": name},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise HomeAssistantApiError(
                    f"todo.add_item failed: {exc}"
                ) from exc

    async def remove_item(self, uid: str) -> None:
        async with self._client() as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/services/todo/remove_item",
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"entity_id": self._todo_entity_id, "item": [uid]},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise HomeAssistantApiError(
                    f"todo.remove_item failed: {exc}"
                ) from exc
