import pytest

from inventra_backend.resolver.name_normalizer import normalize_product_name, normalize_category
from inventra_backend.resolver.resolver_service import _merge_all_fields
from inventra_backend.resolver.source_client import SourceCandidate, SourceResult


@pytest.mark.parametrize("raw,expected", [
    ("MUELLERMILCH", "Muellermilch"), ("m\u00fcllermilch", "M\u00fcllermilch"),
    ("VOLLMILCH 3,5%", "Vollmilch 3,5%"), ("iPhone", "iPhone"),
    ("M\u00fcller Milch", "M\u00fcller Milch"), ("  M\u00fcller\t Milch\n ", "M\u00fcller Milch"),
    ("Mu\u0308ller Milch", "M\u00fcller Milch"), ("M\u0000ilch", "Milch"),
    ("M\u00c3\u00bcller", "M\u00fcller"), ("stra\u00dfe", "Stra\u00dfe"),
    ("STRA\u1e9eE", "Stra\u00dfe"), ("iPhone M\u00c3x", "iPhone M\u00c3x"),
    (" ", None), (None, None), ("123 3,5%", "123 3,5%"),
])
def test_names(raw, expected):
    assert normalize_product_name(raw) == expected


@pytest.mark.parametrize("raw,expected", [("en:dairy-products", "dairy products"),
    ("de:milch-produkte", "milch produkte"), (" Milch ", "Milch"), (" ", None), ("en: ", None)])
def test_categories(raw, expected):
    assert normalize_category(raw) == expected


def test_merge_normalizes_cached_candidates_and_absent_fields():
    result = _merge_all_fields({"off": SourceResult("off", "FOUND",
        SourceCandidate("VOLLMILCH 3,5%", "  ", None, None, "en:dairy-products", "\t"))})
    assert result["name"].value == "Vollmilch 3,5%"
    assert result["brand"].value is None
    assert result["variant"].value is None
    assert result["category"].value == "dairy products"
