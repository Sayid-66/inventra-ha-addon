from sqlalchemy import Column, Integer, select

from inventra_backend.db.base import Base, configure_engine, session_scope


class _Probe(Base):
    __tablename__ = "_probe"
    id = Column(Integer, primary_key=True)


def test_session_commits_on_success(tmp_path):
    engine = configure_engine(str(tmp_path / "test.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(_Probe(id=1))
    with session_scope(engine) as db:
        assert db.execute(select(_Probe)).scalar_one().id == 1


def test_session_rolls_back_on_exception(tmp_path):
    engine = configure_engine(str(tmp_path / "test2.db"))
    Base.metadata.create_all(engine)
    try:
        with session_scope(engine) as db:
            db.add(_Probe(id=1))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with session_scope(engine) as db:
        assert db.execute(select(_Probe)).scalar_one_or_none() is None
