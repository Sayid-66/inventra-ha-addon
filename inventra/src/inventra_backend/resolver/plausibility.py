from __future__ import annotations

import re
from urllib.parse import urlparse

from .quantity import QuantityCandidate

MAX_PLAUSIBLE_TEXT_LENGTH = 80
MAX_PLAUSIBLE_SEPARATOR_COUNT = 2

_PERCENT_IN_PARENS = re.compile(r"\(\s*\d+[.,]?\d*\s*%\s*\)")
_URL_OR_EMAIL = re.compile(r"(https?://|www\.|@\w+\.\w+)", re.IGNORECASE)
_POSTAL_CODE_LIKE = re.compile(r"\b\d{5}\b")
_LEGAL_BOILERPLATE = re.compile(
    r"vertrieben durch|hergestellt in|distributed by|manufactured in|abgef[uü]llt (f[uü]r|von|durch)",
    re.IGNORECASE,
)
_BARE_NUMBER_WITHOUT_UNIT = re.compile(
    r"\b\d{3,}\b(?!\s*(ml|l|cl|g|kg|stk|st|pack|x)\b)", re.IGNORECASE,
)


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.lower(), flags=re.UNICODE) if len(t) >= 3]


def _looks_like_ingredient_fragment(candidate: str, reference_texts: list[str]) -> bool:
    candidate_tokens = _tokenize(candidate)
    if len(candidate_tokens) < 4:
        return False
    for reference in reference_texts:
        reference_tokens = set(_tokenize(reference))
        if not reference_tokens:
            continue
        overlap = sum(1 for t in candidate_tokens if t in reference_tokens)
        if overlap / len(candidate_tokens) >= 0.5:
            return True
    return False


def is_plausible_text(candidate: str, reference_texts: list[str] | None = None) -> bool:
    """Generic plausibility gate for name/brand/category/variant text —
    ported from the Android ProductNameSelector.kt heuristics, generalized
    beyond food-specific assumptions (spec §6)."""
    trimmed = (candidate or "").strip()
    if not trimmed:
        return False
    if len(trimmed) > MAX_PLAUSIBLE_TEXT_LENGTH:
        return False
    separator_count = trimmed.count(",") + trimmed.count(";")
    if separator_count > MAX_PLAUSIBLE_SEPARATOR_COUNT:
        return False
    if _PERCENT_IN_PARENS.search(trimmed):
        return False
    if _URL_OR_EMAIL.search(trimmed):
        return False
    if _POSTAL_CODE_LIKE.search(trimmed):
        return False
    if _LEGAL_BOILERPLATE.search(trimmed):
        return False
    if _BARE_NUMBER_WITHOUT_UNIT.search(trimmed):
        return False
    if _looks_like_ingredient_fragment(trimmed, reference_texts or []):
        return False
    return True


_ADDRESS_LIKE = re.compile(r"\b\d{1,4}\s*,\s*\d{5}\b|\bstra(ss|ß)e\b", re.IGNORECASE)


def is_plausible_brand(candidate: str) -> bool:
    """Brand-specific tightening on top of is_plausible_text: additionally
    rejects address-shaped text (spec §6)."""
    trimmed = (candidate or "").strip()
    if not is_plausible_text(trimmed):
        return False
    if _ADDRESS_LIKE.search(trimmed):
        return False
    return True


def is_plausible_quantity(candidate: QuantityCandidate | None) -> bool:
    """A QuantityCandidate is plausible iff it parsed at all (parse_quantity
    already enforces number+recognized-unit) — see spec §6."""
    return candidate is not None


def is_plausible_image_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
