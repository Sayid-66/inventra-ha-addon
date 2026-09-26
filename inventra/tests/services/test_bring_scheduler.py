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
