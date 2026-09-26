from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class SingleFlight:
    """Coalesces concurrent calls for the same key within one running
    backend instance (spec §4.1). Not a distributed lock, not a
    correctness requirement — resolve is idempotent, so uncoalesced
    duplicate work (e.g. across a restart) is merely wasteful, never
    wrong."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task] = {}

    async def run(self, key: str, coro_factory: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing

        task = asyncio.ensure_future(coro_factory())
        self._inflight[key] = task
        try:
            return await task
        finally:
            if self._inflight.get(key) is task:
                del self._inflight[key]
