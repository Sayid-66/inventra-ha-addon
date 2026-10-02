import logging

import pytest
from sqlalchemy.exc import IntegrityError

from inventra_backend.db.base import configure_engine, enable_foreign_keys_if_clean


def create_db(tmp_path):
    engine = configure_engine(str(tmp_path / "fk.db"))
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE parents (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE children (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parents(id))")
    return engine


def test_clean_db_enforces_foreign_keys_on_new_and_pooled_connections(tmp_path):
    engine = create_db(tmp_path)
    assert enable_foreign_keys_if_clean(engine) is True
    for fresh in [False, True]:
        if fresh:
            engine.dispose()
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 15000
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql("INSERT INTO children VALUES (1, 999)")
    engine.dispose()


def test_dirty_db_warns_with_counts_and_keeps_foreign_keys_off(tmp_path, caplog):
    engine = create_db(tmp_path)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO children VALUES (1, 999), (2, 999)")
    with caplog.at_level(logging.WARNING):
        assert enable_foreign_keys_if_clean(engine) is False
    assert "children=2" in caplog.text and "remain OFF" in caplog.text
    engine.dispose()
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 0
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 15000
    engine.dispose()


def test_configure_engine_does_not_opt_in(tmp_path):
    engine = create_db(tmp_path)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 0
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 15000
    engine.dispose()


def test_unavailable_db_warns_and_does_not_enable_foreign_keys(tmp_path, caplog):
    from inventra_backend.db.base import init_engine

    with caplog.at_level(logging.WARNING):
        engine = init_engine(str(tmp_path / "missing" / "inventra.db"))
    assert "could not probe database for foreign_key_check; foreign keys stay OFF" in caplog.text
    assert not (tmp_path / "missing").exists()
    engine.dispose()
