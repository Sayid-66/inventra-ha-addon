from datetime import datetime, timedelta

from inventra_backend.config import Settings
from inventra_backend.resolver.resolution_store import create_resolution, get_resolution


def test_create_then_get_round_trips_proposed_fields(db_session):
    proposed = {"name": {"value": "Milch", "source": "off", "confidence": "high"}}
    resolution_id = create_resolution(db_session, "4006381333931", proposed, Settings())
    db_session.commit()

    fetched = get_resolution(db_session, resolution_id)
    assert fetched is not None
    assert fetched["barcode"] == "4006381333931"
    assert fetched["proposed_fields"] == proposed


def test_unknown_resolution_id_returns_none(db_session):
    assert get_resolution(db_session, "does-not-exist") is None


def test_expired_resolution_returns_none(db_session):
    from inventra_backend.db.models import ResolutionResult
    import json
    db_session.add(ResolutionResult(
        resolution_id="expired-1", barcode="4006381333931",
        proposed_fields_json=json.dumps({}),
        created_at=datetime.utcnow() - timedelta(hours=2),
        expires_at=datetime.utcnow() - timedelta(hours=1),
    ))
    db_session.commit()

    assert get_resolution(db_session, "expired-1") is None


def test_resolution_id_is_a_valid_uuid7():
    import re
    resolution_id = create_resolution.__wrapped__ if hasattr(create_resolution, "__wrapped__") else None
    # sanity check the format contract directly against a generated id instead:
    from inventra_backend.resolver.resolution_store import _new_resolution_id
    generated = _new_resolution_id()
    assert re.match(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$",
        generated,
    )
