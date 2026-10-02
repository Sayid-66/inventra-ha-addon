import asyncio
import json
from datetime import datetime

import httpx
import pytest
from sqlalchemy.orm import Session

from inventra_backend.config import Settings, reset_settings_cache, get_settings
from inventra_backend.db.base import get_engine
from inventra_backend.db.models import Barcode, Product
from inventra_backend.resolver import resolver_service
from inventra_backend.resolver.sources import ALL_SOURCES


def _off_off_json(name: str, brand: str, quantity: str, image_url: str):
    return httpx.Response(200, json={
        "status": 1,
        "product": {
            "product_name_de": name, "brands": brand,
            "quantity": quantity, "image_url": image_url, "categories": "",
        },
    })


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    from inventra_backend.db.base import init_engine
    db_path = str(tmp_path / "resolver.db")
    import subprocess, sys, os
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        env={**os.environ, "INVENTRA_DB_PATH": db_path}, check=True,
    )
    monkeypatch.setenv("INVENTRA_DB_PATH", db_path)
    reset_settings_cache()
    init_engine(db_path)
    yield
    reset_settings_cache()


@pytest.mark.anyio
async def test_known_barcode_is_served_locally_with_no_external_calls(monkeypatch):
    with Session(get_engine()) as db:
        db.add(Product(id="p1", name="Milch", version=1))
        db.add(Barcode(code="4006381333931", product_id="p1", version=1))
        db.commit()

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("external source must not be called for a known barcode")

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", _should_not_be_called)

    result = await resolver_service.resolve("4006381333931")
    assert result.matched_locally is True
    assert result.product["id"] == "p1"
    assert result.resolution_id is None


@pytest.mark.anyio
async def test_unknown_barcode_fans_out_to_all_four_sources_and_merges(monkeypatch):
    def make_handler(name):
        def handler(request: httpx.Request) -> httpx.Response:
            return _off_off_json(name, "Marke", "500 ml", "https://x/y.jpg")
        return handler

    async def fake_fetch_all_sources(barcode, settings):
        from inventra_backend.resolver.source_client import SourceResult, SourceCandidate
        return {
            "off": SourceResult("off", "FOUND", SourceCandidate("Produkt X", "Marke", "500 ml", "https://x/y.jpg", None, None)),
            "obf": SourceResult("obf", "NOT_FOUND", None),
            "opff": SourceResult("opff", "NOT_FOUND", None),
            "opf": SourceResult("opf", "FOUND", SourceCandidate("Produkt X", "Marke", "500 ml", "https://x/y.jpg", None, None)),
        }

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fake_fetch_all_sources)

    result = await resolver_service.resolve("9999999999999")
    assert result.matched_locally is False
    assert result.resolution_id is not None
    assert result.fields["name"].value == "Produkt X"
    assert result.fields["name"].confidence.value == "high"  # off+opf agree


@pytest.mark.anyio
async def test_one_failing_source_does_not_prevent_a_result(monkeypatch):
    async def fake_fetch_all_sources(barcode, settings):
        from inventra_backend.resolver.source_client import SourceResult, SourceCandidate
        return {
            "off": SourceResult("off", "FOUND", SourceCandidate("Produkt X", None, None, None, None, None)),
            "obf": SourceResult("obf", "ERROR", None, error="timeout"),
            "opff": SourceResult("opff", "NOT_FOUND", None),
            "opf": SourceResult("opf", "NOT_FOUND", None),
        }

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fake_fetch_all_sources)

    result = await resolver_service.resolve("9999999999998")
    assert result.fields["name"].value == "Produkt X"


@pytest.mark.anyio
async def test_gate_failed_only_field_stays_empty(monkeypatch):
    async def fake_fetch_all_sources(barcode, settings):
        from inventra_backend.resolver.source_client import SourceResult, SourceCandidate
        garbage = "Pantothensure ZERO GO NRGY by 9180 BOOST BERRIES G"
        return {
            "off": SourceResult("off", "FOUND", SourceCandidate(garbage, None, None, None, None, None)),
            "obf": SourceResult("obf", "NOT_FOUND", None),
            "opff": SourceResult("opff", "NOT_FOUND", None),
            "opf": SourceResult("opf", "NOT_FOUND", None),
        }

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fake_fetch_all_sources)

    result = await resolver_service.resolve("4260456731392")
    assert result.fields["name"].value is None
    assert result.fields["name"].suggested is None


