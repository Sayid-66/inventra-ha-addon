from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import os
import re
from typing import Optional
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from .bring_ha_client import BringHaClient, HomeAssistantApiError
from .stock_query_service import build_summaries, build_summary_for_product, stock_packs
from ..config import get_settings
from ..db.base import get_engine
from ..db.models import BringWatchOrigin, BringWatchState, BringWatchStateEnum, Product, Unit

CONFIRMATION_MIN_ATTEMPTS = 3
CONFIRMATION_TIMEOUT = timedelta(minutes=30)
HA_CORE_API_BASE = "http://supervisor/core/api"

logger = logging.getLogger(__name__)
_reconcile_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()


def _get_reconcile_lock() -> asyncio.Lock:
    """Serialize Bring operations within the current loop, never across loops."""
    loop = asyncio.get_running_loop()
    lock = _reconcile_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _reconcile_locks[loop] = lock
    return lock


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _find_by_name(items: list[dict], name: str) -> Optional[dict]:
    normalized_name = _normalize(name)
    for item in items:
        if _normalize(item["summary"]) == normalized_name:
            return item
    return None


def _find_by_uid(items: list[dict], uid: str) -> Optional[dict]:
    for item in items:
        if item["uid"] == uid:
            return item
    return None


def build_display_name(name: str, brand: str | None, variant: str | None, quantity: float | None = None, unit_abbreviation: str | None = None) -> str:
    result = name.strip()
    variant = variant.strip() if variant else ""
    brand = brand.strip() if brand else ""
    if variant and variant.lower() not in result.lower():
        result = f"{result} {variant}".strip()
    if brand and brand.lower() not in result.lower():
        result = f"{brand} {result}".strip()
    if quantity is not None and unit_abbreviation:
        amount = format(quantity, ".15g")
        size = f"{amount} {unit_abbreviation}"
        # Decimal comma and optional spacing are equivalent package spellings.
        number_pattern = re.escape(amount).replace(r"\.", r"[.,]")
        pattern = rf"(?<![\d.,]){number_pattern}\s*{re.escape(unit_abbreviation)}(?!\w)"
        if not re.search(pattern, result, re.IGNORECASE):
            result = f"{result} {size}".strip()
    if len(result) <= 60:
        return result
    words = result.split()
    shortened = words[0]
    if quantity is not None and unit_abbreviation:
        shortened = shortened[:60]
    for word in words[1:]:
        if len(shortened) + 1 + len(word) > 60:
            break
        shortened += f" {word}"
    return shortened


def _find_name_conflict(db: Session, product_id: str, name: str) -> Optional[str]:
    """§4.5: any OTHER product with an active bring_watch_state row
    whose owning product's current display name matches blocks a new automatic add.

    Compare against the current Product fields, rather than the watch row's
    bring_item_name snapshot, so a stale snapshot left by a rename cannot
    block a later product that legitimately reuses the old name.
    """
    for other_id, other_name, other_brand, other_variant, amount, abbreviation in db.execute(
        select(BringWatchState.product_id, Product.name, Product.brand, Product.variant, Product.quantity, Unit.abbreviation)
        .join(Product, Product.id == BringWatchState.product_id)
        .outerjoin(Unit, Unit.id == Product.unit_id)
        .where(BringWatchState.product_id != product_id)
    ):
        if _normalize(build_display_name(other_name, other_brand, other_variant, amount, abbreviation)) == _normalize(name):
            return other_id
    return None


