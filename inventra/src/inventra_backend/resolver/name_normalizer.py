"""Pure cleanup for source candidates only; user-entered names stay verbatim."""
import re
import unicodedata


def normalize_product_name(name: str | None) -> str | None:
    if name is None:
        return None
    # Repair only recognizable UTF-8 decoded as Latin-1/Windows-1252.
    if any(marker in name for marker in ("\u00c3", "\u00c2", "\u00e2\u0080", "\u00e2\u20ac")):
        for encoding in ("latin-1", "cp1252"):
            try:
                repaired = name.encode(encoding).decode("utf-8")
                if repaired.encode("utf-8").decode(encoding) == name:
                    name = repaired
                    break
            except (UnicodeError, LookupError):
                pass
    name = unicodedata.normalize("NFC", name)
    name = "".join(c for c in name if c.isspace() or not unicodedata.category(c).startswith("C"))
    name = " ".join(name.split())
    if name.isupper() or name.islower():
        name = " ".join(word[:1].upper() + word[1:].lower() for word in name.split(" "))
    return name or None


def clean_source_text(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


def normalize_category(value: str | None) -> str | None:
    value = clean_source_text(value)
    if value is None:
        return None
    value = re.sub(r"^[a-zA-Z]{2,3}:", "", value)
    return clean_source_text(value.replace("-", " "))
