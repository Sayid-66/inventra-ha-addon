from __future__ import annotations

import enum
from dataclasses import dataclass, field

from .plausibility import (
    is_plausible_brand,
    is_plausible_image_url,
    is_plausible_quantity,
    is_plausible_text,
)
from .quantity import QuantityCandidate, parse_quantity


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class FieldMergeResult:
    value: str | None
    suggested: str | None
    confidence: Confidence | None
    selected_source: str | None
    contributing_sources: list[str] = field(default_factory=list)
    conflicting_sources: list[str] = field(default_factory=list)


def _comparison_key_text(value: str) -> str:
    """Comparison normalization for text fields (spec §5.1): case-folding
    only. Never used for the stored value."""
    return value.strip().casefold()


def _score_group(group_size: int, total_groups: int) -> Confidence:
    """Bounded consensus (spec §6 item 2): a single plausible source is
    MEDIUM at best (never HIGH without agreement); two or more agreeing,
    plausible sources reach HIGH, capped regardless of how many more than
    two agree, since the four Open-Facts projects are not four independent
    proofs."""
    if group_size >= 2:
        return Confidence.HIGH
    return Confidence.MEDIUM


def _merge_generic(
    candidates_by_source: dict[str, str | None],
    is_plausible: callable,
    comparison_key: callable,
) -> FieldMergeResult:
    plausible = {
        source: value.strip() for source, value in candidates_by_source.items()
        if value and is_plausible(value)
    }
    if not plausible:
        return FieldMergeResult(value=None, suggested=None, confidence=None, selected_source=None)

    groups: dict[str, list[tuple[str, str]]] = {}
    for source, value in plausible.items():
        key = comparison_key(value)
        groups.setdefault(key, []).append((source, value))

    best_key = max(groups, key=lambda k: len(groups[k]))
    best_group = groups[best_key]
    contributing = [s for s, _ in best_group]
    conflicting = [s for k, members in groups.items() if k != best_key for s, _ in members]
    confidence = _score_group(len(best_group), len(groups))
    selected_source, selected_value = best_group[0]

    if confidence == Confidence.LOW:
        return FieldMergeResult(
            value=None, suggested=selected_value, confidence=confidence,
            selected_source=selected_source, contributing_sources=contributing,
            conflicting_sources=conflicting,
        )
    return FieldMergeResult(
        value=selected_value, suggested=None, confidence=confidence,
        selected_source=selected_source, contributing_sources=contributing,
        conflicting_sources=conflicting,
    )


def merge_text_field(field_kind: str, candidates_by_source: dict[str, str | None]) -> FieldMergeResult:
    """field_kind: "name" | "generic" uses is_plausible_text; "brand" uses
    the stricter is_plausible_brand; "url" uses is_plausible_image_url
    (spec §6)."""
    if field_kind == "brand":
        is_plausible = is_plausible_brand
    elif field_kind == "url":
        is_plausible = is_plausible_image_url
    else:
        is_plausible = is_plausible_text
    return _merge_generic(candidates_by_source, is_plausible, _comparison_key_text)


def merge_quantity_field(candidates_by_source: dict[str, str | None]) -> FieldMergeResult:
    """QuantityCandidate is treated atomically (spec §2): parsed once per
    source, grouped for consensus by total_base_unit_amount() (so 500 ml
    and 0,5 l land in the same group), but the returned value is always the
    winning source's own literal text — never a synthesized spelling, and a
    multipack's pack structure is never lost."""
    parsed: dict[str, QuantityCandidate] = {}
    for source, text in candidates_by_source.items():
        if not text:
            continue
        candidate = parse_quantity(text)
        if candidate is not None and is_plausible_quantity(candidate):
            parsed[source] = candidate

    if not parsed:
        return FieldMergeResult(value=None, suggested=None, confidence=None, selected_source=None)

    def _dimension(unit: str) -> str:
        # total_base_unit_amount() alone is not a safe consensus key: e.g.
        # "500 ml" and "500 g" both evaluate to 500.0 in their respective
        # base units, so grouping by that number alone would wrongly treat
        # a volume and a mass candidate as agreeing. Group by (dimension,
        # amount) instead so cross-dimension "agreement" can never happen.
        unit = unit.lower()
        if unit in ("ml", "cl", "l"):
            return "volume"
        if unit in ("g", "kg"):
            return "mass"
        return "count"

    groups: dict[tuple[str, float | None], list[tuple[str, QuantityCandidate]]] = {}
    for source, candidate in parsed.items():
        key = (_dimension(candidate.unit), candidate.total_base_unit_amount())
        groups.setdefault(key, []).append((source, candidate))

    best_key = max(groups, key=lambda k: len(groups[k]))
    best_group = groups[best_key]
    contributing = [s for s, _ in best_group]
    conflicting = [s for k, members in groups.items() if k != best_key for s, _ in members]
    confidence = _score_group(len(best_group), len(groups))
    selected_source, selected_candidate = best_group[0]

    if confidence == Confidence.LOW:
        return FieldMergeResult(
            value=None, suggested=selected_candidate.raw_text, confidence=confidence,
            selected_source=selected_source, contributing_sources=contributing,
            conflicting_sources=conflicting,
        )
    return FieldMergeResult(
        value=selected_candidate.raw_text, suggested=None, confidence=confidence,
        selected_source=selected_source, contributing_sources=contributing,
        conflicting_sources=conflicting,
    )
