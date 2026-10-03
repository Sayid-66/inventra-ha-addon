import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from inventra_backend.db.models import BringWatchOrigin, BringWatchState, BringWatchStateEnum, Product
from inventra_backend.services import bring_service as bring
from inventra_backend.services.bring_ha_client import HomeAssistantApiError


class Client:
    def __init__(self, engine, items=None, add_failures=0, remove_failures=0):
        self.engine = engine
        self.items = items or []
        self.add_failures = add_failures
        self.remove_failures = remove_failures
        self.added = []
        self.removed = []
        self.calls = []
        self.hook = None

    def write(self, call):
        # A separate SQLite connection must acquire the write lock during HA.
        with self.engine.connect() as conn:
            conn.connection.dbapi_connection.execute('PRAGMA busy_timeout=100')
            conn.execute(text("UPDATE products SET name=name WHERE id='p'"))
            conn.commit()
        self.calls.append(call)
        if self.hook:
            hook, self.hook = self.hook, None
            hook()

    async def get_items(self):
        self.write('get')
        return list(self.items)

    async def add_item(self, name, description=None):
        self.write('add')
        self.added.append(name)
        if self.add_failures:
            self.add_failures -= 1
            raise HomeAssistantApiError('token=secret')
        self.items.append(dict(uid='u', summary=name, status='needs_action', description=description))

    async def update_item(self, uid, description):
        self.write('update')
        next(item for item in self.items if item['uid'] == uid)['description'] = description

    async def remove_item(self, uid):
        self.write('remove')
        self.removed.append(uid)
        if self.remove_failures:
            self.remove_failures -= 1
            raise HomeAssistantApiError('token=secret')
        self.items[:] = [item for item in self.items if item['uid'] != uid]


def setup(db, monkeypatch, deleted=False, watched=False, origin=BringWatchOrigin.INVENTRA_CREATED):
    db.add(Product(id='p', name='Water', min_stock=2, deleted_at=datetime.utcnow() if deleted else None))
    db.flush()
    if watched:
        db.add(BringWatchState(product_id='p', state=BringWatchStateEnum.ON_LIST_CONFIRMED,
                              origin=origin, bring_uid='u', bring_item_name='Water', retry_count=0))
    db.commit()
    monkeypatch.setattr(bring, 'get_engine', lambda: db.get_bind())


def row(db):
    db.expire_all()
    result = db.get(BringWatchState, 'p')
    # Release the assertion's write transaction before the next async operation.
    if result is not None:
        db.expunge(result)
    db.commit()
    return result


@pytest.mark.anyio
async def test_add_recovers_and_confirms(db_session, monkeypatch):
    setup(db_session, monkeypatch)
    client = Client(db_session.get_bind(), add_failures=1)
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    assert row(db_session).last_error == 'ADD_FAILED'
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    assert client.items
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    assert row(db_session).state == BringWatchStateEnum.ON_LIST_CONFIRMED
    assert client.added == ['Water', 'Water']
    assert row(db_session).last_error is None


@pytest.mark.anyio
async def test_add_failure_limit_and_retry(db_session, monkeypatch):
    setup(db_session, monkeypatch)
    client = Client(db_session.get_bind(), add_failures=20)
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    watch = db_session.get(BringWatchState, 'p')
    watch.confirmation_deadline_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    for _ in range(2):
        await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    assert row(db_session).state == BringWatchStateEnum.ERROR
    assert row(db_session).retry_count == 3
    assert 'secret' not in row(db_session).last_error
    # The API retry uses this exact row clear + scheduled evaluation path.
    bring.on_product_deleted(db_session, 'p')
    db_session.commit()
    client.add_failures = 0
    monkeypatch.setattr(bring, 'BringHaClient', lambda **kwargs: client)
    await asyncio.wait_for(bring.schedule_stock_change('p', False), 2)
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    assert row(db_session).state == BringWatchStateEnum.ON_LIST_CONFIRMED


