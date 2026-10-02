from __future__ import annotations

from contextlib import contextmanager
from collections import Counter
import logging
import sqlite3
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def configure_engine(db_path: str) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _disable_pysqlite_transaction_management(dbapi_connection, connection_record) -> None:
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("PRAGMA busy_timeout = 15000")

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn) -> None:
        # Serialize writers before their first read to close the Batch lost-update window.
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def enable_foreign_keys_if_clean(engine: Engine) -> bool:
    """Enable FKs for a clean database; leave them off if the probe cannot run."""
    # Use DBAPI directly: SQLite cannot enable FKs inside BEGIN IMMEDIATE.
    try:
        with engine.connect() as connection:
            raw = connection.connection.dbapi_connection
            violations = raw.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                counts = Counter(row[0] for row in violations)
                logging.getLogger(__name__).warning(
                    "SQLite foreign keys remain OFF: foreign_key_check violations by table: %s",
                    ", ".join(f"{table}={count}" for table, count in sorted(counts.items())),
                )
                return False
            raw.execute("PRAGMA foreign_keys=ON")

    except (SQLAlchemyError, sqlite3.Error):
        logging.getLogger(__name__).warning(
            "could not probe database for foreign_key_check; foreign keys stay OFF",
            exc_info=True,
        )
        return False

    def enable(dbapi_connection, connection_record, *args):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", enable)
    # Include connections already in the pool when this opt-in is called.
    event.listen(engine, "checkout", enable)
    return True


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


_engine: Engine | None = None


def init_engine(db_path: str) -> Engine:
    global _engine
    _engine = configure_engine(db_path)
    enable_foreign_keys_if_clean(_engine)
    return _engine


def get_engine() -> Engine:
    assert _engine is not None, "init_engine() must run before get_engine()"
    return _engine


def get_db() -> Iterator[Session]:
    with session_scope(get_engine()) as db:
        yield db
