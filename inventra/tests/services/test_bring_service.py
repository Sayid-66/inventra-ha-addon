from datetime import datetime, timedelta

import pytest

from inventra_backend.db.models import BringWatchOrigin, BringWatchState, BringWatchStateEnum, Product
from inventra_backend.services.bring_service import (
    CONFIRMATION_MIN_ATTEMPTS, CONFIRMATION_TIMEOUT,
    advance_on_list_confirmed, advance_pending_add, build_display_name, try_add_or_adopt,
)
from inventra_backend.services.bring_ha_client import HomeAssistantApiError
from inventra_backend.services.bring_service import evaluate_product, on_product_deleted, reconcile_once


class _FakeClient:
    def __init__(self, items):
        self.items = items
        self.added = []
        self.removed = []

    async def get_items(self):
        return self.items

    async def add_item(self, name):
        self.added.append(name)
        self.items.append({"summary": name, "uid": f"uid-{name}", "status": "needs_action"})

    async def remove_item(self, uid):
        self.removed.append(uid)


@pytest.mark.anyio
async def test_low_product_does_not_adopt_another_products_claimed_uid(db_session):
    other = Product(id="milk-a", name="Milch", min_stock=2)
    product = Product(id="milk-b", name="Milch", brand="Weihenstephan", min_stock=2)
    db_session.add_all([other, product, BringWatchState(
        product_id=other.id, state=BringWatchStateEnum.ON_LIST_CONFIRMED,
        origin=BringWatchOrigin.INVENTRA_CREATED, bring_item_name="Milch",
        bring_uid="X", retry_count=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )])
    db_session.flush()
    client = _FakeClient([{"summary": "Milch", "uid": "X", "status": "needs_action"}])

    await evaluate_product(db_session, client, product.id)

    watch = db_session.get(BringWatchState, product.id)
    assert watch.bring_uid != "X"
    assert watch.state == BringWatchStateEnum.PENDING_ADD
    assert watch.origin == BringWatchOrigin.INVENTRA_CREATED
    assert client.added == ["Weihenstephan Milch"]


@pytest.mark.anyio
async def test_name_conflict_normalizes_case_and_whitespace(db_session):
    other = Product(id="milk-a", name="Milch 1 l", min_stock=2)
    product = Product(id="milk-b", name="milch  1 l", min_stock=2)
    db_session.add_all([other, product, BringWatchState(
        product_id=other.id, state=BringWatchStateEnum.ON_LIST_CONFIRMED,
        origin=BringWatchOrigin.INVENTRA_CREATED, bring_item_name="Milch 1 l",
        bring_uid="X", retry_count=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )])
    db_session.flush()
    client = _FakeClient([])

    watch = await try_add_or_adopt(db_session, product, client)

    assert watch.state == BringWatchStateEnum.ERROR
    assert watch.last_error == "name_conflict_with_product_id=milk-a"
    assert watch.bring_uid is None
    assert client.added == []


@pytest.mark.parametrize("name,brand,variant,expected", [
    ("Wasser", None, None, "Wasser"),
    ("Knuspermüsli", "Vitalis", None, "Vitalis Knuspermüsli"),
    ("Cola", None, "Cherry", "Cola Cherry"),
    ("Cola", "Coca-Cola", "Cherry", "Coca-Cola Cola Cherry"),
    ("Vitalis Knuspermüsli", "vitalis", None, "Vitalis Knuspermüsli"),
    ("Cola Cherry", None, "cherry", "Cola Cherry"),
    ("Lange Beschreibung mit mehreren ganzen Wörtern und einem abschließenden Wort", None, None,
     "Lange Beschreibung mit mehreren ganzen Wörtern und einem"),
    (f"😀 {'a' * 58} b", None, None, f"😀 {'a' * 58}"),
])
def test_build_display_name(name, brand, variant, expected):
    assert build_display_name(name, brand, variant) == expected


@pytest.mark.anyio
async def test_try_add_or_adopt_uses_display_name_for_snapshot_and_confirmation(db_session):
    product = Product(id="p1", name="Knuspermüsli", brand="Vitalis", variant="Schoko", version=1, min_stock=3)
    db_session.add(product)
    db_session.flush()
    client = _FakeClient(items=[])

    watch = await try_add_or_adopt(db_session, product, client)

    assert client.added == ["Vitalis Knuspermüsli Schoko"]
    assert watch.bring_item_name == client.added[0]
    advance_pending_add(watch, client.items)
    assert watch.state == BringWatchStateEnum.ON_LIST_CONFIRMED


