from datetime import datetime, timedelta

from inventra_backend.config import Settings
from inventra_backend.resolver.cache import get_fresh_cache_entries, upsert_cache_entries
from inventra_backend.resolver.source_client import SourceCandidate, SourceResult


def test_upsert_then_get_round_trips_a_found_result(db_session):
    result = SourceResult(
        source="off", status="FOUND",
        candidate=SourceCandidate(
            name="Milch", brand="Weihenstephan", quantity_text="1 l",
            image_url="https://x/y.jpg", category="Milchprodukte", variant=None,
        ),
    )
    upsert_cache_entries(db_session, "4006381333931", {"off": result}, Settings())
    db_session.commit()

    fresh = get_fresh_cache_entries(db_session, "4006381333931")
    assert fresh["off"].status == "FOUND"
    assert fresh["off"].candidate.name == "Milch"


def test_not_found_result_round_trips_with_no_candidate(db_session):
    result = SourceResult(source="opf", status="NOT_FOUND", candidate=None)
    upsert_cache_entries(db_session, "0000000000001", {"opf": result}, Settings())
    db_session.commit()

    fresh = get_fresh_cache_entries(db_session, "0000000000001")
    assert fresh["opf"].status == "NOT_FOUND"
    assert fresh["opf"].candidate is None


def test_expired_entry_is_not_returned(db_session):
    from inventra_backend.db.models import ResolverSourceCache
    db_session.add(ResolverSourceCache(
        barcode="4006381333931", source="off", status="FOUND", candidate_json=None,
        fetched_at=datetime.utcnow() - timedelta(days=60),
        expires_at=datetime.utcnow() - timedelta(days=30),
    ))
    db_session.commit()

    assert get_fresh_cache_entries(db_session, "4006381333931") == {}


def test_error_status_gets_a_much_shorter_ttl_than_not_found(db_session):
    settings = Settings()
    error_result = SourceResult(source="off", status="ERROR", candidate=None, error="timeout")
    upsert_cache_entries(db_session, "4006381333931", {"off": error_result}, settings)
    db_session.commit()

    from inventra_backend.db.models import ResolverSourceCache
    row = db_session.get(ResolverSourceCache, {"barcode": "4006381333931", "source": "off"})
    ttl = (row.expires_at - row.fetched_at).total_seconds()
    assert ttl == settings.resolver_cache_error_ttl_seconds
    assert ttl < settings.resolver_cache_not_found_ttl_seconds


def test_upsert_is_idempotent_on_conflict(db_session):
    settings = Settings()
    first = SourceResult(source="off", status="NOT_FOUND", candidate=None)
    upsert_cache_entries(db_session, "4006381333931", {"off": first}, settings)
    db_session.commit()

    second = SourceResult(
        source="off", status="FOUND",
        candidate=SourceCandidate("Milch", None, None, None, None, None),
    )
    upsert_cache_entries(db_session, "4006381333931", {"off": second}, settings)
    db_session.commit()

    fresh = get_fresh_cache_entries(db_session, "4006381333931")
    assert fresh["off"].status == "FOUND"  # the later upsert wins, no duplicate row
