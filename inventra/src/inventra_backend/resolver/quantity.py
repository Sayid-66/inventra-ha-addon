from __future__ import annotations

import re
from dataclasses import dataclass

from ..services.unit_normalizer import canonical_unit, normalize_quantity

_VOLUME_UNITS = {"ml": 1.0, "cl": 10.0, "l": 1000.0}  # base unit: ml
_MASS_UNITS = {"g": 1.0, "kg": 1000.0}  # base unit: g
_COUNT_UNITS = {"stk", "st"}

_MULTIPACK = re.compile(
    r"""^\s*(?P<pack>\d+)\s*[x×]\s*(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>ml|cl|l|g|kg|stk|st)\s*$""",
    re.IGNORECASE,
)
_SINGLE = re.compile(
    r"""^\s*(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>ml|cl|l|g|kg|stk|st)\s*$""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuantityCandidate:
    amount: float
    unit: str
    pack_count: int
    raw_text: str

    def total_base_unit_amount(self) -> float | None:
        """Comparison-normalization only (spec §5.1) — never used to rewrite
        the stored value. Returns None if the unit has no comparable base
        (count units aren't volume/mass-convertible)."""
        unit = self.unit.lower()
        if unit in _VOLUME_UNITS:
            return self.amount * _VOLUME_UNITS[unit] * self.pack_count
        if unit in _MASS_UNITS:
            return self.amount * _MASS_UNITS[unit] * self.pack_count
        return None


def parse_quantity(text: str | None) -> QuantityCandidate | None:
    if not text:
        return None
    trimmed = text.strip()
    if not trimmed:
        return None

    # Preserve pack counts for all recognized catalog units, too.
    catalog_pack = re.fullmatch(r"(\d+)\s*[x\u00d7]\s*(\d+(?:[.,]\d+)?)\s*([^\W\d_]+\.?)", trimmed, re.I)
    if catalog_pack and not _MULTIPACK.match(trimmed):
        unit = canonical_unit(catalog_pack[3])
        if unit:
            return QuantityCandidate(float(catalog_pack[2].replace(',', '.')),
                                     unit.lower(), int(catalog_pack[1]), trimmed)

    match = _MULTIPACK.match(trimmed)
    if match:
        amount = float(match.group("amount").replace(",", "."))
        return QuantityCandidate(
            amount=amount, unit=match.group("unit").lower(),
            pack_count=int(match.group("pack")), raw_text=trimmed,
        )

    match = _SINGLE.match(trimmed)
    if match:
        amount = float(match.group("amount").replace(",", "."))
        return QuantityCandidate(
            amount=amount, unit=match.group("unit").lower(), pack_count=1, raw_text=trimmed,
        )

    amount, unit = normalize_quantity(trimmed)
    if amount is not None and unit is not None:
        return QuantityCandidate(amount=amount, unit=unit.lower(), pack_count=1, raw_text=trimmed)
    return None
