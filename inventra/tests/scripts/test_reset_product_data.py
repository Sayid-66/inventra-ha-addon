"""Bounded admin-reset tests using real SQLite files and injected async Bring."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from test_reset_production_data import seed_database, dump

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/reset_product_data.py"
spec = importlib.util.spec_from_file_location("reset_product_data", SCRIPT)
reset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reset)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "productive.db"
    seed_database(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO units (id,name,abbreviation,is_standard,created_at) VALUES ('custom','Custom','cu',0,'2026-01-01')")
        db.execute("UPDATE products SET unit_id='custom' WHERE id='p0'")
        db.execute("UPDATE products SET deleted_at='2026-01-01',version=4 WHERE id='p1'")
        db.execute("UPDATE barcodes SET deleted_at='2026-01-01' WHERE code='code0'")
        db.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        db.execute("INSERT INTO alembic_version VALUES ('keep-version')")
        db.execute("CREATE TABLE future_settings (value TEXT)")
        db.execute("INSERT INTO future_settings VALUES ('unchanged')")
        for kind in (*reset.PRODUCT_ENTITY_TYPES, 'Location', 'Store', 'Unit', 'Other'):
            db.execute("INSERT INTO change_log (revision,entity_type,entity_id,change_kind,snapshot,created_at) VALUES (90,?,'keep','UPDATE','{}','2026-01-01')", (kind,))
        db.execute("UPDATE processed_operations SET result_snapshot=? WHERE operation_id='0'", (json.dumps({'deviceId': 'paired'}),))
        db.execute("UPDATE processed_operations SET result_snapshot=? WHERE operation_id='1'", (json.dumps({'productId': 'p0', 'quantity': 1}),))
        db.execute("UPDATE processed_operations SET result_snapshot=? WHERE operation_id='2'", (json.dumps({'result': {'id': 'p1'}}),))
        for operation_id, result in (
            ('3', {'locationId': 'real-kitchen'}),
            ('4', {'storeId': 'Aldi'}),
            ('5', {'unitId': 'custom'}),
            ('6', {'acknowledgedSignature': 'p0:2026-01-01|p1:2026-02-01'}),
            ('7', {'productId': 'unrelated-product'}),
        ):
            db.execute('UPDATE processed_operations SET result_snapshot=? WHERE operation_id=?',
                       (json.dumps(result), operation_id))
    return path


def confirm(path, **kwargs):
    return reset.reset_database(path, confirm=True, skip_bring=True, **kwargs)


def rows(path, tables):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables}


class FakeBring:
    def __init__(self, items=(), error=None, on_remove=None):
        self.items = list(items)
        self.error = error
        self.removed = []
        self.get_calls = 0
        self.on_remove = on_remove

    async def get_items(self):
        self.get_calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return self.items

    async def remove_item(self, uid):
        if self.on_remove:
            self.on_remove(uid)
        self.removed.append(uid)


def test_full_reset_and_idempotent_repeat(database):
    kept_tables = ('locations', 'stores', 'units', 'devices', 'pairing_codes',
                   'instance_meta', 'alembic_version', 'future_settings')
    preserved = rows(database, kept_tables)
    kept_operations = [row for row in rows(database, ['processed_operations'])['processed_operations']
                       if row[0] not in ('1', '2', '6')]
    old_other = rows(database, ['change_log'])['change_log'][-4:]
    report = confirm(database)
    assert 'Bring phase explicitly skipped' in report
    assert rows(database, kept_tables) == preserved
    assert rows(database, ['processed_operations'])['processed_operations'] == kept_operations
    assert 'processed_operations: 151 -> 148' in report
    with sqlite3.connect(database) as db:
        for model in reset.CLEAR_MODELS:
            assert db.execute(f'SELECT count(*) FROM "{model.__tablename__}"').fetchone() == (0,)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT current_revision FROM revision_counter').fetchone() == (94,)
        changes = db.execute('SELECT * FROM change_log ORDER BY id').fetchall()
        assert changes[:4] == old_other
        assert len(changes) == 25
        for change in changes[4:]:
            assert change[1] == 94 and change[2] == 'Product' and change[4] == 'DELETE'
            snapshot = json.loads(change[5])
            assert snapshot['id'] == change[3] and snapshot['deletedAt']
            if change[3] == 'keep':
                assert set(snapshot) == {'id', 'deletedAt'}
                continue
            assert snapshot['version'] == (5 if change[3] == 'p1' else 2)
            assert 'fieldProvenance' in snapshot
    original = dump(database)
    repeat_report = confirm(database)
    assert 'Already reset' in repeat_report
    assert 'processed_operations: 148 -> 148' in repeat_report
    assert dump(database) == original


def test_dry_run_is_read_only_and_lists_products(database):
    before = database.read_bytes(), database.stat().st_mtime_ns
    fake = FakeBring([{'uid': 'uid', 'summary': 'Test', 'status': 'needs_action'}])
    report = reset.reset_database(database, dry_run=True, bring_client_factory=lambda entity: fake)
    assert 'Product 0 (p0)' in report and 'Product 1 (p1)' in report
    assert 'products: 20 -> 0' in report and 'units: 1 -> 1' in report
    assert 'processed_operations: 151 -> 148' in report
    assert 'Bring currently on list' in report
    assert fake.get_calls == 1 and not fake.removed
    assert (database.read_bytes(), database.stat().st_mtime_ns) == before
    assert not (database.parent / 'backups').exists()


def images(path):
    directory = path.parent / 'product_images'
    directory.mkdir()
    product = directory / '01234567-89ab-7cde-8fab-0123456789ab.123456abcdef.jpg'
    product.write_bytes(b'image')
    (directory / 'README.txt').write_bytes(b'keep')
    (directory / '01234567-89ab-7cde-8fab-0123456789ab-folder').mkdir()
    return directory, product


def test_backup_valid_and_images_copied_before_cleanup(database):
    directory, product = images(database)
    original = dump(database)
    report = confirm(database)
    backups = database.parent / 'backups'
    backup = next(backups.glob('inventra-pre-reset-*.db'))
    assert dump(backup) == original
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    image_backup = next(backups.glob('product_images-*'))
    assert (image_backup / product.name).read_bytes() == b'image'
    assert not product.exists()
    assert (directory / 'README.txt').exists()
    assert (directory / '01234567-89ab-7cde-8fab-0123456789ab-folder').is_dir()
    assert 'Product image files removed: 1' in report


@pytest.mark.parametrize('failure', ['backup', 'copy-images', 'late-trigger', 'count-mismatch', 'bad-counter', 'foreign-key'])
def test_failures_keep_database_and_images(database, monkeypatch, failure):
    _, product = images(database)
    if failure == 'backup':
        def fail(*args):
            raise OSError('backup failure')
        monkeypatch.setattr(reset, '_backup', fail)
    elif failure == 'copy-images':
        def fail(*args, **kwargs):
            raise OSError('copy failure')
        monkeypatch.setattr(reset.shutil, 'copytree', fail)
    elif failure == 'count-mismatch':
        original_counts = reset._counts
        calls = 0
        def wrong_counts(db):
            nonlocal calls
            counts = original_counts(db)
            calls += 1
            if calls > 1:
                counts['products'] += 1
            return counts
        monkeypatch.setattr(reset, '_counts', wrong_counts)
    else:
        with sqlite3.connect(database) as db:
            if failure == 'late-trigger':
                db.execute("CREATE TRIGGER fail_reset BEFORE INSERT ON change_log BEGIN SELECT RAISE(ABORT,'injected'); END")
            elif failure == 'bad-counter':
                db.execute('UPDATE revision_counter SET current_revision=1')
            else:
                db.execute("UPDATE devices SET default_location_id='missing'")
    before = dump(database)
    with pytest.raises(Exception):
        confirm(database)
    assert dump(database) == before and product.read_bytes() == b'image'


def seed_bring(path):
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM bring_watch_state')
        for product, name, uid, origin in (
            ('p0', 'By uid', 'u0', 'INVENTRA_CREATED'),
            ('p1', 'Fallback', 'stale', 'INVENTRA_CREATED'),
            ('p2', 'Absent', None, 'INVENTRA_CREATED'),
            ('p3', 'Adopted', 'u3', 'ADOPTED_EXISTING'),
        ):
            db.execute("INSERT INTO bring_watch_state (product_id,state,origin,bring_item_name,bring_uid,retry_count,created_at,updated_at) VALUES (?,'ON_LIST_CONFIRMED',?,?,?,0,'2026-01-01','2026-01-01')", (product, origin, name, uid))


def test_bring_uid_name_absent_and_adopted(database):
    seed_bring(database)
    _, image = images(database)
    def before_commit(uid):
        assert image.exists()
        with sqlite3.connect(database) as db:
            assert db.execute('SELECT count(*) FROM products').fetchone() == (20,)
        assert list((database.parent / 'backups').glob('*.db'))
    fake = FakeBring([
        {'uid': 'u0', 'summary': 'Renamed', 'status': 'needs_action'},
        {'uid': 'u1', 'summary': 'fALLBACK', 'status': 'needs_action'},
        {'uid': 'u3', 'summary': 'Adopted', 'status': 'needs_action'},
    ], on_remove=before_commit)
    seen = []
    def factory(entity):
        seen.append(entity)
        return fake
    report = reset.reset_database(database, confirm=True, bring_entity='todo.test', bring_client_factory=factory)
    assert fake.get_calls == 1 and fake.removed == ['u0'] and seen == ['todo.test']
    assert 'Bring absent: Absent' in report and 'Bring Adopted: not removed (state=ON_LIST_CONFIRMED, origin=ADOPTED_EXISTING)' in report
    assert not image.exists()


@pytest.mark.parametrize('stage', ['get', 'remove'])
def test_bring_errors_abort(database, stage):
    before = dump(database)
    _, image = images(database)
    def fail(uid):
        raise RuntimeError('HA failed')
    fake = FakeBring([{'uid': 'u', 'summary': 'Test', 'status': 'needs_action'}],
                     error='HA failed' if stage == 'get' else None,
                     on_remove=fail if stage == 'remove' else None)
    with pytest.raises(RuntimeError, match='HA failed'):
        reset.reset_database(database, confirm=True, bring_client_factory=lambda entity: fake)
    assert dump(database) == before and image.exists()


def test_skip_bring_never_constructs_client(database):
    def forbidden(entity):
        pytest.fail('skip must not construct a client')
    assert 'explicitly skipped' in confirm(database, bring_client_factory=forbidden)


def test_claimed_uid_protected_and_completed_name_not_removed(database):
    seed_bring(database)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE bring_watch_state SET bring_uid='u0' WHERE product_id='p3'")
    fake = FakeBring([
        {'uid': 'u0', 'summary': 'Adopted', 'status': 'needs_action'},
        {'uid': 'u1', 'summary': 'Fallback', 'status': 'completed'},
    ])
    report = reset.reset_database(database, confirm=True, bring_client_factory=lambda entity: fake)
    assert not fake.removed
    assert 'Bring absent: By uid' in report
    assert 'Bring absent: Fallback' in report


def test_ambiguous_bring_fails_before_removals(database):
    seed_bring(database)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE bring_watch_state SET state='PENDING_ADD' WHERE product_id='p1'")
    original = dump(database)
    fake = FakeBring([
        {'uid': 'u0', 'summary': 'By uid', 'status': 'needs_action'},
        {'uid': 'u1', 'summary': 'Fallback', 'status': 'needs_action'},
        {'uid': 'u2', 'summary': 'Fallback', 'status': 'needs_action'},
    ])
    with pytest.raises(ValueError, match='Ambiguous'):
        reset.reset_database(database, confirm=True, bring_client_factory=lambda entity: fake)
    assert not fake.removed and dump(database) == original


def test_dry_run_ha_unavailable_is_read_only(database):
    original = database.read_bytes()
    fake = FakeBring(error='unreachable')
    report = reset.reset_database(database, dry_run=True, bring_client_factory=lambda entity: fake)
    assert 'inspection unavailable: unreachable' in report
    assert database.read_bytes() == original


def test_live_wal_backup_and_dry_run(database):
    with sqlite3.connect(database) as live:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute("UPDATE products SET name='Live WAL product' WHERE id='p0'")
        live.commit()
        original = dump(database)
        assert 'Live WAL product' in reset.reset_database(database, dry_run=True, skip_bring=True)
        assert dump(database) == original
        confirm(database)
        backup = next((database.parent / 'backups').glob('*.db'))
        assert dump(backup) == original


def test_image_cleanup_error_is_nonfatal_after_commit(database, monkeypatch):
    _, product = images(database)
    original_unlink = Path.unlink
    def deny_image(file, *args, **kwargs):
        if file == product:
            raise OSError('cannot remove image')
        return original_unlink(file, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', deny_image)
    report = confirm(database)
    assert 'RESET COMMITTED' in report and 'Image cleanup error' in report
    assert product.exists()
    assert rows(database, ['products']) == {'products': []}


def test_no_products_retains_log_even_when_caches_exist(database):
    confirm(database)
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO mhd_warning_ack_state VALUES (0,'old')")
    original = rows(database, ['change_log', 'revision_counter'])
    confirm(database, backup_dir=database.parent / 'second-backup')
    assert rows(database, ['change_log', 'revision_counter']) == original


@pytest.mark.parametrize('kwargs', [{}, {'dry_run': True, 'confirm': True}])
def test_exactly_one_mode_required(database, kwargs):
    original = database.read_bytes()
    with pytest.raises(ValueError, match='exactly one'):
        reset.reset_database(database, **kwargs)
    assert database.read_bytes() == original


def test_cli_and_missing_path(tmp_path, database, capsys):
    assert reset.main([str(database), '--dry-run', '--skip-bring']) == 0
    assert 'Product 0' in capsys.readouterr().out
    missing = tmp_path / 'missing.db'
    assert reset.main([str(missing), '--confirm', '--skip-bring']) == 1
    assert not missing.exists()


def test_many_uuid_products_and_operations(database):
    # Exceed the traditional SQLite 999-variable limit on both sides.
    from uuid import UUID
    product_ids = [str(UUID(int=i + 1)) for i in range(1005)]
    engine = reset.create_engine(f"sqlite:///{database}")
    try:
        with engine.begin() as db:
            db.execute(reset.m.Product.__table__.insert(), [
                {'id': id_, 'name': 'Extra', 'deleted_at': None} for id_ in product_ids])
            db.execute(reset.m.ProcessedOperation.__table__.insert(), [
                {'operation_id': f'extra-{i}', 'payload_hash': 'hash',
                 'result_snapshot': json.dumps({'nested': [{'id': id_}]})}
                for i, id_ in enumerate(product_ids)])
    finally:
        engine.dispose()
    original = dump(database)
    report = reset.reset_database(database, dry_run=True, skip_bring=True)
    assert 'processed_operations: 1156 -> 148' in report
    assert dump(database) == original
    report = confirm(database)
    assert 'processed_operations: 1156 -> 148' in report
    remaining = rows(database, ['processed_operations'])['processed_operations']
    assert len(remaining) == 148
    assert all(not row[0].startswith('extra-') for row in remaining)


@pytest.mark.parametrize('dry_run', [True, False])
def test_safe_states_and_normalized_pending(database, dry_run):
    seed_bring(database)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE bring_watch_state SET state='LOCKED_PURCHASED' WHERE product_id='p0'")
        db.execute("UPDATE bring_watch_state SET state='ERROR' WHERE product_id='p2'")
        db.execute("UPDATE bring_watch_state SET state='PENDING_ADD',bring_item_name='  MILK  Tea ' WHERE product_id='p1'")
    fake = FakeBring([
        {'uid': 'user0', 'summary': 'By uid', 'status': 'needs_action'},
        {'uid': 'user2', 'summary': 'Absent', 'status': 'needs_action'},
        {'uid': 'u3', 'summary': 'milk tea', 'status': 'needs_action'},
        {'uid': 'fresh', 'summary': ' milk\t TEA ', 'status': 'needs_action'},
        {'uid': 'done', 'summary': 'milk tea', 'status': 'completed'},
    ])
    report = reset.reset_database(database, dry_run=dry_run, confirm=not dry_run,
                                 bring_client_factory=lambda entity: fake)
    assert fake.removed == ([] if dry_run else ['fresh'])
    for state, origin in [('LOCKED_PURCHASED', 'INVENTRA_CREATED'),
                          ('ERROR', 'INVENTRA_CREATED'),
                          ('ON_LIST_CONFIRMED', 'ADOPTED_EXISTING')]:
        assert f'not removed (state={state}, origin={origin})' in report
    assert 'origin=INVENTRA_CREATED, state=PENDING_ADD' in report


@pytest.mark.parametrize('bad_items,error', [
    ([{'summary': 'Fallback', 'status': 'needs_action'}], 'Bring item missing uid: Fallback'),
    ([{'uid': 'a', 'summary': 'Fallback', 'status': 'needs_action'},
      {'uid': 'b', 'summary': 'Fallback', 'status': 'needs_action'}], 'Ambiguous Bring item: Fallback'),
])
def test_complete_bring_plan_errors(database, bad_items, error):
    seed_bring(database)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE bring_watch_state SET state='PENDING_ADD' WHERE product_id='p1'")
    original = dump(database)
    fake = FakeBring([{'uid': 'u0', 'summary': 'By uid', 'status': 'needs_action'}, *bad_items])
    report = reset.reset_database(database, dry_run=True, bring_client_factory=lambda entity: fake)
    assert f'Bring plan error: {error}' in report
    with pytest.raises(ValueError, match=error):
        reset.reset_database(database, confirm=True, bring_client_factory=lambda entity: fake)
    assert not fake.removed and dump(database) == original


def test_bring_removal_printed_before_late_db_failure(database, capsys):
    _, image = images(database)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TRIGGER fail_reset BEFORE INSERT ON change_log BEGIN SELECT RAISE(ABORT,'late failure'); END")
    original = dump(database)
    fake = FakeBring([{'uid': 'u', 'summary': 'Test', 'status': 'needs_action'}])
    with pytest.raises(Exception, match='late failure'):
        reset.reset_database(database, confirm=True, bring_client_factory=lambda entity: fake)
    assert fake.removed == ['u']
    assert 'Bring removed:' in capsys.readouterr().out
    assert dump(database) == original and image.read_bytes() == b'image'


@pytest.mark.parametrize('failure', ['integrity', 'count'])
def test_real_backup_validation_failure(database, monkeypatch, failure):
    _, image = images(database)
    original = dump(database)
    connect = reset.sqlite3.connect
    class CopyConnection:
        def __init__(self, connection):
            self.connection = connection
        def __enter__(self):
            self.connection.__enter__()
            return self
        def __exit__(self, *args):
            return self.connection.__exit__(*args)
        def execute(self, sql):
            if failure == 'integrity' and sql == 'PRAGMA integrity_check':
                return connect(':memory:').execute("SELECT 'injected corrupt backup'")
            if failure == 'count' and sql == 'SELECT count(*) FROM "products"':
                return connect(':memory:').execute('SELECT -1')
            return self.connection.execute(sql)
    def wrapped(path, *args, **kwargs):
        connection = connect(path, *args, **kwargs)
        if 'inventra-pre-reset-' in str(path) and 'mode=ro' in str(path):
            return CopyConnection(connection)
        return connection
    monkeypatch.setattr(reset.sqlite3, 'connect', wrapped)
    fake = FakeBring([{'uid': 'u', 'summary': 'Test', 'status': 'needs_action'}])
    with pytest.raises(ValueError, match='Backup integrity_check failed|Backup count mismatch: products'):
        reset.reset_database(database, confirm=True, bring_client_factory=lambda entity: fake)
    assert fake.get_calls == 0 and not fake.removed
    assert dump(database) == original and image.read_bytes() == b'image'


def test_missing_products_tombstoned_once_and_old_deletes_retained(database):
    confirm(database)
    with sqlite3.connect(database) as db:
        old = db.execute("SELECT * FROM change_log WHERE entity_type='Product'").fetchall()
        db.execute("INSERT INTO change_log (revision,entity_type,entity_id,change_kind,snapshot,created_at) VALUES (95,'Product','gone','UPDATE','{}','2026-01-01')")
        db.execute('UPDATE revision_counter SET current_revision=95')
    report = confirm(database, backup_dir=database.parent / 'stale-backup')
    assert 'current products: 0; missing products: 1' in report
    with sqlite3.connect(database) as db:
        changes = db.execute("SELECT * FROM change_log WHERE entity_type='Product' ORDER BY id").fetchall()
        assert changes[:-1] == old
        assert changes[-1][1:5] == (96, 'Product', 'gone', 'DELETE')
        assert set(json.loads(changes[-1][5])) == {'id', 'deletedAt'}
    original = dump(database)
    confirm(database)
    assert dump(database) == original


def test_no_products_or_product_log_is_noop(database):
    confirm(database)
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM change_log WHERE entity_type='Product'")
    original = dump(database)
    confirm(database)
    assert dump(database) == original
