from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from math import ceil
import os
import re
from typing import Optional
from types import SimpleNamespace

from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.engine import Engine
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


def bring_quantity(product: Product, stock_packs: float) -> int:
    return max(1, ceil((product.min_stock or 0) - stock_packs))


def bring_description(product: Product, stock_packs: float) -> str:
    return f"{bring_quantity(product, stock_packs)} Stk."


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


async def try_add_or_adopt(db: Session, product: Product, client: BringHaClient) -> BringWatchState | None:
    product_id = product.id
    await evaluate_product(db, client, product_id, _force_add=True)
    return db.get(BringWatchState, product_id)


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
        watch.last_error = None
        watch.updated_at = datetime.utcnow()
        return
    watch.retry_count += 1
    watch.last_error = "ADD_UNCONFIRMED"
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


REMOVAL_MAX_ATTEMPTS = 10


def _watch_marker(watch):
    if watch is None:
        return None
    return (watch.state, watch.origin, watch.bring_uid, watch.bring_item_name,
            watch.updated_at, watch.retry_count, watch.last_error)


def _product_marker(db, product):
    if product is None:
        return None
    summary = build_summary_for_product(db, product.id)
    return (product.version, product.deleted_at, product.min_stock, product.name,
            product.brand, product.variant, product.quantity, product.unit_id,
            product.unit.abbreviation if product.unit else None, summary)


# DB access in these async plan/apply flows is synchronous and short.
# BEGIN IMMEDIATE may wait up to the 15 s busy_timeout under write contention.

def _plan(db, product_id, summary=None, force_add=False, reconcile=False):
    product = db.get(Product, product_id, populate_existing=True)
    watch = db.get(BringWatchState, product_id, populate_existing=True)
    marker = _product_marker(db, product)
    action = 'none'
    name = None
    conflict = None
    candidates = []
    description = None
    if product is None or product.deleted_at is not None:
        action = 'remove' if watch is not None else 'none'
    elif not force_add and product.min_stock in (None, 0):
        action = ('remove' if watch.state == BringWatchStateEnum.PENDING_ADD else
                  ('advance' if reconcile else 'clear')) if watch is not None else 'none'
    else:
        current = summary if summary is not None else marker[-1]
        if current is not None:
            description = bring_description(product, stock_packs(product, current))
        elif force_add:
            description = bring_description(product, 0)
        if not force_add and current is not None and stock_packs(product, current) >= product.min_stock:
            if watch is not None and watch.state in (
                BringWatchStateEnum.PENDING_ADD, BringWatchStateEnum.ON_LIST_CONFIRMED,
            ):
                action = 'remove'
        elif current is not None or force_add:
            if watch is None:
                action = 'create'
                name = build_display_name(product.name, product.brand, product.variant,
                                          product.quantity, product.unit.abbreviation if product.unit else None)
                conflict = _find_name_conflict(db, product_id, name)
                candidates = [name, product.name, f"{product.brand or ''} {product.name}"]
            else:
                action = 'advance'
    fields = None if watch is None else SimpleNamespace(
        state=watch.state, origin=watch.origin, bring_uid=watch.bring_uid,
        bring_item_name=watch.bring_item_name, last_error=watch.last_error)
    return SimpleNamespace(product_id=product_id, product_marker=marker,
                           watch_marker=_watch_marker(watch), fields=fields,
                           claimed=_claimed_by_others(db, product_id), action=action,
                           name=name, candidates=candidates, conflict=conflict,
                           description=description)


def _matches(db, plan):
    watch = db.get(BringWatchState, plan.product_id, populate_existing=True)
    product = db.get(Product, plan.product_id, populate_existing=True)
    return (_watch_marker(watch) == plan.watch_marker
            and _product_marker(db, product) == plan.product_marker
            and _claimed_by_others(db, plan.product_id) == plan.claimed
            and (plan.action != 'create' or
                 _find_name_conflict(db, plan.product_id, plan.name) == plan.conflict))


