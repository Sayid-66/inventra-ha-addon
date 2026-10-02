import importlib.util
from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db.test_scrub_pairing_tokens_migration import ROOT, migrate


def test_identity_migration_single_row_repeatable_and_downgrade(tmp_path):
    path = tmp_path / "identity.db"
    migrate(path, "upgrade", "0010_scrub_pairing_tokens_from_idempotency")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as db:
        before = db.execute(text("SELECT current_revision FROM revision_counter WHERE id=0")).scalar_one()
    migrate(path, "upgrade", "head")
    with engine.connect() as db:
        identity, created = db.execute(text("SELECT instance_id, created_at FROM instance_meta")).one()
        assert UUID(identity).version == 4
        assert created is not None
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0011_instance_identity"
    migrate(path, "upgrade", "head")
    spec = importlib.util.spec_from_file_location("identity_migration", ROOT / "migrations/versions/0011_instance_identity.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as db:
        migration.op = Operations(MigrationContext.configure(db))
        migration.upgrade()
        assert db.execute(text("SELECT instance_id FROM instance_meta")).all() == [(identity,)]
        assert db.execute(text("SELECT current_revision FROM revision_counter WHERE id=0")).scalar_one() == before
        db.execute(text("DELETE FROM instance_meta"))
        migration.upgrade()
        assert UUID(db.execute(text("SELECT instance_id FROM instance_meta")).scalar_one()).version == 4
        with pytest.raises(IntegrityError):
            db.execute(text("INSERT INTO instance_meta (id, instance_id) VALUES (1, 'invalid')"))
    migrate(path, "downgrade", "0010_scrub_pairing_tokens_from_idempotency")
    assert "instance_meta" not in inspect(engine).get_table_names()
    engine.dispose()
