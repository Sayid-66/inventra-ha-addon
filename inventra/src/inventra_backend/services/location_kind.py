"""Name-based freezer detection shared in rule text with the Android classifier."""


def is_freezer_location_name(name: str) -> bool:
    """Lowercase, fold umlauts/eszett; match freezer substrings or the token tk."""
    normalized = name.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    tokens = "".join(char if char.isalpha() else " " for char in normalized).split()
    return (
        any(part in normalized for part in ("gefrier", "tiefkuehl", "tiefkuhl", "froster", "truhe"))
        or "tk" in tokens
    )