async def _remove_inventra_item_for_row(client, fields_or_row, claimed_uids: set[str], items=None) -> bool:
    """Return False only on failure; callers retain the row for a bounded retry."""
    if fields_or_row.origin != BringWatchOrigin.INVENTRA_CREATED or fields_or_row.state not in (
        BringWatchStateEnum.ON_LIST_CONFIRMED, BringWatchStateEnum.PENDING_ADD,
    ):
        return True
    try:
        if items is None:
            items = await client.get_items()
        actionable = [item for item in items
                      if item['status'] == 'needs_action' and item['uid'] not in claimed_uids]
        if fields_or_row.state == BringWatchStateEnum.ON_LIST_CONFIRMED:
            uid = fields_or_row.bring_uid
            match = _find_by_uid(actionable, uid) if uid else None
        else:
            match = _find_by_name(actionable, fields_or_row.bring_item_name)
        if match is not None:
            await client.remove_item(match['uid'])
            items[:] = [item for item in items if item['uid'] != match['uid']]
        return True
    except HomeAssistantApiError:
        # Never store/log HTTP exception text, which can contain credentials.
        return False


def _apply_removal(db, plan, success):
    watch = db.get(BringWatchState, plan.product_id)
    if success:
        on_product_deleted(db, plan.product_id)
        return
    # Count consecutive removal failures independently of add confirmations.
    count = watch.retry_count + 1 if (watch.last_error or '').startswith('REMOVE_FAILED') else 1
    watch.retry_count = count
    watch.last_error = 'REMOVE_FAILED'
    watch.updated_at = datetime.utcnow()
    if count >= REMOVAL_MAX_ATTEMPTS:
        logger.warning('bring removal gave up after %s attempts for product %s', count, plan.product_id)
        on_product_deleted(db, plan.product_id)


def _needs_items(plan):
    if plan.action == 'create':
        return plan.conflict is None
    if plan.action == 'remove':
        return (plan.fields.origin == BringWatchOrigin.INVENTRA_CREATED
                and plan.fields.state in (BringWatchStateEnum.PENDING_ADD,
                                          BringWatchStateEnum.ON_LIST_CONFIRMED))
    if plan.action == 'advance':
        return (plan.description is not None
                and plan.fields.origin == BringWatchOrigin.INVENTRA_CREATED
                and (plan.fields.state == BringWatchStateEnum.PENDING_ADD
                     or (plan.fields.state == BringWatchStateEnum.ON_LIST_CONFIRMED
                         and plan.fields.bring_uid is not None)))
    return False


async def _sync_quantity(client, plan, items):
    if not _needs_items(plan):
        return
    actionable = [item for item in items if item['status'] == 'needs_action'
                  and item['uid'] not in plan.claimed]
    if plan.fields.state == BringWatchStateEnum.PENDING_ADD:
        if _find_by_name([item for item in items if item['status'] == 'completed'],
                         plan.fields.bring_item_name) is not None:
            return
        match = _find_by_name(actionable, plan.fields.bring_item_name)
    else:
        match = _find_by_uid(actionable, plan.fields.bring_uid)
    if match is None or match.get('description') == plan.description:
        return
    try:
        await client.update_item(match['uid'], plan.description)
    except HomeAssistantApiError:
        logger.warning('bring quantity update failed for product %s', plan.product_id)
    else:
        match['description'] = plan.description