@pytest.mark.anyio
async def test_re_resolve_returns_none_for_a_barcode_not_owned_by_the_product():
    with Session(get_engine()) as db:
        db.add(Product(id="p1", name="Milch", version=1))
        db.add(Barcode(code="4006381333931", product_id="p1", version=1))
        db.commit()

    result = await resolver_service.re_resolve("p1", "0000000000000")
    assert result is None


@pytest.mark.anyio
async def test_re_resolve_diffs_against_current_product_and_respects_manual_fields(monkeypatch):
    with Session(get_engine()) as db:
        db.add(Product(
            id="p1", name="Alte Milch", brand=None, version=1,
            field_provenance=json.dumps({"name": {"manual": True, "selectedSource": "manual"}}),
        ))
        db.add(Barcode(code="4006381333931", product_id="p1", version=1))
        db.commit()

    async def fake_fetch_all_sources(barcode, settings):
        from inventra_backend.resolver.source_client import SourceResult, SourceCandidate
        return {
            "off": SourceResult("off", "FOUND", SourceCandidate("Neue Milch", "Marke", None, None, None, None)),
            "obf": SourceResult("obf", "NOT_FOUND", None),
            "opff": SourceResult("opff", "NOT_FOUND", None),
            "opf": SourceResult("opf", "NOT_FOUND", None),
        }

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fake_fetch_all_sources)

    result = await resolver_service.re_resolve("p1", "4006381333931")
    assert result is not None
    assert result.diff["name"]["manual"] is True
    assert result.diff["name"]["changed"] is False  # manual field never proposed as changeable
    assert result.diff["brand"]["proposedValue"] == "Marke"
    assert result.diff["brand"]["changed"] is True


@pytest.mark.anyio
async def test_re_resolve_bypasses_but_refreshes_the_source_cache(monkeypatch):
    with Session(get_engine()) as db:
        db.add(Product(id="p1", name="Milch", version=1))
        db.add(Barcode(code="4006381333931", product_id="p1", version=1))
        db.commit()

    calls = {"count": 0}

    async def fake_fetch_all_sources(barcode, settings):
        calls["count"] += 1
        from inventra_backend.resolver.source_client import SourceResult
        return {s.source_id: SourceResult(s.source_id, "NOT_FOUND", None) for s in ALL_SOURCES}

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fake_fetch_all_sources)

    # Prime the cache via a normal resolve of a DIFFERENT unknown barcode is
    # irrelevant here; the point is re-resolve for 4006381333931 must call
    # the sources even though a Barcode row (and thus a "known" product)
    # already exists for it.
    await resolver_service.re_resolve("p1", "4006381333931")
    assert calls["count"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("manual,proposed,changed", [
    (False, "500 g", False), (False, "750 g", True), (True, "750 g", False),
])
async def test_re_resolve_quantity_display_and_comparison(monkeypatch, manual, proposed, changed):
    from inventra_backend.db.models import Unit
    from inventra_backend.services.unit_normalizer import STANDARD_UNIT_IDS
    from inventra_backend.resolver.source_client import SourceResult, SourceCandidate

    with Session(get_engine()) as db:
        db.add(Product(
            id="p1", name="Mehl", quantity=500.0,
            unit=db.get(Unit, STANDARD_UNIT_IDS["g"]),
            field_provenance=json.dumps({"quantity": {"manual": manual}}),
        ))
        db.add(Barcode(code="4006381333931", product_id="p1", version=1))
        db.commit()

    async def fake_fetch(barcode, settings):
        return {
            s.source_id: SourceResult(
                s.source_id, "FOUND",
                SourceCandidate("Mehl", None, proposed, None, None, None),
            ) for s in ALL_SOURCES
        }

    monkeypatch.setattr(resolver_service, "_fetch_all_sources", fake_fetch)
    result = await resolver_service.re_resolve("p1", "4006381333931")
    assert result.diff["quantity"]["currentValue"] == "500 g"
    assert isinstance(result.diff["quantity"]["currentValue"], str)
    assert result.diff["quantity"]["changed"] is changed
    assert result.diff["quantity"]["manual"] is manual
    assert result.diff["quantity"]["proposedValue"] == (None if manual else proposed)
