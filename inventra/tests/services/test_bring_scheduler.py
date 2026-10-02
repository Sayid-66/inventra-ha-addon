import asyncio

import pytest

from inventra_backend.services.bring_service import run_bring_reconcile_loop


@pytest.mark.anyio
async def test_reconcile_loop_runs_immediately_then_on_interval(monkeypatch):
    calls = []

    async def _fake_reconcile_once(db, client):
        calls.append("tick")

    monkeypatch.setattr("inventra_backend.services.bring_service.reconcile_once", _fake_reconcile_once)
    monkeypatch.setattr("inventra_backend.services.bring_service.get_engine", lambda: object())

    task = asyncio.create_task(run_bring_reconcile_loop(interval_seconds=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 2  # ran at startup and at least once more on the interval


@pytest.mark.anyio
async def test_concurrent_stock_changes_complete_without_overlapping_transactions(db_session, monkeypatch):
    from inventra_backend.db.models import BringWatchState, BringWatchStateEnum, Product
    from inventra_backend.services import bring_service

    db_session.add_all([Product(id="first", name="Water", min_stock=2),
                        Product(id="second", name="Milk", min_stock=2)])
    db_session.commit()
    first_adding = asyncio.Event()
    release = asyncio.Event()
    added = []
    fetches = []

    class Client:
        async def get_items(self):
            fetches.append(True)
            return []

        async def add_item(self, name):
            added.append(name)
            if name == "Water":
                first_adding.set()
                await release.wait()

    monkeypatch.setattr(bring_service, "get_engine", lambda: db_session.get_bind())
    monkeypatch.setattr(bring_service, "BringHaClient", lambda **kwargs: Client())
    first = asyncio.create_task(bring_service.schedule_stock_change("first", False))
    second = None
    try:
        await asyncio.wait_for(first_adding.wait(), 2)
        second = asyncio.create_task(bring_service.schedule_stock_change("second", False))
        await asyncio.sleep(0.02)
        assert not second.done()
        assert len(fetches) == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        db_session.expire_all()
        assert added == ["Water", "Milk"]
        for product_id in ("first", "second"):
            assert db_session.get(BringWatchState, product_id).state == BringWatchStateEnum.PENDING_ADD
    finally:
        release.set()
        tasks = [task for task in (first, second) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_reconcile_lock_is_scoped_to_running_event_loop():
    from inventra_backend.services import bring_service

    async def contend():
        lock = bring_service._get_reconcile_lock()
        assert bring_service._get_reconcile_lock() is lock
        entered = asyncio.Event()

        async def waiter():
            async with bring_service._get_reconcile_lock():
                entered.set()

        async with lock:
            task = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            assert not entered.is_set()
        await asyncio.wait_for(task, 2)
        assert entered.is_set()
        return lock

    first_lock = asyncio.run(contend())
    second_lock = asyncio.run(contend())
    assert second_lock is not first_lock