@pytest.mark.anyio
@pytest.mark.parametrize('path', ['recovered', 'sweep', 'cleanup', 'scheduled_deleted'])
@pytest.mark.parametrize('failures', [1, 10])
async def test_removal_retries_and_gives_up(db_session, monkeypatch, path, failures):
    setup(db_session, monkeypatch, deleted=path != 'recovered', watched=True)
    if path == 'recovered':
        original = bring.build_summary_for_product
        def recovered(db, pid):
            summary = original(db, pid)
            summary['totalStk'] = 2
            return summary
        monkeypatch.setattr(bring, 'build_summary_for_product', recovered)
    client = Client(db_session.get_bind(), [dict(uid='u', summary='Water', status='needs_action')],
                    remove_failures=failures)
    monkeypatch.setattr(bring, 'BringHaClient', lambda **kwargs: client)
    async def cycle():
        if path == 'cleanup':
            await bring.on_product_deleted_with_cleanup('p')
        elif path == 'scheduled_deleted':
            await bring.schedule_stock_change('p', False)
        else:
            await bring.reconcile_once(db_session, client)
    for attempt in range(failures):
        await asyncio.wait_for(cycle(), 2)
        watch = row(db_session)
        if attempt < 9:
            assert watch.state == BringWatchStateEnum.ON_LIST_CONFIRMED
            assert watch.last_error == 'REMOVE_FAILED'
            assert watch.retry_count == attempt + 1
        else:
            assert watch is None
    if failures == 1:
        await asyncio.wait_for(cycle(), 2)
        assert row(db_session) is None
        assert client.items == []
    else:
        assert client.items
    assert 'remove' in client.calls


@pytest.mark.anyio
@pytest.mark.parametrize('path', ['reconcile', 'scheduled', 'evaluate'])
async def test_add_has_no_write_transaction(db_session, monkeypatch, path):
    setup(db_session, monkeypatch)
    client = Client(db_session.get_bind())
    monkeypatch.setattr(bring, 'BringHaClient', lambda **kwargs: client)
    if path == 'reconcile':
        await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    elif path == 'scheduled':
        await asyncio.wait_for(bring.schedule_stock_change('p', False), 2)
    else:
        await asyncio.wait_for(bring.evaluate_product(db_session, client, 'p'), 2)
    assert client.calls == ['get', 'add']
    assert row(db_session).state == BringWatchStateEnum.PENDING_ADD


@pytest.mark.anyio
@pytest.mark.parametrize('change', ['delete', 'stock', 'watch'])
async def test_stale_add_outcome_is_not_applied(db_session, monkeypatch, change):
    setup(db_session, monkeypatch)
    client = Client(db_session.get_bind(), add_failures=1)
    original_add = client.add_item
    async def changed_add(name, description=None):
        with Session(db_session.get_bind()) as db:
            if change == 'delete':
                db.get(Product, 'p').deleted_at = datetime.utcnow()
            elif change == 'stock':
                from inventra_backend.db.models import Batch, Location
                db.add(Location(id='l', name='Shelf', normalized_name='shelf'))
                db.flush()
                db.add(Batch(id='b', product_id='p', location_id='l',
                             event_timestamp=1, is_content_tracked=False, remaining_quantity=2))
            else:
                watch = db.get(BringWatchState, 'p')
                watch.state = BringWatchStateEnum.ERROR
                watch.updated_at = datetime.utcnow()
                watch.last_error = 'manual'
            db.commit()
        await original_add(name, description)
    client.add_item = changed_add
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    watch = row(db_session)
    assert watch.retry_count == 0
    assert watch.last_error == ('manual' if change == 'watch' else None)
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    if change != 'watch':
        assert row(db_session) is None


@pytest.mark.anyio
async def test_adopted_orphan_never_removed(db_session, monkeypatch):
    setup(db_session, monkeypatch, deleted=True, watched=True, origin=BringWatchOrigin.ADOPTED_EXISTING)
    client = Client(db_session.get_bind(), [dict(uid='u', summary='Water', status='needs_action')])
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    assert row(db_session) is None
    assert client.removed == []