async def try_add_or_adopt(db: Session, product: Product, client: BringHaClient) -> BringWatchState:
    """Called only when no bring_watch_state row exists yet for this
    product and its stock is below min_stock (spec §4.2/§4.3/§4.5)."""
    item_name = build_display_name(product.name, product.brand, product.variant, product.quantity, product.unit.abbreviation if product.unit else None)
    conflict_product_id = _find_name_conflict(db, product.id, item_name)
    if conflict_product_id is not None:
        watch = BringWatchState(
            product_id=product.id, state=BringWatchStateEnum.ERROR, origin=BringWatchOrigin.INVENTRA_CREATED,
            bring_item_name=item_name, bring_uid=None, retry_count=0, confirmation_deadline_at=None,
            last_error=f"name_conflict_with_product_id={conflict_product_id}",
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        db.add(watch)
        db.flush()
        return watch

    try:
        items = await client.get_items()
    except HomeAssistantApiError:
        # HA unreachable right now: create nothing, the next periodic
        # reconcile cycle will retry from a clean slate (spec §5).
        return None

    claimed_uids = set(db.execute(
        select(BringWatchState.bring_uid).where(BringWatchState.bring_uid.is_not(None))
    ).scalars())
    actionable_items = [
        item for item in items
        if item["status"] == "needs_action" and item["uid"] not in claimed_uids
    ]
    candidate_names = [item_name, product.name, f"{product.brand or ''} {product.name}"]
    existing = next(
        (match for name in candidate_names if (match := _find_by_name(actionable_items, name)) is not None),
        None,
    )
    if existing is not None:
        watch = BringWatchState(
            product_id=product.id, state=BringWatchStateEnum.ON_LIST_CONFIRMED, origin=BringWatchOrigin.ADOPTED_EXISTING,
            bring_item_name=existing["summary"], bring_uid=existing["uid"], retry_count=0, confirmation_deadline_at=None,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        db.add(watch)
        db.flush()
        return watch

    # A historical `completed` match with this name is deliberately
    # ignored here — only a uid WE later confirm may ever lock (§4.2).
    watch = BringWatchState(
        product_id=product.id, state=BringWatchStateEnum.PENDING_ADD, origin=BringWatchOrigin.INVENTRA_CREATED,
        bring_item_name=item_name, bring_uid=None, retry_count=0,
        confirmation_deadline_at=datetime.utcnow() + CONFIRMATION_TIMEOUT,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(watch)
    db.flush()  # persisted BEFORE the external call — crash safety, spec §4.3

    try:
        await client.add_item(item_name)
    except HomeAssistantApiError:
        pass  # harmless: next reconcile cycle retries add_item (Bring! merges by name)
    return watch


def _claimed_by_others(db: Session, product_id: str) -> set[str]:
    return set(db.scalars(select(BringWatchState.bring_uid).where(
        BringWatchState.product_id != product_id,
        BringWatchState.bring_uid.is_not(None),
    )))


def advance_pending_add(watch: BringWatchState, items: list[dict]) -> None:
    db = object_session(watch)
    claimed = _claimed_by_others(db, watch.product_id) if db is not None else set()
    match = _find_by_name([
        item for item in items
        if item["uid"] not in claimed and item["status"] == "needs_action"
    ], watch.bring_item_name)
    watch.last_checked_at = datetime.utcnow()
    if match is not None and match["status"] == "needs_action":
        watch.state = BringWatchStateEnum.ON_LIST_CONFIRMED
        watch.bring_uid = match["uid"]
        watch.updated_at = datetime.utcnow()
        return
    watch.retry_count += 1
    watch.updated_at = datetime.utcnow()
    deadline_elapsed = watch.confirmation_deadline_at is not None and datetime.utcnow() >= watch.confirmation_deadline_at
    if watch.retry_count >= CONFIRMATION_MIN_ATTEMPTS and deadline_elapsed:
        watch.state = BringWatchStateEnum.ERROR
        watch.last_error = "add_item not confirmed within timeout"


def advance_on_list_confirmed(watch: BringWatchState, items: list[dict]) -> None:
    watch.last_checked_at = datetime.utcnow()
    match = _find_by_uid(items, watch.bring_uid)
    if match is None:
        watch.state = BringWatchStateEnum.LOCKED_PURCHASED
        watch.lock_reason = "missing_assumed_done"
        watch.updated_at = datetime.utcnow()
    elif match["status"] == "completed":
        watch.state = BringWatchStateEnum.LOCKED_PURCHASED
        watch.lock_reason = "completed"
        watch.updated_at = datetime.utcnow()
    # else: still needs_action, nothing changes


def on_product_deleted(db: Session, product_id: str) -> None:
    """Delete only the local watch row; callers handle external cleanup."""
    watch = db.get(BringWatchState, product_id)
    if watch is not None:
        db.delete(watch)
        db.flush()


async def _remove_inventra_item_for_row(client, fields_or_row, claimed_uids: set[str]) -> None:
    """Best-effort cleanup, preserving items owned by other watch rows."""
    origin = fields_or_row.origin
    state = fields_or_row.state
    if origin != BringWatchOrigin.INVENTRA_CREATED or state not in (
        BringWatchStateEnum.ON_LIST_CONFIRMED, BringWatchStateEnum.PENDING_ADD,
    ):
        return
    try:
        items = await client.get_items()
        actionable = [item for item in items
                      if item["status"] == "needs_action" and item["uid"] not in claimed_uids]
        if state == BringWatchStateEnum.ON_LIST_CONFIRMED:
            uid = fields_or_row.bring_uid
            match = _find_by_uid(actionable, uid) if uid else None
        else:
            match = _find_by_name(actionable, fields_or_row.bring_item_name)
        if match is not None:
            await client.remove_item(match["uid"])
    except Exception as exc:
        logger.warning("bring deletion item cleanup failed for %s: %s", fields_or_row.bring_item_name, exc)


async def evaluate_product(db: Session, client: BringHaClient, product_id: str, summary: dict | None = None) -> None:
    """Single entry point for both the periodic reconcile (Task 5) and
    the immediate per-event triggers (Task 6). Implements the
    min_stock-changed/deactivated cleanup rules from spec section 4.4.
    Never clears a LOCKED_PURCHASED or ERROR row on its own -- only an
    explicit stock-increase trigger (on_stock_increase, Task 6) may do
    that, otherwise the very next cycle would re-add immediately."""
    product = db.execute(
        select(Product).where(Product.id == product_id).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    watch = db.get(BringWatchState, product_id)
    if product is None or product.deleted_at is not None:
        if watch is not None and client is not None:
            await _remove_inventra_item_for_row(client, watch, _claimed_by_others(db, product_id))
        on_product_deleted(db, product_id)
        return
    if product.min_stock in (None, 0):
        if watch is not None:
            db.delete(watch)
            db.flush()
        return

    if summary is None:
        summary = build_summary_for_product(db, product_id)
    if summary is None:
        return
    total = stock_packs(product, summary)

    if total >= product.min_stock:
        if watch is not None and watch.state in (
            BringWatchStateEnum.PENDING_ADD,
            BringWatchStateEnum.ON_LIST_CONFIRMED,
        ):
            # Only remove items Inventra created and confirmed by uid; an HA
            # failure must not block local cleanup or affect adopted items.
            if (
                watch.state == BringWatchStateEnum.ON_LIST_CONFIRMED
                and watch.origin == BringWatchOrigin.INVENTRA_CREATED
                and watch.bring_uid is not None
            ):
                try:
                    await client.remove_item(watch.bring_uid)
                except HomeAssistantApiError as exc:
                    logger.warning("bring item removal failed for product %s: %s", product_id, exc)
            db.delete(watch)
            db.flush()
        return

    if watch is None:
        await try_add_or_adopt(db, product, client)
        db.flush()
    # PENDING_ADD/ON_LIST_CONFIRMED rows are advanced by reconcile_once
    # against the shared get_items snapshot, not here. LOCKED_PURCHASED/
    # ERROR rows stay untouched until a stock-increase trigger clears
    # them (Task 6) -- that is the whole point of the lock (spec section 4.4).


class _SnapshotClient:
    """Reuse one HA list snapshot while retaining Task 4's add behavior."""

    def __init__(self, client: BringHaClient, items: list[dict]):
        self._client = client
        self._items = items

    async def get_items(self) -> list[dict]:
        return self._items

    async def add_item(self, name: str) -> None:
        await self._client.add_item(name)

    async def remove_item(self, uid: str) -> None:
        await self._client.remove_item(uid)
        self._items[:] = [item for item in self._items if item["uid"] != uid]


async def reconcile_once(db: Session, client: BringHaClient) -> None:
    """Run one reconcile cycle using a single HA get_items snapshot."""
    async with _get_reconcile_lock():
        try:
            items = await client.get_items()
        except HomeAssistantApiError as exc:
            logger.warning("bring reconcile skipped, HA API unreachable: %s", exc)
            return

        orphaned = db.scalars(
            select(BringWatchState).outerjoin(Product, Product.id == BringWatchState.product_id)
            .where((Product.id.is_(None)) | (Product.deleted_at.is_not(None)))
        ).all()
        snapshot_client = _SnapshotClient(client, items)
        # Keep claims from all rows until every orphan has been considered.
        claims = {watch.product_id: _claimed_by_others(db, watch.product_id) for watch in orphaned}
        for watch in orphaned:
            await _remove_inventra_item_for_row(snapshot_client, watch, claims[watch.product_id])
            db.delete(watch)
        db.commit()

        for watch in db.execute(
            select(BringWatchState).where(BringWatchState.state == BringWatchStateEnum.PENDING_ADD)
        ).scalars().all():
            advance_pending_add(watch, items)
        for watch in db.execute(
            select(BringWatchState).where(BringWatchState.state == BringWatchStateEnum.ON_LIST_CONFIRMED)
        ).scalars().all():
            advance_on_list_confirmed(watch, items)
        db.commit()

        watched_ids = {row[0] for row in db.execute(select(BringWatchState.product_id)).all()}
        for summary in build_summaries(db):
            if summary["productId"] in watched_ids:
                continue
            await evaluate_product(db, snapshot_client, summary["productId"], summary=summary)
        db.commit()


def on_stock_increase(db: Session, product_id: str) -> None:
    """§4.4: a purchase, or a correction that raises the quantity,
    clears an existing LOCKED_PURCHASED/ERROR row so the next
    evaluation can decide fresh whether a new low-stock add is due."""
    watch = db.get(BringWatchState, product_id)
    if watch is not None and watch.state in (BringWatchStateEnum.LOCKED_PURCHASED, BringWatchStateEnum.ERROR):
        db.delete(watch)
        db.flush()


def on_product_deleted_sync(product_id: str) -> None:
    """Compatibility wrapper for row-only cleanup in a separate session."""
    with Session(get_engine()) as db:
        on_product_deleted(db, product_id)
        db.commit()


async def on_product_deleted_with_cleanup(product_id: str) -> None:
    """Best-effort removal of Inventra-created items after product deletion."""
    async with _get_reconcile_lock():
        try:
            with Session(get_engine()) as db:
                fields = db.execute(select(
                    BringWatchState.origin, BringWatchState.state,
                    BringWatchState.bring_uid, BringWatchState.bring_item_name,
                ).where(BringWatchState.product_id == product_id)).one_or_none()
                claimed = _claimed_by_others(db, product_id)
            if fields is not None:
                origin, state, uid, name = fields
                if origin == BringWatchOrigin.INVENTRA_CREATED and state in (
                    BringWatchStateEnum.ON_LIST_CONFIRMED, BringWatchStateEnum.PENDING_ADD,
                ):
                    settings = get_settings()
                    client = BringHaClient(
                        base_url=HA_CORE_API_BASE,
                        token=os.environ.get("SUPERVISOR_TOKEN", ""),
                        todo_entity_id=settings.bring_todo_entity_id,
                    )
                    await _remove_inventra_item_for_row(client, fields, claimed)
        except Exception as exc:
            logger.warning("bring deletion cleanup failed for product %s: %s", product_id, exc)
        finally:
            try:
                with Session(get_engine()) as db:
                    on_product_deleted(db, product_id)
                    db.commit()
            except Exception as exc:
                logger.warning("bring watch deletion failed for product %s: %s", product_id, exc)


async def schedule_stock_change(product_id: str, increased: bool) -> None:
    """BackgroundTasks entry point (spec §3.1). FastAPI runs background
    tasks only after the response has been produced, i.e. strictly
    after the triggering request's own `get_db` session has already
    committed — so this never races the booking it followed. Opens its
    own session and HA client; any failure here is logged and otherwise
    swallowed, since the booking that triggered it already succeeded."""
    settings = get_settings()
    client = BringHaClient(
        base_url=HA_CORE_API_BASE,
        token=os.environ.get("SUPERVISOR_TOKEN", ""),
        todo_entity_id=settings.bring_todo_entity_id,
    )
    async with _get_reconcile_lock():
        with Session(get_engine()) as db:
            try:
                if increased:
                    on_stock_increase(db, product_id)
                await evaluate_product(db, client, product_id)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("bring schedule_stock_change failed for product %s", product_id)


async def run_bring_reconcile_loop(interval_seconds: int) -> None:
    """Runs immediately (covers Inventra restart, spec §5.6), then every
    `interval_seconds`. Never lets one bad cycle kill the loop."""
    settings = get_settings()
    client = BringHaClient(
        base_url=HA_CORE_API_BASE,
        token=os.environ.get("SUPERVISOR_TOKEN", ""),
        todo_entity_id=settings.bring_todo_entity_id,
    )
    while True:
        try:
            with Session(get_engine()) as db:
                await reconcile_once(db, client)
        except Exception:
            logger.exception("bring reconcile loop iteration failed")
        await asyncio.sleep(interval_seconds)
