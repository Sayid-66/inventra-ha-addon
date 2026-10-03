"""Pure naming for resolver proposals and explicit maintenance, never user PATCHes."""
import re
import unicodedata

from .name_normalizer import normalize_product_name
from .plausibility import is_plausible_text
from ..services.unit_normalizer import canonical_unit

# Deliberately small German slogan list; matching requires a whole opener word.
SLOGAN_OPENERS = ("frisch vom", "frisch aus", "aus der", "aus dem", "aus",
                  "vom", "zum", "f\u00fcr", "mit", "lecker", "unser", "neu")
SIZE_PATTERN = re.compile(
    r"(?<!\w)(?:\d+\s*[x\u00d7]\s*)?\d+(?:[.,]\d+)?\s*(?:kilogramm|milliliter|liter|litre|gramm|kg|ml|cl|g|l|stk\.?)(?!\w)",
    re.IGNORECASE,
)


def _tidy(name):
    return " ".join(name.split()).strip(" -\u2013,;:&")


def split_brands(brands_raw):
    result = []
    seen = set()
    for brand in (brands_raw or "").split(","):
        brand = brand.strip()
        if brand and brand.casefold() not in seen:
            seen.add(brand.casefold())
            result.append(brand)
    return result


def strip_brands(name, brands):
    for brand in sorted((b.strip() for b in brands if b and b.strip()), key=len, reverse=True):
        phrase = r"\s+".join(re.escape(word) for word in brand.split())
        name = re.sub(r"(?<![\w-])" + phrase + r"(?![\w-])", " ", name, flags=re.IGNORECASE)
    return _tidy(name)


def strip_size_tokens(name, size_text=None):
    if size_text:
        # Match the exact appended token, including arbitrary catalog units.
        phrase = r"\s*".join(re.escape(part) for part in size_text.split())
        name = re.sub(r"(?<![\w-])" + phrase + r"(?![\w-])", " ", name,
                      flags=re.IGNORECASE)
    return _tidy(SIZE_PATTERN.sub(" ", name))


def dedupe_words(name):
    seen = set()
    def keep(match):
        word = match.group()
        key = word.casefold()
        if len(word) >= 4 and word.isalpha() and key in seen:
            return ""
        seen.add(key)
        return word
    return _tidy(re.sub(r"[^\W\d_]+(?:-[^\W\d_]+)*", keep, name))


def _has_price_or_currency(name):
    if any(unicodedata.category(c) == "Sc" for c in name):
        return True
    return bool(re.search(
        r"\b(?:EUR|EURO)\b|\b\d+[.,]\d{2}(?!\d|\s*(?:kg|g|ml|cl|l|stk|st)(?!\w))\b",
        name, re.IGNORECASE,
    ))


def is_unusable_name(name):
    name = _tidy(name or "")
    if not name or len(name.split()) > 7:
        return True
    if _has_price_or_currency(name):
        return True
    return any(re.match(re.escape(opener) + r"(?!\w)", name, re.IGNORECASE)
               for opener in SLOGAN_OPENERS)


def format_size(amount, unit, pack_count=1):
    if amount is None or not unit:
        return None
    unit = canonical_unit(unit) or ("cl" if unit.lower() == "cl" else unit)
    def number(value):
        return format(value, ".3f").rstrip("0").rstrip(".").replace(".", ",")
    text = number(amount) + " " + unit
    return f"{number(pack_count)} x {text}" if pack_count != 1 else text


def compose_product_name(source_name, brands, size_text, category=None, category_ok=False):
    normalized = normalize_product_name(source_name) or ""
    core = strip_brands(normalized, brands)
    core = strip_size_tokens(core, size_text) if size_text else core
    source_core = strip_size_tokens(normalized, size_text) if size_text else normalized
    if not core or (core != source_core and len(core.split()) == 1
                    and len(core) <= 4 and core.isalpha()):
        core = source_core
    core = dedupe_words(core)
    if _has_price_or_currency(normalized) or is_unusable_name(core):
        # Category is an explicit fallback only, never a language/overlap heuristic.
        core = strip_brands(normalize_product_name(category) or "", brands)
        if size_text:
            core = strip_size_tokens(core, size_text)
        if (not category_ok or len(core.split()) > 3 or is_unusable_name(core)
                or not is_plausible_text(core)):
            return None
    return f"{core} {size_text}" if size_text else core
