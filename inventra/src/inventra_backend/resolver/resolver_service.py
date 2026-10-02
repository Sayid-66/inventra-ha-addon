from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db.base import get_engine
from ..services.barcode_service import find_barcode
from ..services.product_service import get_product
from ..services.unit_normalizer import quantity_diff
from .cache import get_fresh_cache_entries, upsert_cache_entries
from .resolution_store import create_resolution
from .scoring import FieldMergeResult, merge_text_field, merge_quantity_field
from .single_flight import SingleFlight
from .source_client import SourceResult
from .sources import ALL_SOURCES, build_client

_single_flight = SingleFlight()

_TEXT_FIELDS = {"name": "name", "brand": "brand", "category": "generic", "variant": "generic"}


@dataclass(frozen=True)
class ResolveResult:
    matched_locally: bool
    product: dict | None
    resolution_id: str | None
    fields: dict[str, FieldMergeResult] = field(default_factory=dict)


async def _fetch_one_source(config, barcode: str, settings: Settings) -> SourceResult:
    client = build_client(config, settings.resolver_source_timeout_seconds, "Inventra/resolver")
    return await client.fetch(barcode)


async def _fetch_all_sources(barcode: str, settings: Settings) -> dict[str, SourceResult]:
    """Always all four, in parallel, no early exit (spec §4.1)."""
    tasks = [_fetch_one_source(config, barcode, settings) for config in ALL_SOURCES]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=False),
            timeout=settings.resolver_global_deadline_seconds,
        )
    except asyncio.TimeoutError:
        # Global deadline hit: whatever already-finished results asyncio.gather
        # would have returned are not recoverable from a timed-out gather, so
        # treat every source as ERROR for this attempt — no partial credit,
        # no retry (spec §4.1). Per-source timeouts (shorter than the global
        # deadline) are expected to make this rare in practice.
        return {c.source_id: SourceResult(c.source_id, "ERROR", None, error="global_deadline") for c in ALL_SOURCES}
    return {r.source: r for r in results}


def _merge_all_fields(results: dict[str, SourceResult]) -> dict[str, FieldMergeResult]:
    def text_by_source(attr: str) -> dict[str, str | None]:
        return {s: (r.candidate.__dict__.get(attr) if r.candidate else None) for s, r in results.items()}

    return {
        "name": merge_text_field("name", text_by_source("name")),
        "brand": merge_text_field("brand", text_by_source("brand")),
        "quantity": merge_quantity_field(text_by_source("quantity_text")),
        "imageUrl": merge_text_field("url", text_by_source("image_url")),
        "category": merge_text_field("generic", text_by_source("category")),
        "variant": merge_text_field("generic", text_by_source("variant")),
    }


def _field_result_to_dict(result: FieldMergeResult) -> dict:
    return {
        "value": result.value, "suggested": result.suggested,
        "confidence": result.confidence.value if result.confidence else None,
        "selectedSource": result.selected_source,
        "contributingSources": result.contributing_sources,
        "conflictingSources": result.conflicting_sources,
    }


async def resolve(barcode: str) -> ResolveResult:
    # Step 1 (spec §4.2): short session, known-barcode check + cache read, then close.
    with Session(get_engine()) as db:
        barcode_row = find_barcode(db, barcode)
        if barcode_row is not None:
            product = get_product(db, barcode_row.product_id)
            if product is not None and product.deleted_at is None:
                from ..services.product_service import _to_dict
                db.commit()
                return ResolveResult(matched_locally=True, product=_to_dict(product), resolution_id=None)
        cached = get_fresh_cache_entries(db, barcode)
        db.commit()

    settings = get_settings()
    missing_sources = {c.source_id for c in ALL_SOURCES} - set(cached.keys())

    # Step 2: no DB session open. External fan-out, coalesced per barcode.
    if missing_sources:
        async def do_fetch() -> dict[str, SourceResult]:
            return await _fetch_all_sources(barcode, settings)

        fresh_results = await _single_flight.run(barcode, do_fetch)
    else:
        fresh_results = {}
    all_results = {**cached, **{s: r for s, r in fresh_results.items() if s in missing_sources}}

    merged = _merge_all_fields(all_results)
    proposed_fields = {name: _field_result_to_dict(result) for name, result in merged.items()}

    # Step 3 (spec §4.2): short session, write cache + resolution, then close.
    with Session(get_engine()) as db:
        if fresh_results:
            upsert_cache_entries(db, barcode, fresh_results, settings)
        resolution_id = create_resolution(db, barcode, proposed_fields, settings)
        db.commit()

    return ResolveResult(matched_locally=False, product=None, resolution_id=resolution_id, fields=merged)


@dataclass(frozen=True)
class ReResolveResult:
    product_id: str
    resolution_id: str
    diff: dict[str, dict] = field(default_factory=dict)


async def re_resolve(product_id: str, barcode: str) -> ReResolveResult | None:
    import json as _json
    from ..services.product_service import _to_dict

    with Session(get_engine()) as db:
        barcode_row = find_barcode(db, barcode)
        if barcode_row is None or barcode_row.product_id != product_id:
            db.commit()
            return None
        product = get_product(db, product_id)
        if product is None or product.deleted_at is not None:
            db.commit()
            return None
        current = _to_dict(product)
        provenance = _json.loads(product.field_provenance) if product.field_provenance else {}
        db.commit()

    settings = get_settings()

    # Bypasses the cache for THIS barcode (spec §3.2/§4) — always a fresh fetch.
    async def do_fetch() -> dict[str, SourceResult]:
        return await _fetch_all_sources(barcode, settings)

    fresh_results = await _single_flight.run(f"re-resolve:{barcode}", do_fetch)
    merged = _merge_all_fields(fresh_results)
    proposed_fields = {name: _field_result_to_dict(result) for name, result in merged.items()}

    field_key_map = {"name": "name", "brand": "brand", "quantity": "quantity", "imageUrl": "imageUrl", "category": "category", "variant": "variant"}
    current_value_key_map = {"name": "name", "brand": "brand", "quantity": "quantity", "imageUrl": "imageUrl", "category": "category", "variant": "variant"}
    diff: dict[str, dict] = {}
    for field_name, result in merged.items():
        is_manual = bool(provenance.get(field_name, {}).get("manual"))
        current_value = current.get(current_value_key_map[field_name])
        proposed_value = result.value
        changed = (not is_manual) and proposed_value is not None and proposed_value != current_value
        if field_name == "quantity":
            current_unit = current.get("unit")
            current_value, size_changed = quantity_diff(
                current["quantity"], current_unit["abbreviation"] if current_unit else None,
                proposed_value,
            )
            changed = (not is_manual) and proposed_value is not None and size_changed
        diff[field_name] = {
            "currentValue": current_value, "proposedValue": proposed_value if not is_manual else None,
            "source": result.selected_source, "confidence": result.confidence.value if result.confidence else None,
            "manual": is_manual, "changed": changed,
        }

    with Session(get_engine()) as db:
        upsert_cache_entries(db, barcode, fresh_results, settings)
        resolution_id = create_resolution(db, barcode, proposed_fields, settings)
        db.commit()

    return ReResolveResult(product_id=product_id, resolution_id=resolution_id, diff=diff)
