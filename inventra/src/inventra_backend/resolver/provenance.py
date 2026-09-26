from __future__ import annotations

from datetime import datetime
import unicodedata


def _confirmation_equal(a: str, b: str) -> bool:
    """Strict confirmation equality (spec §5.1): trim + Unicode
    normalization only — never case-folding, never semantic/unit
    equivalence. GO NRGY != Go Nrgy here, deliberately."""
    return unicodedata.normalize("NFC", a.strip()) == unicodedata.normalize("NFC", b.strip())


def derive_field_provenance(
    resolution: dict | None, submitted: dict[str, str | None], previous_provenance: dict,
) -> dict:
    """For every field present (non-None) in `submitted`: credit the
    resolver's real source/confidence if the value matches what
    `resolution` actually proposed under confirmation equality, else mark
    `manual`. Fields absent from `submitted` keep whatever provenance they
    already had (spec §3.1 — no whole-record lock)."""
    result = dict(previous_provenance)
    proposed_fields = (resolution or {}).get("proposed_fields", {})
    now = datetime.utcnow().isoformat()

    for field_name, value in submitted.items():
        if value is None:
            continue
        proposed = proposed_fields.get(field_name)
        proposed_value = proposed.get("value") if proposed else None
        if proposed_value is not None and _confirmation_equal(value, proposed_value):
            result[field_name] = {
                "selectedSource": proposed["selectedSource"],
                "contributingSources": proposed.get("contributingSources", []),
                "conflictingSources": proposed.get("conflictingSources", []),
                "confidence": proposed["confidence"],
                "manual": False,
                "resolvedAt": now,
            }
        else:
            result[field_name] = {"selectedSource": "manual", "manual": True, "modifiedAt": now}

    return result
