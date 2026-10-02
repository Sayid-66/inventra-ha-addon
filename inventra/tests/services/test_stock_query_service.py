from datetime import datetime
from inventra_backend.db.models import Batch, Location, Product
from inventra_backend.services.stock_query_service import build_summaries, build_summary_for_product


def test_single_summary_matches_all(db_session):
    db_session.add_all([Product(id="p1", name="Milch", content_unit_label="ml"), Product(id="p2", name="Brot"),
                        Location(id="l1", name="Keller", normalized_name="keller"),
                        Location(id="l2", name="Kueche", normalized_name="kueche")])
    db_session.flush()
    for i, (pid, lid, content, quantity) in enumerate([
        ("p1", "l1", False, 2), ("p1", "l2", False, 3),
        ("p1", "l1", True, 400), ("p1", "l2", True, 600), ("p2", "l1", False, 10),
    ]):
        db_session.add(Batch(id=f"b{i}", product_id=pid, location_id=lid, is_content_tracked=content,
                             remaining_quantity=quantity, event_timestamp=1000,
                             content_unit_label="ml" if content else None, mhd="2026-12-01"))
    db_session.flush()
    summary = build_summary_for_product(db_session, "p1")
    assert summary == next(s for s in build_summaries(db_session) if s["productId"] == "p1")
    assert summary["totalStk"] == 5
    assert summary["totalContent"] == 1000
    assert len(summary["stkByLocation"]) == len(summary["contentByLocation"]) == 2


def test_single_summary_missing_deleted(db_session):
    db_session.add(Product(id="deleted", name="Deleted", deleted_at=datetime.utcnow()))
    db_session.flush()
    assert build_summary_for_product(db_session, "unknown") is None
    assert build_summary_for_product(db_session, "deleted") is None
