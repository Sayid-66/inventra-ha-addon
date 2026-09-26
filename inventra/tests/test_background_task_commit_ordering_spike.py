from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.testclient import TestClient


RUNS_PER_VARIANT = 20
EXPECTED_ORDER = [
    "route-handler-returned",
    "dependency-post-yield-commit-executed",
    "background-task-started",
    "background-task-saw-the-committed-row",
]


@dataclass(frozen=True)
class Marker:
    sequence: int
    timestamp_ns: int
    event: str


class OrderingLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._markers: list[Marker] = []

    def append(self, event: str) -> None:
        with self._lock:
            self._markers.append(
                Marker(
                    sequence=len(self._markers),
                    timestamp_ns=time.monotonic_ns(),
                    event=event,
                )
            )

    def snapshot(self) -> list[Marker]:
        with self._lock:
            return list(self._markers)


def _run_ordering_proof(db_path: Path, *, slow_commit: bool) -> list[Marker]:
    with sqlite3.connect(db_path) as setup_connection:
        setup_connection.execute(
            "CREATE TABLE committed_items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )

    ordering = OrderingLog()
    app = FastAPI()

    def committing_dependency() -> Iterator[sqlite3.Connection]:
        request_connection = sqlite3.connect(db_path, check_same_thread=False)
        try:
            request_connection.execute("BEGIN")
            yield request_connection
            if slow_commit:
                time.sleep(0.05)
            request_connection.commit()
            ordering.append("dependency-post-yield-commit-executed")
        finally:
            request_connection.close()

    def verify_from_independent_connection() -> None:
        ordering.append("background-task-started")
        with sqlite3.connect(db_path) as background_connection:
            row = background_connection.execute(
                "SELECT value FROM committed_items WHERE id = 1"
            ).fetchone()
        ordering.append(
            "background-task-saw-the-committed-row"
            if row == ("created-by-request",)
            else "background-task-did-not-see-the-committed-row"
        )

    @app.post("/items")
    def create_item(
        background_tasks: BackgroundTasks,
        request_connection: sqlite3.Connection = Depends(committing_dependency),
    ) -> dict[str, str]:
        request_connection.execute(
            "INSERT INTO committed_items (id, value) VALUES (?, ?)",
            (1, "created-by-request"),
        )
        background_tasks.add_task(verify_from_independent_connection)
        ordering.append("route-handler-returned")
        return {"status": "created"}

    with TestClient(app) as client:
        response = client.post("/items")

    assert response.status_code == 200
    return ordering.snapshot()


def _assert_expected_order(markers: list[Marker], *, iteration: int) -> None:
    assert [marker.sequence for marker in markers] == list(range(len(markers)))
    assert [marker.event for marker in markers] == EXPECTED_ORDER, (
        f"iteration {iteration} recorded an unexpected order: "
        f"{[marker.event for marker in markers]}"
    )


def test_background_task_starts_after_dependency_commit(tmp_path: Path) -> None:
    for iteration in range(RUNS_PER_VARIANT):
        markers = _run_ordering_proof(
            tmp_path / f"normal-{iteration}.sqlite3", slow_commit=False
        )
        _assert_expected_order(markers, iteration=iteration)


def test_background_task_starts_after_slow_dependency_commit(tmp_path: Path) -> None:
    for iteration in range(RUNS_PER_VARIANT):
        markers = _run_ordering_proof(
            tmp_path / f"slow-{iteration}.sqlite3", slow_commit=True
        )
        _assert_expected_order(markers, iteration=iteration)
