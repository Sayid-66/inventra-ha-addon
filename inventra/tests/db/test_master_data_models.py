import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from inventra_backend.db.base import Base, configure_engine, session_scope
from inventra_backend.db.models import Product, Barcode, Location, Store


def test_product_roundtrip(tmp_path):
    engine = configure_engine(str(tmp_path / "t.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(Product(id="p1", name="Milch", version=1))
    with session_scope(engine) as db:
        p = db.execute(select(Product).where(Product.id == "p1")).scalar_one()
        assert p.name == "Milch"
        assert p.version == 1
        assert p.deleted_at is None


def test_barcode_unique_code(tmp_path):
    engine = configure_engine(str(tmp_path / "t2.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(Product(id="p1", name="Milch", version=1))
        db.add(Barcode(code="4001", product_id="p1", version=1))
    with pytest.raises(IntegrityError):
        with session_scope(engine) as db:
            db.add(Barcode(code="4001", product_id="p1", version=1))


def test_location_normalized_name_unique(tmp_path):
    engine = configure_engine(str(tmp_path / "t3.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(Location(id="l1", name="Keller", normalized_name="keller", version=1))
    with pytest.raises(IntegrityError):
        with session_scope(engine) as db:
            db.add(Location(id="l2", name="KELLER", normalized_name="keller", version=1))


def test_store_normalized_name_unique(tmp_path):
    engine = configure_engine(str(tmp_path / "t4.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(Store(id="s1", name="Rewe", normalized_name="rewe", version=1))
    with pytest.raises(IntegrityError):
        with session_scope(engine) as db:
            db.add(Store(id="s2", name="REWE", normalized_name="rewe", version=1))