@pytest.mark.anyio
async def test_same_base_name_with_different_brands_does_not_conflict(db_session):
    other = Product(id="p2", name="Müsli", brand="Vitalis", version=1, min_stock=3)
    product = Product(id="p1", name="Müsli", brand="Alnatura", version=1, min_stock=3)
    db_session.add_all([other, product, BringWatchState(
        product_id="p2", state=BringWatchStateEnum.ON_LIST_CONFIRMED,
        origin=BringWatchOrigin.INVENTRA_CREATED, bring_item_name="Vitalis Müsli",
        bring_uid="u2", retry_count=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )])
    db_session.flush()

    client = _FakeClient(items=[])
    watch = await try_add_or_adopt(db_session, product, client)

    assert watch.state == BringWatchStateEnum.PENDING_ADD
    assert client.added == ["Alnatura Müsli"]


@pytest.mark.anyio
async def test_try_add_or_adopt_creates_pending_add_and_calls_add_item(db_session):
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.flush()
    client = _FakeClient(items=[])

    watch = await try_add_or_adopt(db_session, product, client)

    assert watch.state == BringWatchStateEnum.PENDING_ADD
    assert watch.origin == BringWatchOrigin.INVENTRA_CREATED
    assert watch.bring_uid is None
    assert client.added == ["Wasser"]


@pytest.mark.anyio
async def test_try_add_or_adopt_adopts_existing_needs_action_item_without_calling_add_item(db_session):
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.flush()
    client = _FakeClient(items=[{"summary": "Wasser", "uid": "existing-uid", "status": "needs_action"}])

    watch = await try_add_or_adopt(db_session, product, client)

    assert watch.state == BringWatchStateEnum.ON_LIST_CONFIRMED
    assert watch.origin == BringWatchOrigin.ADOPTED_EXISTING
    assert watch.bring_uid == "existing-uid"
    assert client.added == []


@pytest.mark.anyio
async def test_try_add_or_adopt_ignores_historical_completed_match_and_still_adds(db_session):
    """Correction from user review: a pre-existing `completed` entry with
    the same name must NOT be treated as 'already purchased' — only a
    uid Inventra itself confirmed may ever lock."""
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.flush()
    client = _FakeClient(items=[{"summary": "Wasser", "uid": "old-uid", "status": "completed"}])

    watch = await try_add_or_adopt(db_session, product, client)

    assert watch.state == BringWatchStateEnum.PENDING_ADD
    assert watch.origin == BringWatchOrigin.INVENTRA_CREATED
    assert client.added == ["Wasser"]