async def _execute_plan(engine, client, plan, items, advance):
    # All ORM work is confined to short context-managed sessions. No lazy ORM
    # objects cross the HA boundary; plans contain only values.
    if plan.action == 'none':
        return
    with Session(engine) as preflight:
        if not _matches(preflight, plan):
            return
    if plan.action == 'create':
        actionable = [item for item in items if item['status'] == 'needs_action'
                      and item['uid'] not in plan.claimed]
        existing = next((match for name in plan.candidates
                         if (match := _find_by_name(actionable, name)) is not None), None)
        with Session(engine) as db:
            if not _matches(db, plan):
                return
            now = datetime.utcnow()
            watch = BringWatchState(
                product_id=plan.product_id, state=BringWatchStateEnum.PENDING_ADD,
                origin=BringWatchOrigin.INVENTRA_CREATED, bring_item_name=plan.name,
                retry_count=0, created_at=now, updated_at=now,
                confirmation_deadline_at=now + CONFIRMATION_TIMEOUT)
            if plan.conflict is not None:
                watch.state = BringWatchStateEnum.ERROR
                watch.last_error = f'name_conflict_with_product_id={plan.conflict}'
                watch.confirmation_deadline_at = None
            elif existing is not None:
                watch.state = BringWatchStateEnum.ON_LIST_CONFIRMED
                watch.origin = BringWatchOrigin.ADOPTED_EXISTING
                watch.bring_uid = existing['uid']
                watch.bring_item_name = existing['summary']
                watch.confirmation_deadline_at = None
            db.add(watch)
            db.flush()
            pending = watch.state == BringWatchStateEnum.PENDING_ADD
            plan.watch_marker = _watch_marker(watch)
            db.commit()
        if not pending:
            return
        failed = False
        try:
            await client.add_item(plan.name, plan.description)
        except HomeAssistantApiError:
            failed = True
        if failed:
            with Session(engine) as db:
                if _matches(db, plan):
                    watch = db.get(BringWatchState, plan.product_id)
                    watch.retry_count += 1
                    watch.last_error = 'ADD_FAILED'
                    watch.updated_at = datetime.utcnow()
                    db.commit()
        return
    if plan.action == 'remove':
        success = await _remove_inventra_item_for_row(client, plan.fields, plan.claimed, items)
        with Session(engine) as db:
            if _matches(db, plan):
                _apply_removal(db, plan, success)
                db.commit()
        return
    if plan.action == 'clear':
        with Session(engine) as db:
            if _matches(db, plan):
                on_product_deleted(db, plan.product_id)
                db.commit()
        return
    await _sync_quantity(client, plan, items)
    if not advance:
        return
    failed = False
    if plan.fields.state == BringWatchStateEnum.PENDING_ADD:
        # Visibility, even for a foreign claim, prevents a duplicate add. Only
        # unclaimed UIDs may subsequently confirm ownership.
        visible = _find_by_name(items,
                                plan.fields.bring_item_name)
        completed = _find_by_name([item for item in items if item['status'] == 'completed'],
                                  plan.fields.bring_item_name)
        if completed is not None:
            with Session(engine) as db:
                if _matches(db, plan):
                    watch = db.get(BringWatchState, plan.product_id)
                    watch.state = BringWatchStateEnum.LOCKED_PURCHASED
                    watch.lock_reason = 'completed'
                    watch.last_error = None
                    watch.last_checked_at = watch.updated_at = datetime.utcnow()
                    db.commit()
            return
        if visible is None or plan.fields.last_error == 'ADD_FAILED':
            try:
                await client.add_item(plan.fields.bring_item_name, plan.description)
            except HomeAssistantApiError:
                failed = True
    with Session(engine) as db:
        if not _matches(db, plan):
            return
        watch = db.get(BringWatchState, plan.product_id)
        if watch.state == BringWatchStateEnum.PENDING_ADD:
            advance_pending_add(watch, items)
            if failed and watch.state != BringWatchStateEnum.ERROR:
                watch.last_error = 'ADD_FAILED'
        elif watch.state == BringWatchStateEnum.ON_LIST_CONFIRMED:
            advance_on_list_confirmed(watch, items)
            if watch.state == BringWatchStateEnum.ON_LIST_CONFIRMED and watch.last_error:
                watch.last_error = None
                watch.retry_count = 0
                watch.updated_at = datetime.utcnow()
        db.commit()


def _release_session(db):
    # Preserve values held by compatibility callers while releasing all locks.
    expire = db.expire_on_commit
    db.expire_on_commit = False
    try:
        db.commit()
    finally:
        db.expire_on_commit = expire
        db.close()


async def evaluate_product(db: Session, client: BringHaClient, product_id: str, summary: dict | None = None, *, _force_add=False) -> None:
    engine = db.get_bind()
    plan = _plan(db, product_id, summary, _force_add)
    _release_session(db)
    if not _needs_items(plan):
        await _execute_plan(engine, client, plan, [], advance=False)
        return
    try:
        items = list(await client.get_items())
    except HomeAssistantApiError:
        if plan.action == 'remove':
            with Session(engine) as outcome:
                if _matches(outcome, plan):
                    _apply_removal(outcome, plan, False)
                    outcome.commit()
        return
    await _execute_plan(engine, client, plan, items, advance=False)


