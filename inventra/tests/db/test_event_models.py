from inventra_backend.db.base import Base, configure_engine, session_scope
from inventra_backend.db.models import (
    Product, Location, Batch, PurchaseEvent, ConsumptionEvent,
    CorrectionEvent, RelocationEvent, Source,
)


def test_purchase_event_and_batch_roundtrip(tmp_path):
    engine = configure_engine(str(tmp_path / "t.db"))
    Base.metadata.create_all(engine)
    with session_scope(engine) as db:
        db.add(Product(id="p1", name="Milch", version=1))
        db.add(Location(id="l1", name="Keller", normalized_name="keller", version=1))
        db.add(PurchaseEvent(
            id="e1", product_id="p1", timestamp=1000, barcode="4001",
            location_id="l1", quantity=2, user_id="u1", source_device_id="d1",
            source=Source.ANDROID,
        ))
        db.add(Batch(
            id="b1", product_id="p1", purchase_event_id="e1", location_id="l1",
            event_timestamp=1000, is_content_tracked=False, remaining_quantity=2,
        ))
    with session_scope(engine) as db:
        batch = db.get(Batch, "b1")
        assert batch.remaining_quantity == 2
        assert batch.purchase_event_id == "e1"
        assert batch.correction_event_id is None


def test_consumption_correction_relocation_events_have_no_version_attribute(tmp_path):
    for cls in (ConsumptionEvent, CorrectionEvent, RelocationEvent):
        assert not hasattr(cls, "version")
