import asyncio
import pytest

from inventra_backend.resolver.single_flight import SingleFlight


@pytest.mark.anyio
async def test_concurrent_calls_for_same_key_share_one_execution():
    calls = {"count": 0}

    async def work():
        calls["count"] += 1
        await asyncio.sleep(0.05)
        return "result"

    sf = SingleFlight()
    results = await asyncio.gather(
        sf.run("barcode-1", work), sf.run("barcode-1", work), sf.run("barcode-1", work),
    )
    assert results == ["result", "result", "result"]
    assert calls["count"] == 1


@pytest.mark.anyio
async def test_different_keys_run_independently():
    calls = {"count": 0}

    async def work():
        calls["count"] += 1
        await asyncio.sleep(0.01)
        return "result"

    sf = SingleFlight()
    await asyncio.gather(sf.run("a", work), sf.run("b", work))
    assert calls["count"] == 2


@pytest.mark.anyio
async def test_a_key_can_run_again_after_the_first_call_completes():
    calls = {"count": 0}

    async def work():
        calls["count"] += 1
        return calls["count"]

    sf = SingleFlight()
    first = await sf.run("barcode-1", work)
    second = await sf.run("barcode-1", work)
    assert (first, second) == (1, 2)


@pytest.mark.anyio
async def test_exception_propagates_to_all_waiters_and_clears_the_slot():
    async def failing():
        raise ValueError("boom")

    sf = SingleFlight()
    with pytest.raises(ValueError):
        await asyncio.gather(sf.run("x", failing), sf.run("x", failing))
    # slot cleared -> a subsequent call is a fresh attempt, not a cached exception
    async def ok():
        return "recovered"
    assert await sf.run("x", ok) == "recovered"
