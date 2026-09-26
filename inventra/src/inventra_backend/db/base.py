from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def configure_engine(db_path: str) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _disable_pysqlite_transaction_management(dbapi_connection, connection_record) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn) -> None:
        # Serialize writers before their first read to close the Batch lost-update window.
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


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
    return _engine


def get_engine() -> Engine:
    assert _engine is not None, "init_engine() must run before get_engine()"
    return _engine


def get_db() -> Iterator[Session]:
    with session_scope(get_engine()) as db:
        yield db