async def reconcile_once(db: Session | Engine, client: BringHaClient) -> None:
    """Serialize cycles; release SQLite before every HA await."""
    async with _get_reconcile_lock():
        if hasattr(db, 'get_bind'):
            engine = db.get_bind()
            _release_session(db)
        else:
            engine = db
        try:
            items = list(await client.get_items())
        except HomeAssistantApiError:
            logger.warning('bring reconcile skipped, HA API unreachable')
            return
        # Sweep only after fetching the list. Keep original claims throughout
        # the sweep so deleting one orphan cannot authorize another's removal.
        try:
            with Session(engine) as planning:
                orphan_ids = list(planning.scalars(
                    select(BringWatchState.product_id).outerjoin(Product, Product.id == BringWatchState.product_id)
                    .where((Product.id.is_(None)) | (Product.deleted_at.is_not(None)))))
        except Exception:
            logger.exception('bring orphan sweep planning failed')
            orphan_ids = []
        for product_id in orphan_ids:
            try:
                with Session(engine) as planning:
                    plan = _plan(planning, product_id)
                await _execute_plan(engine, client, plan, items, advance=False)
            except Exception:
                logger.exception('bring orphan cleanup failed for product %s', product_id)
        with Session(engine) as planning:
            summaries = build_summaries(planning)
            product_ids = [summary['productId'] for summary in summaries]
        # Re-read each product after previous HA actions: a stock booking or
        # deletion during this cycle must affect subsequent decisions.
        for product_id in product_ids:
            try:
                with Session(engine) as planning:
                    plan = _plan(planning, product_id, reconcile=True)
                await _execute_plan(engine, client, plan, items, advance=True)
            except Exception:
                logger.exception('bring reconcile failed for product %s', product_id)


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


def _client():
    settings = get_settings()
    return BringHaClient(base_url=HA_CORE_API_BASE,
                         token=os.environ.get('SUPERVISOR_TOKEN', ''),
                         todo_entity_id=settings.bring_todo_entity_id)


async def on_product_deleted_with_cleanup(product_id: str) -> None:
    async with _get_reconcile_lock():
        try:
            engine = get_engine()
            with Session(engine) as db:
                plan = _plan(db, product_id)
            if plan.fields is None:
                return
            # The deletion callback also accepts an existing product for
            # compatibility; compare its full marker before applying cleanup.
            plan.action = 'remove'
            if not _needs_items(plan):
                await _execute_plan(engine, None, plan, [], advance=False)
                return
            client = _client()
            items = []
            success = True
            if plan.fields.origin == BringWatchOrigin.INVENTRA_CREATED and plan.fields.state in (
                BringWatchStateEnum.PENDING_ADD, BringWatchStateEnum.ON_LIST_CONFIRMED,
            ):
                try:
                    items = list(await client.get_items())
                except HomeAssistantApiError:
                    success = False
            with Session(engine) as preflight:
                if not _matches(preflight, plan):
                    return
            if success:
                success = await _remove_inventra_item_for_row(client, plan.fields, plan.claimed, items)
            with Session(engine) as db:
                if _matches(db, plan):
                    _apply_removal(db, plan, success)
                    db.commit()
        except Exception:
            logger.exception('bring watch deletion failed for product %s', product_id)


async def schedule_stock_change(product_id: str, increased: bool) -> None:
    async with _get_reconcile_lock():
        try:
            engine = get_engine()
            with Session(engine) as db:
                if increased:
                    on_stock_increase(db, product_id)
                plan = _plan(db, product_id)
                db.commit()
            if not _needs_items(plan):
                await _execute_plan(engine, None, plan, [], advance=False)
                return
            client = _client()
            try:
                items = list(await client.get_items())
            except HomeAssistantApiError:
                if plan.action == 'remove':
                    with Session(engine) as db:
                        if _matches(db, plan):
                            _apply_removal(db, plan, False)
                            db.commit()
                return
            await _execute_plan(engine, client, plan, items, advance=False)
        except Exception:
            logger.exception('bring schedule_stock_change failed for product %s', product_id)


async def run_bring_reconcile_loop(interval_seconds: int, *, engine: Engine | None = None) -> None:
    """Runs immediately (covers Inventra restart, spec §5.6), then every
    `interval_seconds`. Never lets one bad cycle kill the loop."""
    settings = get_settings()
    client = BringHaClient(
        base_url=HA_CORE_API_BASE,
        token=os.environ.get("SUPERVISOR_TOKEN", ""),
        todo_entity_id=settings.bring_todo_entity_id,
    )
    engine = engine if engine is not None else get_engine()
    while True:
        try:
            await reconcile_once(engine, client)
        except Exception:
            logger.exception("bring reconcile loop iteration failed")
        await asyncio.sleep(interval_seconds)
