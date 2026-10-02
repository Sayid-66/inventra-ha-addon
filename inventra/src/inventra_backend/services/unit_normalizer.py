"""Tolerant external package-size parsing; catalog labels are never stored on products."""
import math
import re


_SYNONYMS = {
    "g": "g gramm gram grams gr",
    "kg": "kg kilogramm kilogram kilograms kilo",
    "ml": "ml milliliter millilitre milliliters",
    "l": "l liter litre liters ltr",
    "Stk.": "stk stück stueck stückzahl piece pieces pc pcs x",
    "Pkg.": "pkg packung packungen pack packs paket",
    "Rolle": "rolle rollen roll rolls",
    "Blatt": "blatt blätter blaetter sheet sheets",
    "Portion": "portion portionen portions serving servings",
    "Tabl.": "tabl tablette tabletten tablet tablets",
    "Kaps.": "kaps kapsel kapseln capsule capsules",
    "Beutel": "beutel bag bags sachet sachets",
}
_ALIASES = {alias.casefold(): canonical for canonical, aliases in _SYNONYMS.items()
            for alias in aliases.split()}
_NUMBER = r"\d+(?:[.,]\d+)?"
_SIZE = re.compile(rf"^\s*({_NUMBER})(?:\s*[x×*]\s*({_NUMBER}))?\s*(.*?)\s*$", re.I)


def _amount(value) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _unit(value) -> str | None:
    return _ALIASES.get(value.strip().rstrip(".").casefold()) if isinstance(value, str) else None


def canonical_unit(value: str | None) -> str | None:
    """Return the canonical abbreviation for a recognized unit label."""
    return _unit(value)


def quantity_diff(
    current_amount: float | None, current_unit: str | None, proposed_text: str | None,
) -> tuple[str | None, bool]:
    """Display the current package size and compare without converting units."""
    display = None
    if current_amount is not None:
        display = f"{current_amount:g}"
        if current_unit:
            display += f" {current_unit}"
    if proposed_text is None:
        return display, False
    proposed_amount, proposed_unit = normalize_quantity(proposed_text)
    if proposed_amount is None:
        return display, proposed_text != display
    same_size = (
        current_amount is not None
        and math.isclose(proposed_amount, current_amount, rel_tol=1e-9, abs_tol=1e-9)
        and proposed_unit == canonical_unit(current_unit)
    )
    return display, not same_size


def normalize_quantity(
    text: str | None = None, amount: float | None = None, unit_string: str | None = None,
) -> tuple[float | None, str | None]:
    """Prefer a valid structured pair, then text, retaining unknown-unit amounts."""
    structured_amount, structured_unit = _amount(amount), _unit(unit_string)
    if structured_amount is not None and structured_unit is not None:
        return structured_amount, structured_unit
    match = _SIZE.fullmatch(text) if isinstance(text, str) else None
    if match:
        parsed = _amount(match[1])
        if match[2] and parsed is not None:
            # Package amount means the total contents: 6x125g => 750g. This
            # preserves all contents in the two available fields, without
            # inventing a per-item field or confusing stock/package counts.
            multiplier = _amount(match[2])
            parsed = _amount(parsed * multiplier) if multiplier is not None else None
        parsed_unit = _unit(match[3])
        if parsed_unit is not None:
            return parsed, parsed_unit
        return structured_amount if structured_amount is not None else parsed, structured_unit
    return structured_amount, structured_unit

# Fixed UUIDv7 identities of the migration seeds, independent of mutable labels.
STANDARD_UNIT_IDS = {'g': '01a0e1f3-1846-731c-8e60-bbd0970212b8', 'kg': '01a0e1f3-1847-74be-b7e8-68a5e6ef60b4', 'ml': '01a0e1f3-1848-7a85-9ec2-1028e90cdb9f', 'l': '01a0e1f3-1849-7a08-9e5b-c524f2953c58', 'Stk.': '01a0e1f3-184a-7e83-88e7-9cec2b172ce8', 'Pkg.': '01a0e1f3-184b-7ecc-b5d9-fcc489e08194', 'Rolle': '01a0e1f3-184c-7daa-ac63-f2354d5307bb', 'Blatt': '01a0e1f3-184d-79a0-9271-09699564a9b2', 'Portion': '01a0e1f3-184e-7b13-bdd8-5dd316310830', 'Tabl.': '01a0e1f3-184f-7215-8085-592d3e56a555', 'Kaps.': '01a0e1f3-1850-782d-9695-08bcc58b7e80', 'Beutel': '01a0e1f3-1851-7353-82a6-0a20b4f53873'}
