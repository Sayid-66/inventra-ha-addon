from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from inventra_backend.db.base import get_engine
from inventra_backend.db.models import BringWatchState, Product
from tests.ids import test_uuid as uuid


def seed(state="ERROR", deleted=False):
    product_id = uuid(state + str(deleted))
    with Session(get_engine()) as db:
        db.add(Product(id=product_id, name="Milch", deleted_at=datetime.now(timezone.utc) if deleted else None))
        db.flush()
        db.add(BringWatchState(product_id=product_id, state=state, origin="INVENTRA_CREATED",
                               bring_item_name="Milch", bring_uid="private-uid", last_error="Failed" if state == "ERROR" else None,
                               retry_count=2, updated_at=datetime(2026, 1, 2, 3, 4, 5)))
        db.commit()
    return product_id


def test_list_and_single(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    ids = [seed(state) for state in ("ERROR", "PENDING_ADD", "ON_LIST_CONFIRMED", "LOCKED_PURCHASED")]
    deleted = seed(deleted=True)
    response = client.get("/api/v1/bring/status", headers=headers)
    assert response.status_code == 200
    rows = response.json()
    assert {row["productId"] for row in rows} == set(ids)
    assert deleted not in {row["productId"] for row in rows}
    for row in rows:
        assert set(row) == {"productId", "state", "origin", "itemName", "lastError", "retryCount", "updatedAt"}
        assert row["updatedAt"] == "2026-01-02T03:04:05+00:00"
        assert row["retryCount"] == 2
        assert client.get(f"/api/v1/bring/status/{row['productId']}", headers=headers).json() == row
    assert client.get(f"/api/v1/bring/status/{deleted}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/bring/status/{uuid('missing')}", headers=headers).status_code == 404


@pytest.mark.parametrize("method,path", [("get", "/api/v1/bring/status"), ("get", "/api/v1/bring/status/p"), ("post", "/api/v1/bring/status/p/retry")])
def test_requires_device(api_client, method, path):
    assert getattr(api_client, method)(path).status_code == 401


def test_retry_deletes_before_evaluation(api_client_with_device, monkeypatch):
    client, device = api_client_with_device
    product_id = seed()
    calls = []

    async def evaluate(pid, increased):
        with Session(get_engine()) as db:
            assert db.get(BringWatchState, pid) is None
        calls.append((pid, increased))

    monkeypatch.setattr("inventra_backend.api.bring.bring_service.schedule_stock_change", evaluate)
    headers = {"Authorization": f"Bearer {device.token}"}
    response = client.post(f"/api/v1/bring/status/{product_id}/retry", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"productId": product_id, "state": None}
    assert calls == [(product_id, False)]
    with Session(get_engine()) as db:
        assert db.get(BringWatchState, product_id) is None
    assert client.post(f"/api/v1/bring/status/{product_id}/retry", headers=headers).status_code == 422


@pytest.mark.parametrize("state", ["PENDING_ADD", "ON_LIST_CONFIRMED", "LOCKED_PURCHASED"])
def test_retry_requires_error(api_client_with_device, state):
    client, device = api_client_with_device
    product_id = seed(state)
    response = client.post(f"/api/v1/bring/status/{product_id}/retry", headers={"Authorization": f"Bearer {device.token}"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BRING_NOT_IN_ERROR"
    with Session(get_engine()) as db:
        assert db.get(BringWatchState, product_id).state == state


def test_retry_recovers_after_repeated_ha_add_failures(api_client_with_device, monkeypatch):
    import asyncio
    from datetime import timedelta
    from inventra_backend.services import bring_service as bring
    from inventra_backend.services.bring_ha_client import HomeAssistantApiError
    client, device = api_client_with_device
    product_id = uuid('retry-outage')
    engine = get_engine()
    with Session(engine) as db:
        db.add(Product(id=product_id, name='Water', min_stock=2))
        db.commit()
    class HaClient:
        failing = True
        items = []
        async def get_items(self):
            with Session(engine) as db:
                db.get(Product, product_id).name = 'Water'
                db.commit()
            return list(self.items)
        async def add_item(self, name, description=None):
            with Session(engine) as db:
                db.get(Product, product_id).name = 'Water'
                db.commit()
            if self.failing:
                raise HomeAssistantApiError('secret')
            self.items.append(dict(uid='water', summary=name, status='needs_action', description=description))
    ha = HaClient()
    monkeypatch.setattr(bring, 'BringHaClient', lambda **kwargs: ha)
    async def cycle():
        await asyncio.wait_for(bring.reconcile_once(engine, ha), 2)
    asyncio.run(cycle())
    with Session(engine) as db:
        db.get(BringWatchState, product_id).confirmation_deadline_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
    asyncio.run(cycle())
    asyncio.run(cycle())
    with Session(engine) as db:
        assert db.get(BringWatchState, product_id).state == 'ERROR'
    ha.failing = False
    response = client.post(f'/api/v1/bring/status/{product_id}/retry',
                           headers={'Authorization': f'Bearer {device.token}'})
    assert response.status_code == 200
    assert response.json() == {'productId': product_id, 'state': None}
    asyncio.run(cycle())
    status = client.get(f'/api/v1/bring/status/{product_id}',
                        headers={'Authorization': f'Bearer {device.token}'}).json()
    assert status['state'] == 'ON_LIST_CONFIRMED'
    assert status['lastError'] is None