@pytest.mark.anyio
async def test_cleanup_preserves_uid_claimed_during_list_fetch(db_session, monkeypatch):
    setup(db_session, monkeypatch, deleted=True, watched=True)
    client = Client(db_session.get_bind(), [dict(uid='u', summary='Water', status='needs_action')])
    def claim():
        with Session(db_session.get_bind()) as db:
            db.add(Product(id='owner', name='Other', min_stock=2))
            db.flush()
            db.add(BringWatchState(product_id='owner', state=BringWatchStateEnum.ON_LIST_CONFIRMED,
                                  origin=BringWatchOrigin.ADOPTED_EXISTING, bring_uid='u',
                                  bring_item_name='Water', retry_count=0))
            db.commit()
    client.hook = claim
    monkeypatch.setattr(bring, 'BringHaClient', lambda **kwargs: client)
    await asyncio.wait_for(bring.on_product_deleted_with_cleanup('p'), 2)
    assert row(db_session) is not None  # changed claims invalidate the plan
    assert client.removed == []
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    assert row(db_session) is None
    assert client.removed == []


@pytest.mark.anyio
async def test_stale_remove_outcome_preserves_new_watch_state(db_session, monkeypatch):
    setup(db_session, monkeypatch, deleted=True, watched=True)
    client = Client(db_session.get_bind(), [dict(uid='u', summary='Water', status='needs_action')])
    original_remove = client.remove_item
    async def changed_remove(uid):
        with Session(db_session.get_bind()) as db:
            watch = db.get(BringWatchState, 'p')
            watch.state = BringWatchStateEnum.ERROR
            watch.last_error = 'new-state'
            watch.updated_at = datetime.utcnow()
            db.commit()
        await original_remove(uid)
    client.remove_item = changed_remove
    await asyncio.wait_for(bring.reconcile_once(db_session, client), 2)
    watch = row(db_session)
    assert watch.state == BringWatchStateEnum.ERROR
    assert watch.last_error == 'new-state'


@pytest.mark.anyio
async def test_pending_success_checked_off_is_locked(db_session, monkeypatch):
    setup(db_session, monkeypatch)
    client = Client(db_session.get_bind())
    await bring.reconcile_once(db_session, client)
    client.items[0]['status'] = 'completed'
    await bring.reconcile_once(db_session, client)
    assert client.added == ['Water']
    assert row(db_session).state == BringWatchStateEnum.LOCKED_PURCHASED


@pytest.mark.anyio
@pytest.mark.parametrize('minimum', [None, 0])
async def test_pending_disabled_minimum_never_adds(db_session, monkeypatch, minimum):
    setup(db_session, monkeypatch)
    client = Client(db_session.get_bind(), add_failures=1)
    await bring.reconcile_once(db_session, client)
    db_session.get(Product, 'p').min_stock = minimum
    db_session.commit()
    await bring.reconcile_once(db_session, client)
    assert client.added == ['Water']
    assert row(db_session) is None


@pytest.mark.anyio
@pytest.mark.parametrize('orphan', [False, True])
async def test_product_failure_does_not_abort_cycle(db_session, monkeypatch, orphan):
    setup(db_session, monkeypatch, deleted=orphan, watched=orphan)
    db_session.add(Product(id='next', name='Next', min_stock=2))
    db_session.commit()
    class BrokenClient(Client):
        async def add_item(self, name, description=None):
            if name == 'Water':
                raise ValueError('bad product')
            await super().add_item(name, description)
        async def remove_item(self, uid):
            raise ValueError('bad orphan')
    client = BrokenClient(db_session.get_bind(),
                          [dict(uid='u', summary='Water', status='needs_action')] if orphan else [])
    await bring.reconcile_once(db_session, client)
    assert client.added == ['Next']