@pytest.mark.anyio
async def test_try_add_or_adopt_sets_error_on_name_conflict_with_another_product(db_session):
    other = Product(id="p2", name="Wasser", version=1, min_stock=3)
    db_session.add(other)
    db_session.add(BringWatchState(
        product_id="p2", state=BringWatchStateEnum.ON_LIST_CONFIRMED, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", bring_uid="u2", retry_count=0,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.flush()
    client = _FakeClient(items=[])

    watch = await try_add_or_adopt(db_session, product, client)

    assert watch.state == BringWatchStateEnum.ERROR
    assert watch.last_error == "name_conflict_with_product_id=p2"
    assert client.added == []


@pytest.mark.anyio
@pytest.mark.parametrize("content,added", [(400, True), (1200, False)])
async def test_evaluate_content_stock_in_packages(db_session, content, added):
    from inventra_backend.db.models import Batch, Location, Unit
    from inventra_backend.services.unit_normalizer import STANDARD_UNIT_IDS

    product = Product(
        id="content-product", name="Wasser", min_stock=2, quantity=500,
        unit=db_session.get(Unit, STANDARD_UNIT_IDS["ml"]), content_unit_label="ml",
    )
    db_session.add_all([
        product, Location(id="content-location", name="Keller", normalized_name="keller"),
        Batch(
            id="content-batch", product_id=product.id, location_id="content-location",
            event_timestamp=1000, is_content_tracked=True, content_unit_label="ml",
            remaining_quantity=content,
        ),
    ])
    db_session.flush()
    client = _FakeClient([])
    await evaluate_product(db_session, client, product.id)
    assert client.added == (["Wasser 500 ml"] if added else [])
    watch = db_session.get(BringWatchState, product.id)
    if added:
        assert watch.state == BringWatchStateEnum.PENDING_ADD
    else:
        assert watch is None


@pytest.mark.anyio
async def test_try_add_or_adopt_ignores_stale_bring_item_name_from_renamed_other_product(db_session):
    """A stale watch name after a rename must not conflict with a later product."""
    renamed_product = Product(id="pA", name="Mineralwasser", version=1, min_stock=3)
    db_session.add(renamed_product)
    db_session.add(BringWatchState(
        product_id="pA", state=BringWatchStateEnum.ON_LIST_CONFIRMED, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", bring_uid="uA", retry_count=0,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    product_b = Product(id="pB", name="Wasser", version=1, min_stock=3)
    db_session.add(product_b)
    db_session.flush()
    client = _FakeClient(items=[])

    watch = await try_add_or_adopt(db_session, product_b, client)

    assert watch.state != BringWatchStateEnum.ERROR
    assert watch.last_error is None or "name_conflict" not in watch.last_error
    assert watch.state == BringWatchStateEnum.PENDING_ADD
    assert watch.origin == BringWatchOrigin.INVENTRA_CREATED
    assert watch.bring_uid is None
    assert client.added == ["Wasser"]


@pytest.mark.parametrize("item_name", ["Wasser", "  WASSER  "])
def test_advance_pending_add_confirms_on_needs_action_match(item_name):
    watch = BringWatchState(
        product_id="p1", state=BringWatchStateEnum.PENDING_ADD, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", retry_count=2,
        confirmation_deadline_at=datetime.utcnow() + timedelta(minutes=10),
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    advance_pending_add(watch, [{"summary": item_name, "uid": "u1", "status": "needs_action"}])
    assert watch.state == BringWatchStateEnum.ON_LIST_CONFIRMED
    assert watch.bring_uid == "u1"


def test_advance_pending_add_stays_pending_before_timeout_even_with_enough_retries():
    watch = BringWatchState(
        product_id="p1", state=BringWatchStateEnum.PENDING_ADD, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", retry_count=CONFIRMATION_MIN_ATTEMPTS,
        confirmation_deadline_at=datetime.utcnow() + timedelta(minutes=5),  # not yet elapsed
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    advance_pending_add(watch, [])
    assert watch.state == BringWatchStateEnum.PENDING_ADD
    assert watch.retry_count == CONFIRMATION_MIN_ATTEMPTS + 1


def test_advance_pending_add_stays_pending_after_timeout_with_too_few_retries():
    watch = BringWatchState(
        product_id="p1", state=BringWatchStateEnum.PENDING_ADD, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", retry_count=1,
        confirmation_deadline_at=datetime.utcnow() - timedelta(minutes=1),  # already elapsed
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    advance_pending_add(watch, [])
    assert watch.state == BringWatchStateEnum.PENDING_ADD
    assert watch.retry_count == 2


def test_advance_pending_add_errors_only_when_both_conditions_met():
    watch = BringWatchState(
        product_id="p1", state=BringWatchStateEnum.PENDING_ADD, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", retry_count=CONFIRMATION_MIN_ATTEMPTS,
        confirmation_deadline_at=datetime.utcnow() - timedelta(minutes=1),
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    advance_pending_add(watch, [])
    assert watch.state == BringWatchStateEnum.ERROR
    assert watch.last_error == "add_item not confirmed within timeout"


def test_advance_on_list_confirmed_locks_on_completed():
    watch = BringWatchState(
        product_id="p1", state=BringWatchStateEnum.ON_LIST_CONFIRMED, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", bring_uid="u1", retry_count=0,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    advance_on_list_confirmed(watch, [{"summary": "Wasser", "uid": "u1", "status": "completed"}])
    assert watch.state == BringWatchStateEnum.LOCKED_PURCHASED
    assert watch.lock_reason == "completed"


def test_advance_on_list_confirmed_locks_when_uid_missing():
    watch = BringWatchState(
        product_id="p1", state=BringWatchStateEnum.ON_LIST_CONFIRMED, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", bring_uid="u1", retry_count=0,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    advance_on_list_confirmed(watch, [])
    assert watch.state == BringWatchStateEnum.LOCKED_PURCHASED
    assert watch.lock_reason == "missing_assumed_done"


def test_advance_on_list_confirmed_leaves_needs_action_untouched():
    watch = BringWatchState(
        product_id="p1", state=BringWatchStateEnum.ON_LIST_CONFIRMED, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", bring_uid="u1", retry_count=0,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    advance_on_list_confirmed(watch, [{"summary": "Wasser", "uid": "u1", "status": "needs_action"}])
    assert watch.state == BringWatchStateEnum.ON_LIST_CONFIRMED


def _total_stock_row(product_id, name, min_stock, total_stk):
    # Minimal stand-in for a row from stock_query_service.build_summaries()
    return {
        "productId": product_id,
        "name": name,
        "totalStk": total_stk,
        "totalContent": None,
        "minStock": min_stock,
    }


@pytest.mark.anyio
async def test_evaluate_product_creates_pending_add_when_below_min_stock(db_session, monkeypatch):
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.flush()
    monkeypatch.setattr(
        "inventra_backend.services.bring_service.build_summary_for_product",
        lambda db, product_id: _total_stock_row("p1", "Wasser", 3, 1),
    )
    client = _FakeClient(items=[])

    await evaluate_product(db_session, client, "p1")

    watch = db_session.get(BringWatchState, "p1")
    assert watch is not None
    assert watch.state == BringWatchStateEnum.PENDING_ADD


@pytest.mark.anyio
async def test_evaluate_product_removes_watch_row_when_stock_recovers(db_session, monkeypatch):
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.add(BringWatchState(
        product_id="p1", state=BringWatchStateEnum.PENDING_ADD, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", retry_count=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db_session.flush()
    monkeypatch.setattr(
        "inventra_backend.services.bring_service.build_summary_for_product",
        lambda db, product_id: _total_stock_row("p1", "Wasser", 3, 5),
    )
    client = _FakeClient(items=[])

    await evaluate_product(db_session, client, "p1")

    assert db_session.get(BringWatchState, "p1") is None


@pytest.mark.anyio
async def test_evaluate_product_removes_confirmed_inventra_created_item_from_bring_when_stock_recovers(
    db_session, monkeypatch,
):
    """A recovered Inventra-created item is removed from the real Bring! list."""
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.add(BringWatchState(
        product_id="p1", state=BringWatchStateEnum.ON_LIST_CONFIRMED,
        origin=BringWatchOrigin.INVENTRA_CREATED, bring_item_name="Wasser", bring_uid="uid-Wasser",
        retry_count=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db_session.flush()
    monkeypatch.setattr(
        "inventra_backend.services.bring_service.build_summary_for_product",
        lambda db, product_id: _total_stock_row("p1", "Wasser", 3, 5),
    )
    client = _FakeClient(items=[])

    await evaluate_product(db_session, client, "p1")

    assert db_session.get(BringWatchState, "p1") is None
    assert client.removed == ["uid-Wasser"]


@pytest.mark.anyio
async def test_evaluate_product_does_not_remove_adopted_existing_item_from_bring_when_stock_recovers(
    db_session, monkeypatch,
):
    """A recovered adopted Bring! item is never removed from the real list."""
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.add(BringWatchState(
        product_id="p1", state=BringWatchStateEnum.ON_LIST_CONFIRMED,
        origin=BringWatchOrigin.ADOPTED_EXISTING, bring_item_name="Wasser", bring_uid="uid-Wasser",
        retry_count=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db_session.flush()
    monkeypatch.setattr(
        "inventra_backend.services.bring_service.build_summary_for_product",
        lambda db, product_id: _total_stock_row("p1", "Wasser", 3, 5),
    )
    client = _FakeClient(items=[])

    await evaluate_product(db_session, client, "p1")

    assert db_session.get(BringWatchState, "p1") is None
    assert client.removed == []


@pytest.mark.anyio
async def test_evaluate_product_leaves_locked_purchased_untouched_while_still_below_min_stock(
    db_session, monkeypatch,
):
    """A purchased lock is cleared only by Task 6's stock-increase trigger."""
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.add(BringWatchState(
        product_id="p1", state=BringWatchStateEnum.LOCKED_PURCHASED, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", bring_uid="u1", lock_reason="completed", retry_count=0,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db_session.flush()
    monkeypatch.setattr(
        "inventra_backend.services.bring_service.build_summary_for_product",
        lambda db, product_id: _total_stock_row("p1", "Wasser", 3, 1),
    )
    client = _FakeClient(items=[])

    await evaluate_product(db_session, client, "p1")

    watch = db_session.get(BringWatchState, "p1")
    assert watch.state == BringWatchStateEnum.LOCKED_PURCHASED


@pytest.mark.anyio
async def test_evaluate_product_removes_watch_row_when_min_stock_is_none(db_session, monkeypatch):
    product = Product(id="p1", name="Wasser", version=1, min_stock=None)
    db_session.add(product)
    db_session.add(BringWatchState(
        product_id="p1", state=BringWatchStateEnum.PENDING_ADD, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", retry_count=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db_session.flush()
    client = _FakeClient(items=[])

    await evaluate_product(db_session, client, "p1")

    assert db_session.get(BringWatchState, "p1") is None


def test_on_product_deleted_removes_watch_row(db_session):
    db_session.add(BringWatchState(
        product_id="p1", state=BringWatchStateEnum.LOCKED_PURCHASED, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", bring_uid="u1", retry_count=0,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db_session.flush()

    on_product_deleted(db_session, "p1")

    assert db_session.get(BringWatchState, "p1") is None


@pytest.mark.anyio
async def test_reconcile_once_advances_all_watched_rows_from_one_snapshot(db_session, monkeypatch):
    product = Product(id="p1", name="Wasser", version=1, min_stock=3)
    db_session.add(product)
    db_session.add(BringWatchState(
        product_id="p1", state=BringWatchStateEnum.ON_LIST_CONFIRMED, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name="Wasser", bring_uid="u1", retry_count=0,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db_session.commit()
    monkeypatch.setattr(
        "inventra_backend.services.bring_service.build_summaries",
        lambda db: [_total_stock_row("p1", "Wasser", 3, 1)],
    )
    client = _FakeClient(items=[{"summary": "Wasser", "uid": "u1", "status": "completed"}])

    await reconcile_once(db_session, client)

    watch = db_session.get(BringWatchState, "p1")
    assert watch.state == BringWatchStateEnum.LOCKED_PURCHASED


@pytest.mark.anyio
async def test_reconcile_once_does_not_crash_when_ha_unreachable(db_session):
    class _BrokenClient:
        async def get_items(self):
            raise HomeAssistantApiError("down")

    await reconcile_once(db_session, _BrokenClient())  # must not raise


@pytest.mark.parametrize("name,amount,unit,expected", [
    ("Rügenwalder Bratwurst", 400, "g", "Rügenwalder Bratwurst 400 g"),
    ("Wurst 400 g", 400, "g", "Wurst 400 g"),
    ("Wasser 1,5l", 1.5, "l", "Wasser 1,5l"),
    ("Wasser", 1.5, "l", "Wasser 1.5 l"),
    ("Wurst 1400 g", 400, "g", "Wurst 1400 g 400 g"),
])
def test_display_name_package_size(name, amount, unit, expected):
    assert build_display_name(name, None, None, amount, unit) == expected


def test_display_size_is_capped_with_name():
    result = build_display_name("Wasser " * 8, None, None, 400, "ml")
    assert len(result) <= 60
    assert len(build_display_name("W" * 80, None, None, 400, "ml")) == 60


@pytest.mark.anyio
async def test_bring_package_sizes_distinguish_conflicts(db_session):
    from inventra_backend.db.models import Unit
    from inventra_backend.services.unit_normalizer import STANDARD_UNIT_IDS

    unit = db_session.get(Unit, STANDARD_UNIT_IDS["g"])
    first = Product(id="size-1", name="Wurst", quantity=400, unit=unit)
    second = Product(id="size-2", name="Wurst", quantity=500, unit=unit)
    duplicate = Product(id="size-3", name="Wurst", quantity=400, unit=unit)
    db_session.add_all([first, second, duplicate])
    db_session.flush()
    client = _FakeClient([])
    await try_add_or_adopt(db_session, first, client)
    await try_add_or_adopt(db_session, second, client)
    watch = await try_add_or_adopt(db_session, duplicate, client)
    assert client.added == ["Wurst 400 g", "Wurst 500 g"]
    assert watch.state == BringWatchStateEnum.ERROR


@pytest.mark.parametrize("name", ["  WATER   bottle ", "water\tbottle"])
def test_find_by_name_normalization(name):
    from inventra_backend.services.bring_service import _find_by_name
    item = {"summary": name, "uid": "u", "status": "needs_action"}
    assert _find_by_name([item], "Water Bottle") is item
    assert _find_by_name([item], "Water") is None


@pytest.mark.anyio
@pytest.mark.parametrize("name,status,adopted", [
    ("milch", "needs_action", True),
    (" WEIHENSTEPHAN  milch ", "needs_action", True),
    ("Weihenstephan Milch 1 l", "needs_action", True),
    ("milch", "completed", False),
    ("Milch Schokolade", "needs_action", False),
])
async def test_manual_milk_adoption(db_session, name, status, adopted):
    from inventra_backend.db.models import Unit
    from inventra_backend.services.unit_normalizer import STANDARD_UNIT_IDS
    product = Product(id="milk", name="Milch", brand="Weihenstephan", quantity=1,
                      unit=db_session.get(Unit, STANDARD_UNIT_IDS["l"]), min_stock=2)
    db_session.add(product)
    db_session.flush()
    client = _FakeClient([
        {"summary": "milch", "uid": "old", "status": "completed"},
        {"summary": name, "uid": "manual", "status": status},
    ])
    watch = await try_add_or_adopt(db_session, product, client)
    if adopted:
        assert watch.state == BringWatchStateEnum.ON_LIST_CONFIRMED
        assert watch.origin == BringWatchOrigin.ADOPTED_EXISTING
        assert watch.bring_item_name == name
        assert watch.bring_uid == "manual"
        assert client.added == []
    else:
        assert watch.state == BringWatchStateEnum.PENDING_ADD
        assert client.added == ["Weihenstephan Milch 1 l"]


@pytest.mark.anyio
@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("stock", [1, 5])
async def test_evaluate_summary_paths(db_session, monkeypatch, supplied, stock):
    db_session.add(Product(id="p1", name="Wasser", min_stock=3))
    db_session.flush()
    summary = _total_stock_row("p1", "Wasser", 3, stock)
    calls = []
    def load(db, product_id):
        calls.append(product_id)
        return summary
    monkeypatch.setattr("inventra_backend.services.bring_service.build_summary_for_product", load)
    client = _FakeClient([])
    await evaluate_product(db_session, client, "p1", summary=summary if supplied else None)
    assert calls == ([] if supplied else ["p1"])
    assert client.added == (["Wasser"] if stock == 1 else [])
    watch = db_session.get(BringWatchState, "p1")
    assert (watch.state if watch else None) == (BringWatchStateEnum.PENDING_ADD if stock == 1 else None)


@pytest.mark.anyio
async def test_reconcile_reuses_summary(db_session, monkeypatch):
    db_session.add(Product(id="p1", name="Wasser", min_stock=3))
    db_session.commit()
    def unexpected(*args):
        pytest.fail("reconcile must reuse summary")
    monkeypatch.setattr("inventra_backend.services.bring_service.build_summary_for_product", unexpected)
    client = _FakeClient([])
    await reconcile_once(db_session, client)
    await reconcile_once(db_session, client)
    assert client.added == ["Wasser"]
    assert db_session.get(BringWatchState, "p1").state == BringWatchStateEnum.ON_LIST_CONFIRMED


@pytest.mark.anyio
@pytest.mark.parametrize("origin,state,status,failure,expected", [
    (BringWatchOrigin.INVENTRA_CREATED, BringWatchStateEnum.ON_LIST_CONFIRMED, "needs_action", None, True),
    (BringWatchOrigin.INVENTRA_CREATED, BringWatchStateEnum.PENDING_ADD, "needs_action", None, True),
    (BringWatchOrigin.ADOPTED_EXISTING, BringWatchStateEnum.ON_LIST_CONFIRMED, "needs_action", None, False),
    (BringWatchOrigin.INVENTRA_CREATED, BringWatchStateEnum.LOCKED_PURCHASED, "needs_action", None, False),
    (BringWatchOrigin.INVENTRA_CREATED, BringWatchStateEnum.ERROR, "needs_action", None, False),
    (BringWatchOrigin.INVENTRA_CREATED, BringWatchStateEnum.ON_LIST_CONFIRMED, "completed", None, False),
    (BringWatchOrigin.INVENTRA_CREATED, BringWatchStateEnum.ON_LIST_CONFIRMED, "missing", None, False),
    (BringWatchOrigin.INVENTRA_CREATED, BringWatchStateEnum.ON_LIST_CONFIRMED, "needs_action", "fetch", False),
    (BringWatchOrigin.INVENTRA_CREATED, BringWatchStateEnum.ON_LIST_CONFIRMED, "needs_action", "remove", False),
])
async def test_product_deletion_external_cleanup(db_session, monkeypatch, origin, state, status, failure, expected):
    from inventra_backend.services import bring_service
    monkeypatch.setattr(bring_service, "get_engine", lambda: db_session.get_bind())

    db_session.add(Product(id="cleanup", name="Milk", deleted_at=datetime.utcnow()))
    db_session.flush()
    db_session.add(BringWatchState(
        product_id="cleanup", origin=origin, state=state, bring_item_name=" MILK  ",
        bring_uid=None if state == BringWatchStateEnum.PENDING_ADD else "milk-uid",
        retry_count=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db_session.commit()

    class Client(_FakeClient):
        async def get_items(self):
            if failure == "fetch":
                raise HomeAssistantApiError("unavailable")
            return await super().get_items()

        async def remove_item(self, uid):
            if failure == "remove":
                raise HomeAssistantApiError("unavailable")
            await super().remove_item(uid)
            self.items[:] = [item for item in self.items if item["uid"] != uid]

    client = Client([] if status == "missing" else [{"summary": "milk", "uid": "milk-uid", "status": status}])
    monkeypatch.setattr(bring_service, "BringHaClient", lambda **kwargs: client)
    await bring_service.on_product_deleted_with_cleanup("cleanup")
    db_session.expire_all()
    assert db_session.get(BringWatchState, "cleanup") is None
    assert client.removed == (["milk-uid"] if expected else [])
    assert client.items == ([] if expected or status == "missing" else [{"summary": "milk", "uid": "milk-uid", "status": status}])


@pytest.mark.anyio
async def test_product_deletion_without_watch_does_not_construct_client(db_session, monkeypatch):
    from inventra_backend.services import bring_service
    monkeypatch.setattr(bring_service, "get_engine", lambda: db_session.get_bind())
    def unexpected(**kwargs):
        pytest.fail("No watch means no HA call")
    monkeypatch.setattr(bring_service, "BringHaClient", unexpected)
    await bring_service.on_product_deleted_with_cleanup("absent")



def _cleanup_watch(product_id, state=BringWatchStateEnum.PENDING_ADD, uid=None,
                   origin=BringWatchOrigin.INVENTRA_CREATED):
    return BringWatchState(
        product_id=product_id, state=state, origin=origin, bring_item_name="Milch",
        bring_uid=uid, retry_count=0, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("state", [BringWatchStateEnum.PENDING_ADD, BringWatchStateEnum.ON_LIST_CONFIRMED])
async def test_cleanup_and_confirmation_exclude_foreign_uid(db_session, monkeypatch, state):
    from inventra_backend.services import bring_service
    db_session.add_all([Product(id="a", name="Milch"), Product(id="b", name="Milch")])
    db_session.flush()
    owner = _cleanup_watch("a", BringWatchStateEnum.ON_LIST_CONFIRMED, "u1", BringWatchOrigin.ADOPTED_EXISTING)
    pending = _cleanup_watch("b")
    db_session.add_all([owner, pending])
    db_session.commit()
    client = _FakeClient([{"summary": "Milch", "uid": "u1", "status": "needs_action"}])
    advance_pending_add(pending, client.items)
    assert pending.bring_uid is None
    assert pending.state == BringWatchStateEnum.PENDING_ADD
    pending.state = state
    pending.bring_uid = "u1" if state == BringWatchStateEnum.ON_LIST_CONFIRMED else None
    db_session.commit()
    monkeypatch.setattr(bring_service, "get_engine", lambda: db_session.get_bind())
    monkeypatch.setattr(bring_service, "BringHaClient", lambda **kwargs: client)
    await bring_service.on_product_deleted_with_cleanup("b")
    db_session.expire_all()
    assert db_session.get(BringWatchState, "b") is None
    assert db_session.get(BringWatchState, "a").bring_uid == "u1"
    assert client.removed == []


@pytest.mark.anyio
async def test_cleanup_waits_for_reconcile_lock(db_session, monkeypatch):
    import asyncio
    from inventra_backend.services import bring_service
    db_session.add(Product(id="b", name="Milch", min_stock=0))
    db_session.flush()
    db_session.add(_cleanup_watch("b"))
    db_session.commit()
    entered, release, cleanup_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class ReconcileClient(_FakeClient):
        async def get_items(self):
            entered.set()
            await asyncio.wait_for(release.wait(), 2)
            return self.items
    class CleanupClient(_FakeClient):
        async def get_items(self):
            cleanup_entered.set()
            return self.items
    client = CleanupClient([{"summary": "Milch", "uid": "u1", "status": "needs_action"}])
    monkeypatch.setattr(bring_service, "get_engine", lambda: db_session.get_bind())
    monkeypatch.setattr(bring_service, "BringHaClient", lambda **kwargs: client)
    reconcile = asyncio.create_task(reconcile_once(db_session, ReconcileClient([])))
    cleanup = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        cleanup = asyncio.create_task(bring_service.on_product_deleted_with_cleanup("b"))
        await asyncio.sleep(0.02)
        assert not cleanup.done()
        assert not cleanup_entered.is_set()
        release.set()
        await asyncio.wait_for(asyncio.gather(reconcile, cleanup), 2)
        assert cleanup_entered.is_set()
        db_session.expire_all()
        assert db_session.get(BringWatchState, "b") is None
    finally:
        release.set()
        for task in (reconcile, cleanup):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (reconcile, cleanup) if task is not None), return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("missing", [False, True])
async def test_reconcile_sweeps_orphan_watch_with_empty_list(db_session, missing):
    if not missing:
        db_session.add(Product(id="orphan", name="Milch", deleted_at=datetime.utcnow()))
        db_session.flush()
    db_session.add(_cleanup_watch("orphan"))
    db_session.commit()
    client = _FakeClient([])
    await reconcile_once(db_session, client)
    assert db_session.get(BringWatchState, "orphan") is None
    assert client.added == client.removed == []


@pytest.mark.anyio
async def test_reconcile_refreshes_product_deleted_after_summaries(db_session, monkeypatch):
    from sqlalchemy.orm import Session
    from inventra_backend.services import bring_service
    first = Product(id="first", name="Wasser", min_stock=2)
    victim = Product(id="victim", name="Milch", min_stock=2)
    db_session.add_all([first, victim])
    db_session.commit()
    original = bring_service.build_summaries
    def summaries(db):
        rows = original(db)
        # Ensure victim remains cached with its pre-delete state.
        db.refresh(victim)
        return sorted(rows, key=lambda row: row["productId"])
    monkeypatch.setattr(bring_service, "build_summaries", summaries)
    original_evaluate = bring_service.evaluate_product
    class HookClient(_FakeClient):
        async def get_items(self):
            # Release BEGIN IMMEDIATE while preserving the stale identity map.
            expire = db_session.expire_on_commit
            db_session.expire_on_commit = False
            try:
                db_session.commit()
            finally:
                db_session.expire_on_commit = expire
            assert victim.deleted_at is None
            with Session(db_session.get_bind()) as second:
                second.get(Product, "victim").deleted_at = datetime.utcnow()
                second.commit()
            return await super().get_items()
    hook = HookClient([])
    async def evaluate(db, client, product_id, summary=None):
        if product_id == "first":
            await hook.get_items()
        await original_evaluate(db, client, product_id, summary)
    monkeypatch.setattr(bring_service, "evaluate_product", evaluate)
    client = _FakeClient([])
    await reconcile_once(db_session, client)
    assert client.added == ["Wasser"]
    assert db_session.get(BringWatchState, "victim") is None



@pytest.mark.anyio
async def test_cleanup_closes_sessions_during_ha_and_logs_delete_failure(db_session, monkeypatch, caplog):
    from contextlib import contextmanager
    from sqlalchemy.orm import Session
    from inventra_backend.services import bring_service
    db_session.add(Product(id="cleanup-sessions", name="Milch"))
    db_session.flush()
    db_session.add(_cleanup_watch("cleanup-sessions"))
    db_session.commit()
    active = []
    @contextmanager
    def tracked_session(engine):
        with Session(engine) as session:
            active.append(session)
            try:
                yield session
            finally:
                active.remove(session)
    class Client(_FakeClient):
        async def get_items(self):
            assert not active
            return await super().get_items()
        async def remove_item(self, uid):
            assert not active
            await super().remove_item(uid)
    client = Client([{"summary": "Milch", "uid": "u1", "status": "needs_action"}])
    monkeypatch.setattr(bring_service, "Session", tracked_session)
    monkeypatch.setattr(bring_service, "get_engine", lambda: db_session.get_bind())
    monkeypatch.setattr(bring_service, "BringHaClient", lambda **kwargs: client)
    def fail_delete(db, product_id):
        assert len(active) == 1
        raise RuntimeError("delete failed")
    monkeypatch.setattr(bring_service, "on_product_deleted", fail_delete)
    await bring_service.on_product_deleted_with_cleanup("cleanup-sessions")
    assert client.removed == ["u1"]
    assert "bring watch deletion failed" in caplog.text
    assert not active


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["sweep", "scheduled"])
@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("state,origin,status,claimed,failure,removed", [
    (BringWatchStateEnum.PENDING_ADD, BringWatchOrigin.INVENTRA_CREATED, "needs_action", False, False, True),
    (BringWatchStateEnum.ON_LIST_CONFIRMED, BringWatchOrigin.INVENTRA_CREATED, "needs_action", False, False, True),
    (BringWatchStateEnum.ON_LIST_CONFIRMED, BringWatchOrigin.ADOPTED_EXISTING, "needs_action", False, False, False),
    (BringWatchStateEnum.LOCKED_PURCHASED, BringWatchOrigin.INVENTRA_CREATED, "needs_action", False, False, False),
    (BringWatchStateEnum.ERROR, BringWatchOrigin.INVENTRA_CREATED, "needs_action", False, False, False),
    (BringWatchStateEnum.ON_LIST_CONFIRMED, BringWatchOrigin.INVENTRA_CREATED, "completed", False, False, False),
    (BringWatchStateEnum.PENDING_ADD, BringWatchOrigin.INVENTRA_CREATED, "needs_action", True, False, False),
    (BringWatchStateEnum.ON_LIST_CONFIRMED, BringWatchOrigin.INVENTRA_CREATED, "needs_action", True, False, False),
    (BringWatchStateEnum.ON_LIST_CONFIRMED, BringWatchOrigin.INVENTRA_CREATED, "needs_action", False, True, False),
])
async def test_deleted_product_paths_cleanup_before_row_deletion(
    db_session, monkeypatch, path, missing, state, origin, status, claimed, failure, removed,
):
    from inventra_backend.services import bring_service
    if not missing:
        db_session.add(Product(id="orphan", name="Milch", deleted_at=datetime.utcnow()))
        db_session.flush()
    db_session.add(_cleanup_watch("orphan", state, None if state == BringWatchStateEnum.PENDING_ADD else "u1", origin))
    if claimed:
        db_session.add(Product(id="owner", name="Milk", min_stock=0))
        db_session.flush()
        db_session.add(_cleanup_watch("owner", BringWatchStateEnum.ON_LIST_CONFIRMED, "u1", BringWatchOrigin.ADOPTED_EXISTING))
    db_session.commit()

    class Client(_FakeClient):
        async def remove_item(self, uid):
            if failure:
                raise HomeAssistantApiError("remove failed")
            await super().remove_item(uid)
            self.items[:] = [item for item in self.items if item["uid"] != uid]

    item = {"summary": "  MILCH  ", "uid": "u1", "status": status}
    client = Client([item])
    monkeypatch.setattr(bring_service, "get_engine", lambda: db_session.get_bind())
    monkeypatch.setattr(bring_service, "BringHaClient", lambda **kwargs: client)
    if path == "sweep":
        await reconcile_once(db_session, client)
        # The delayed deletion callback must remain safe after the sweep.
        await bring_service.on_product_deleted_with_cleanup("orphan")
    else:
        await bring_service.schedule_stock_change("orphan", increased=False)
    db_session.expire_all()
    assert db_session.get(BringWatchState, "orphan") is None
    assert client.removed == (["u1"] if removed else [])
    assert client.items == ([] if removed else [item])
    if claimed:
        assert db_session.get(BringWatchState, "owner").bring_uid == "u1"


@pytest.mark.anyio
async def test_sweep_does_not_adopt_removed_item_from_snapshot(db_session):
    db_session.add_all([Product(id="orphan", name="Milch", deleted_at=datetime.utcnow()),
                        Product(id="replacement", name="Milch", min_stock=2)])
    db_session.flush()
    db_session.add(_cleanup_watch("orphan", BringWatchStateEnum.ON_LIST_CONFIRMED, "u1"))
    db_session.commit()
    client = _FakeClient([{"summary": "Milch", "uid": "u1", "status": "needs_action"}])
    await reconcile_once(db_session, client)
    assert client.removed == ["u1"]
    assert client.added == ["Milch"]
    assert db_session.get(BringWatchState, "replacement").state == BringWatchStateEnum.PENDING_ADD
