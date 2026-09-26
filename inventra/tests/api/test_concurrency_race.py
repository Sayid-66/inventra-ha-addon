"""Concurrency regressions for inventory mutations on file-backed SQLite.

These tests target the lost-update window in ``_deplete_fifo``: batches are
read without a write lock or version guard and later overwritten with a
Python-computed quantity.  A test-only rendezvous after the real FIFO read
makes that window reproducible while requests, sessions, SQL, and commits all
remain real.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from inventra_backend.db.base import get_engine
from inventra_backend.services import inventory_service
from tests.ids import test_uuid


REQUEST_TIMEOUT_SECONDS = 15.0


def _headers(device) -> dict[str, str]:
    return {"Authorization": f"Bearer {device.token}"}


def _database_path() -> Path:
    database = get_engine().url.database
    assert database is not None
    return Path(database)


def _create_location(client, device, seed: str, name: str) -> str:
    location_id = test_uuid(seed)
    response = client.post(
        "/api/v1/locations",
        json={"operationId": test_uuid(f"op-{seed}"), "id": location_id, "name": name},
        headers=_headers(device),
    )
    assert response.status_code == 201, response.text
    return location_id


def _purchase_payload(
    *,
    operation_seed: str,
    event_seed: str,
    product_id: str,
    location_id: str,
    quantity: int,
    timestamp: int,
    new_product: bool,
) -> dict:
    return {
        "operationId": test_uuid(operation_seed),
        "id": test_uuid(event_seed),
        "productId": product_id,
        "newProduct": {"name": "Concurrency milk", "imageUrl": None} if new_product else None,
        "barcode": "concurrency-4001",
        "locationId": location_id,
        "quantity": quantity,
        "storeId": None,
        "pricePerUnitCents": None,
        "mhd": None,
        "minStock": None,
        "contentUnitLabel": None,
        "contentTotal": None,
        "contentBreakdown": None,
        "timestamp": timestamp,
    }


def _seed_stock(client, device, quantity: int) -> tuple[str, str]:
    product_id = test_uuid("concurrency-product")
    location_id = _create_location(client, device, "concurrency-source", "Concurrency source")
    response = client.post(
        "/api/v1/purchases",
        json=_purchase_payload(
            operation_seed="concurrency-seed-purchase-op",
            event_seed="concurrency-seed-purchase-event",
            product_id=product_id,
            location_id=location_id,
            quantity=quantity,
            timestamp=1_000,
            new_product=True,
        ),
        headers=_headers(device),
    )
    assert response.status_code == 201, response.text
    return product_id, location_id


def _consumption_payload(
    operation_seed: str,
    event_seed: str,
    product_id: str,
    location_id: str,
    *,
    quantity: int = 1,
    timestamp: int = 2_000,
) -> dict:
    return {
        "operationId": test_uuid(operation_seed),
        "id": test_uuid(event_seed),
        "productId": product_id,
        "locationId": location_id,
        "stockKind": "STK",
        "quantity": quantity,
        "timestamp": timestamp,
    }


def _run_concurrently(calls: list[Callable[[], object]]) -> list[object]:
    """Release daemon workers together and fail after a bounded join."""
    start = threading.Barrier(len(calls) + 1, timeout=REQUEST_TIMEOUT_SECONDS)
    results: list[object | None] = [None] * len(calls)

    def invoke(index: int, call: Callable[[], object]) -> None:
        try:
            start.wait()
            results[index] = call()
        except BaseException as exc:  # Preserve server/thread failures as observable results.
            results[index] = exc

    threads = [
        threading.Thread(target=invoke, args=(index, call), daemon=True)
        for index, call in enumerate(calls)
    ]
    for thread in threads:
        thread.start()
    start.wait()

    deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    alive = [thread.name for thread in threads if thread.is_alive()]
    assert not alive, f"concurrent requests exceeded {REQUEST_TIMEOUT_SECONDS}s: {alive}"
    assert all(result is not None for result in results), "a concurrent request silently vanished"
    return list(results)


def _synchronize_real_fifo_reads(monkeypatch, parties: int) -> None:
    """Legacy pre-fix rendezvous; BEGIN IMMEDIATE tests intentionally do not call it."""
    rendezvous = threading.Barrier(parties, timeout=REQUEST_TIMEOUT_SECONDS)
    real_ordered_batches = inventory_service._ordered_batches

    def ordered_batches_then_rendezvous(*args, **kwargs):
        batches = real_ordered_batches(*args, **kwargs)
        rendezvous.wait()
        return batches

    monkeypatch.setattr(inventory_service, "_ordered_batches", ordered_batches_then_rendezvous)


def _response_diagnostics(results: list[object]) -> list[object]:
    diagnostics = []
    for result in results:
        if isinstance(result, BaseException):
            diagnostics.append(f"EXCEPTION:{type(result).__name__}:{result}")
        else:
            diagnostics.append({"status": result.status_code, "body": result.json()})
    return diagnostics


def _status_codes(results: list[object]) -> list[object]:
    return [
        f"EXCEPTION:{type(result).__name__}" if isinstance(result, BaseException) else result.status_code
        for result in results
    ]


def _scalar(db: sqlite3.Connection, sql: str, parameters: tuple = ()) -> int:
    value = db.execute(sql, parameters).fetchone()[0]
    return int(value or 0)


def _stock(db: sqlite3.Connection, product_id: str, location_id: str | None = None) -> int:
    sql = "SELECT COALESCE(SUM(remaining_quantity), 0) FROM batches WHERE product_id = ?"
    parameters: tuple = (product_id,)
    if location_id is not None:
        sql += " AND location_id = ?"
        parameters += (location_id,)
    return _scalar(db, sql, parameters)


def _event_audit(
    db: sqlite3.Connection,
    table: str,
    entity_type: str,
    event_ids: list[str],
) -> tuple[int, int, int]:
    placeholders = ",".join("?" for _ in event_ids)
    event_count = _scalar(db, f"SELECT COUNT(*) FROM {table} WHERE id IN ({placeholders})", tuple(event_ids))
    log_count, distinct_revisions = db.execute(
        f"""
        SELECT COUNT(*), COUNT(DISTINCT revision)
        FROM change_log
        WHERE entity_type = ? AND change_kind = 'EVENT'
          AND entity_id IN ({placeholders})
        """,
        (entity_type, *event_ids),
    ).fetchone()
    return event_count, int(log_count), int(distinct_revisions)


def _assert_revision_counter_matches_log(db: sqlite3.Connection) -> None:
    counter = _scalar(db, "SELECT current_revision FROM revision_counter WHERE id = 0")
    maximum = _scalar(db, "SELECT COALESCE(MAX(revision), 0) FROM change_log")
    assert counter == maximum, f"revision counter/log mismatch: counter={counter}, max_log_revision={maximum}"


def test_parallel_consumptions_persist_every_event_and_deplete_stock_exactly_once(
    api_client_with_device, monkeypatch
):
    client, device = api_client_with_device
    product_id, location_id = _seed_stock(client, device, quantity=5)
    db_path = _database_path()
    event_ids = [test_uuid(f"baseline-consumption-event-{index}") for index in range(5)]
    payloads = [
        _consumption_payload(
            f"baseline-consumption-op-{index}",
            f"baseline-consumption-event-{index}",
            product_id,
            location_id,
            timestamp=2_000 + index,
        )
        for index in range(5)
    ]
    with sqlite3.connect(db_path) as db:
        starting_revision = _scalar(db, "SELECT current_revision FROM revision_counter WHERE id = 0")

    results = _run_concurrently(
        [lambda payload=payload: client.post("/api/v1/consumptions", json=payload, headers=_headers(device)) for payload in payloads]
    )

    with sqlite3.connect(db_path) as db:
        stock = _stock(db, product_id, location_id)
        event_count, event_log_count, distinct_event_revisions = _event_audit(
            db, "consumption_events", "ConsumptionEvent", event_ids
        )
        ending_revision = _scalar(db, "SELECT current_revision FROM revision_counter WHERE id = 0")
        _assert_revision_counter_matches_log(db)

    actual = {
        "statuses": _status_codes(results),
        "events": event_count,
        "stock": stock,
        "event_logs": event_log_count,
        "distinct_event_revisions": distinct_event_revisions,
        "revision_delta": ending_revision - starting_revision,
    }
    assert actual == {
        "statuses": [201] * 5,
        "events": 5,
        "stock": 0,
        "event_logs": 5,
        "distinct_event_revisions": 5,
        "revision_delta": 5,
    }, f"expected 5 events / stock 0 with five used-once revisions, got {actual}"


def test_parallel_overconsumption_has_only_persisted_successes_or_well_formed_rejections(
    api_client_with_device, monkeypatch
):
    client, device = api_client_with_device
    product_id, location_id = _seed_stock(client, device, quantity=3)
    db_path = _database_path()
    event_ids = [test_uuid(f"over-consumption-event-{index}") for index in range(5)]
    payloads = [
        _consumption_payload(
            f"over-consumption-op-{index}",
            f"over-consumption-event-{index}",
            product_id,
            location_id,
            timestamp=3_000 + index,
        )
        for index in range(5)
    ]

    results = _run_concurrently(
        [lambda payload=payload: client.post("/api/v1/consumptions", json=payload, headers=_headers(device)) for payload in payloads]
    )
    successes = [result for result in results if not isinstance(result, BaseException) and result.status_code == 201]
    rejections = [
        result
        for result in results
        if not isinstance(result, BaseException)
        and result.status_code == 422
        and result.json().get("error", {}).get("code") == "INSUFFICIENT_STOCK"
    ]

    with sqlite3.connect(db_path) as db:
        stock = _stock(db, product_id, location_id)
        event_count, event_log_count, distinct_event_revisions = _event_audit(
            db, "consumption_events", "ConsumptionEvent", event_ids
        )
        persisted_quantity = _scalar(
            db,
            f"SELECT COALESCE(SUM(quantity), 0) FROM consumption_events WHERE id IN ({','.join('?' for _ in event_ids)})",
            tuple(event_ids),
        )
        minimum_batch_quantity = _scalar(
            db,
            "SELECT COALESCE(MIN(remaining_quantity), 0) FROM batches WHERE product_id = ?",
            (product_id,),
        )
        _assert_revision_counter_matches_log(db)

    actual = {
        "statuses": _status_codes(results),
        "valid_responses": len(successes) + len(rejections),
        "successes": len(successes),
        "events": event_count,
        "persisted_quantity": persisted_quantity,
        "stock": stock,
        "event_logs": event_log_count,
        "distinct_event_revisions": distinct_event_revisions,
    }
    assert len(successes) + len(rejections) == 5, f"unexpected concurrent response: {_response_diagnostics(results)}"
    assert len(successes) == event_count, f"HTTP/database persistence mismatch: {actual}"
    assert event_log_count == distinct_event_revisions == event_count, f"event/change-log mismatch: {actual}"
    assert minimum_batch_quantity >= 0 and stock >= 0, f"negative stock after over-consumption: {actual}"
    assert stock == 3 - persisted_quantity, f"expected stock=3-persisted quantity, got {actual}"


def test_parallel_purchase_and_consumption_preserve_additive_stock(api_client_with_device):
    client, device = api_client_with_device
    product_id, location_id = _seed_stock(client, device, quantity=5)
    db_path = _database_path()
    purchase_id = test_uuid("mixed-purchase-event")
    consumption_id = test_uuid("mixed-consumption-event")
    purchase = _purchase_payload(
        operation_seed="mixed-purchase-op",
        event_seed="mixed-purchase-event",
        product_id=product_id,
        location_id=location_id,
        quantity=5,
        timestamp=4_000,
        new_product=False,
    )
    consumption = _consumption_payload(
        "mixed-consumption-op",
        "mixed-consumption-event",
        product_id,
        location_id,
        quantity=2,
        timestamp=4_001,
    )

    results = _run_concurrently(
        [
            lambda: client.post("/api/v1/purchases", json=purchase, headers=_headers(device)),
            lambda: client.post("/api/v1/consumptions", json=consumption, headers=_headers(device)),
        ]
    )

    with sqlite3.connect(db_path) as db:
        stock = _stock(db, product_id, location_id)
        purchase_audit = _event_audit(db, "purchase_events", "PurchaseEvent", [purchase_id])
        consumption_audit = _event_audit(db, "consumption_events", "ConsumptionEvent", [consumption_id])
        _assert_revision_counter_matches_log(db)

    actual = {
        "statuses": _status_codes(results),
        "purchase": purchase_audit,
        "consumption": consumption_audit,
        "stock": stock,
    }
    assert actual == {
        "statuses": [201, 201],
        "purchase": (1, 1, 1),
        "consumption": (1, 1, 1),
        "stock": 8,
    }, f"expected one +5 purchase, one -2 consumption, and stock 8; got {actual}"


def test_parallel_relocations_do_not_lose_source_decrements_or_double_count_stock(
    api_client_with_device, monkeypatch
):
    client, device = api_client_with_device
    product_id, source_id = _seed_stock(client, device, quantity=5)
    target_ids = [
        _create_location(client, device, "relocation-target-1", "Relocation target one"),
        _create_location(client, device, "relocation-target-2", "Relocation target two"),
    ]
    db_path = _database_path()
    event_ids = [test_uuid(f"parallel-relocation-event-{index}") for index in range(2)]
    payloads = [
        {
            "operationId": test_uuid(f"parallel-relocation-op-{index}"),
            "id": event_ids[index],
            "productId": product_id,
            "fromLocationId": source_id,
            "toLocationId": target_ids[index],
            "stockKind": "STK",
            "quantity": 1,
            "timestamp": 5_000 + index,
        }
        for index in range(2)
    ]

    results = _run_concurrently(
        [lambda payload=payload: client.post("/api/v1/relocations", json=payload, headers=_headers(device)) for payload in payloads]
    )

    with sqlite3.connect(db_path) as db:
        total_stock = _stock(db, product_id)
        source_stock = _stock(db, product_id, source_id)
        target_stock = [_stock(db, product_id, target_id) for target_id in target_ids]
        minimum_batch_quantity = _scalar(
            db,
            "SELECT COALESCE(MIN(remaining_quantity), 0) FROM batches WHERE product_id = ?",
            (product_id,),
        )
        event_count, event_log_count, distinct_event_revisions = _event_audit(
            db, "relocation_events", "RelocationEvent", event_ids
        )
        _assert_revision_counter_matches_log(db)

    actual = {
        "statuses": _status_codes(results),
        "events": event_count,
        "event_logs": event_log_count,
        "distinct_event_revisions": distinct_event_revisions,
        "source_stock": source_stock,
        "target_stock": target_stock,
        "total_stock": total_stock,
        "minimum_batch_quantity": minimum_batch_quantity,
    }
    assert actual == {
        "statuses": [201, 201],
        "events": 2,
        "event_logs": 2,
        "distinct_event_revisions": 2,
        "source_stock": 3,
        "target_stock": [1, 1],
        "total_stock": 5,
        "minimum_batch_quantity": 1,
    }, f"expected source=5-2 and conserved total stock without negatives; got {actual}"


def test_parallel_identical_operation_id_creates_one_consumption_and_one_change(
    api_client_with_device, monkeypatch
):
    client, device = api_client_with_device
    product_id, location_id = _seed_stock(client, device, quantity=2)
    db_path = _database_path()
    operation_id = test_uuid("duplicate-consumption-op")
    event_id = test_uuid("duplicate-consumption-event")
    payload = _consumption_payload(
        "duplicate-consumption-op",
        "duplicate-consumption-event",
        product_id,
        location_id,
        timestamp=6_000,
    )
    results = _run_concurrently(
        [
            lambda: client.post("/api/v1/consumptions", json=payload, headers=_headers(device)),
            lambda: client.post("/api/v1/consumptions", json=payload, headers=_headers(device)),
        ]
    )

    with sqlite3.connect(db_path) as db:
        stock = _stock(db, product_id, location_id)
        event_count, event_log_count, distinct_event_revisions = _event_audit(
            db, "consumption_events", "ConsumptionEvent", [event_id]
        )
        operation_count = _scalar(
            db, "SELECT COUNT(*) FROM processed_operations WHERE operation_id = ?", (operation_id,)
        )
        _assert_revision_counter_matches_log(db)

    actual = {
        "statuses": _status_codes(results),
        "events": event_count,
        "event_logs": event_log_count,
        "distinct_event_revisions": distinct_event_revisions,
        "processed_operations": operation_count,
        "stock": stock,
    }
    assert actual == {
        "statuses": [201, 201],
        "events": 1,
        "event_logs": 1,
        "distinct_event_revisions": 1,
        "processed_operations": 1,
        "stock": 1,
    }, f"expected two successful replays but one persisted mutation/change; got {actual}"
