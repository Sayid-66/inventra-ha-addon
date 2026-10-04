"""Pure naming for resolver proposals and explicit maintenance, never user PATCHes."""
import re
import unicodedata

from .name_normalizer import normalize_product_name
from .plausibility import is_plausible_text
from ..services.unit_normalizer import canonical_unit

MODIFIER_ONLY = {"zero", "light", "original", "classic", "bio", "mild", "extra",
                 "plus", "max", "sensitive", "natur", "naturell", "fein", "frisch",
                 "neu", "new"}

# Deliberately small German slogan list; matching requires a whole opener word.
SLOGAN_OPENERS = ("frisch vom", "frisch aus", "aus der", "aus dem", "aus",
                  "vom", "zum", "f\u00fcr", "mit", "lecker", "unser", "neu")
SIZE_PATTERN = re.compile(
    r"(?<![\w.,-])(?:(?P<pack>\d+)\s*[x\u00d7]\s*)?(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>[^\W\d_]+\.?)(?!\w)",
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
        parts = re.split(r"([\s.-]+)", brand)
        phrase = "".join(
            (r"\.?[ -]?" if "." in part else r"[ -]?")
            if index % 2 else re.escape(part)
            for index, part in enumerate(parts)
        )
        name = re.sub(r"(?<![\w-])" + phrase + r"(?![\w-])", " ", name, flags=re.IGNORECASE)
    return _tidy(name)


def extract_size_token(name: str) -> tuple[str, str | None]:
    matches = []
    for match in SIZE_PATTERN.finditer(name):
        raw_unit = match['unit'].rstrip('.')
        unit = canonical_unit(raw_unit) or ('cl' if raw_unit.casefold() == 'cl' else None)
        if unit is None or raw_unit.casefold() == 'x':
            continue
        amount = float(match['amount'].replace(',', '.'))
        pack = int(match['pack'] or 1)
        matches.append((match, (amount, unit, pack)))
    if not matches or len({key for _, key in matches}) != 1:
        return name, None
    amount, unit, pack = matches[0][1]
    cleaned = name
    for match, _ in reversed(matches):
        cleaned = cleaned[:match.start()] + ' ' + cleaned[match.end():]
    size = format_size(amount, unit, pack)
    return _tidy(cleaned), size[:-1] if unit == 'Stk.' else size


def strip_size_tokens(name, size_text=None):
    return extract_size_token(name)[0]


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
    if not name or len(name.split()) > 7 or all(
        word.casefold() in MODIFIER_ONLY for word in name.split()
    ):
        return True
    alphabetic = sum(c.isalpha() for c in name)
    if alphabetic < 3 or alphabetic * 2 < sum(not c.isspace() for c in name):
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


def compose_product_name(source_name, brands, category=None, category_ok=False):
    normalized = normalize_product_name(source_name) or ""
    core = strip_brands(normalized, brands)
    core = strip_size_tokens(core)
    core = normalize_product_name(dedupe_words(core)) or ""
    if _has_price_or_currency(normalized) or is_unusable_name(core):
        # Category is an explicit fallback only, never a language/overlap heuristic.
        core = strip_brands(normalize_product_name(category) or "", brands)
        core = normalize_product_name(dedupe_words(strip_size_tokens(core))) or ""
        if (not category_ok or len(core.split()) > 3 or is_unusable_name(core)
                or not is_plausible_text(core)):
            return None
    return core
