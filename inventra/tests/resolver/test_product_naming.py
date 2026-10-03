import json

import httpx
import pytest

from inventra_backend.resolver.product_naming import (
    split_brands, strip_brands, strip_size_tokens, dedupe_words,
    is_unusable_name, format_size, compose_product_name, SLOGAN_OPENERS,
)
from inventra_backend.resolver.resolver_service import _merge_all_fields
from inventra_backend.resolver.source_client import SourceCandidate, SourceResult, OpenFactsClient

ALDI = "Aldi, ALDI MEINE METZGEREI, ALDI MEINE METZGEREI Huhn, Meine Metzgerei, geka-frisch-frost"
JUNK = "ALDI MEINE METZGEREI Huhn H\u00e4hnchen-Geschnetzeltes Frisch aus dem Brustfilet zum Braten Aus der Frischetruhe 3.29\u20ac 400g Packung 1kg 9.98\u20ac"
CASES = [
    ("Gyros Geschnetzeltes Gyros", "Gut Ponholz", None, "400 g", "Gyros Geschnetzeltes 400 g"),
    (JUNK, ALDI, "H\u00e4hnchenbrustfilet", "400 g", "H\u00e4hnchenbrustfilet 400 g"),
    ("Frisch vom Schwein", "", "Hackfleisch", "500 g", "Hackfleisch 500 g"),
    ("H\u00e4hnchen Geschnetzeltes", "Gut Ponholz", "H\u00e4hnchen", "400 g", "H\u00e4hnchen Geschnetzeltes 400 g"),
    ("Pazifischer Wildlachs", "Ocean Sea", "Wild salmons", "200 g", "Pazifischer Wildlachs 200 g"),
]


def test_brands():
    assert split_brands(" Aldi, , ALDI, Ocean Sea ") == ["Aldi", "Ocean Sea"]
    assert strip_brands("ALDI MEINE METZGEREI Huhn Filet Aldi", split_brands(ALDI)) == "Filet"
    assert strip_brands("M\u00fcllermilch M\u00fcller - M\u00dcLLER", ["M\u00fcller"]) == "M\u00fcllermilch"
    assert strip_brands("Aldi & Ocean Sea: Fisch", ["Aldi", "Ocean Sea"]) == "Fisch"
    assert strip_brands("Aldi Aldi", ["Aldi"]) == ""


@pytest.mark.parametrize("size", ["400g", "400 g", "0,5 l", "1 kg", "6x330ml", "6 x 330 ml"])
def test_strip_size(size):
    assert strip_size_tokens("Fisch " + size) == "Fisch"
    assert strip_size_tokens("2 in 1") == "2 in 1"


def test_format_and_duplicates():
    assert format_size(1.5, "KG") == "1,5 kg"
    assert format_size(330, "ml", 6) == "6 x 330 ml"
    assert format_size(2, "Stk.") == "2 Stk."
    assert format_size(None, "g") is None
    assert format_size(2, None) is None
    assert dedupe_words("Gyros Geschnetzeltes GYROS") == "Gyros Geschnetzeltes"
    assert dedupe_words("Ei Ei") == "Ei Ei"


@pytest.mark.parametrize("name", ["3.29\u20ac", "9,98 EUR", "Preis 3.29", "", "eins zwei drei vier fuenf sechs sieben acht"] + [x + " Schwein" for x in SLOGAN_OPENERS])
def test_unusable(name):
    assert is_unusable_name(name)


def test_usable():
    assert not is_unusable_name("Neuhaus Fisch")
    assert not is_unusable_name("Pazifischer Wildlachs")
    assert compose_product_name("Frisch vom Schwein", [], None) is None
    assert compose_product_name("", [], None, "eins zwei drei vier", True) is None


@pytest.mark.parametrize("name,raw,category,size,expected", CASES)
def test_real_cases(name, raw, category, size, expected):
    brands = split_brands(raw)
    assert compose_product_name(name, brands, size, category, bool(category)) == expected
    candidate = SourceCandidate(name, brands[0] if brands else None, size, None, category, None, tuple(brands))
    result = _merge_all_fields({"off": SourceResult("off", "FOUND", candidate)})
    assert result["name"].value == expected
    assert result["brand"].value == (brands[0] if brands else None)
    assert result["category"].value == category
    assert result["quantity"].value == size
    assert result["variant"].value is None
    if name in (JUNK, "Frisch vom Schwein"):
        assert result["name"].confidence.value == "medium"
        assert result["name"].selected_source == result["category"].selected_source
        assert result["name"].contributing_sources == result["category"].contributing_sources


def test_agreement_after_brand_removal_and_merged_size():
    results = {source: SourceResult(source, "FOUND", SourceCandidate(name, brand, size, None, None, None, (brand,)))
               for source, name, brand, size in [("off", "Aldi Fisch 400g", "Aldi", "400 g"),
                                                 ("opf", "Ocean Sea Fisch", "Ocean Sea", "0,4 kg")]}
    fields = _merge_all_fields(results)
    assert fields["name"].value == "Fisch 400 g"
    assert fields["name"].confidence.value == "high"


@pytest.mark.anyio
async def test_source_keeps_raw_name_and_all_brands():
    client = OpenFactsClient("off", "https://example.org", 1, "test", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": 1, "product": {
            "product_name_de": JUNK, "brands": ALDI, "quantity": "400 g"}})))
    result = await client.fetch("4061458010597")
    assert result.candidate.brand == "Aldi"
    assert result.candidate.brands == tuple(split_brands(ALDI))
    assert result.candidate.name == JUNK


def test_cache_old_and_extra_keys(db_session):
    from dataclasses import asdict
    from datetime import datetime, timedelta
    from inventra_backend.db.models import ResolverSourceCache
    from inventra_backend.resolver.cache import get_fresh_cache_entries
    old = asdict(SourceCandidate("Fisch", "Ocean Sea", None, None, None, None))
    del old["brands"]
    old["future_key"] = "ignored"
    db_session.add(ResolverSourceCache(barcode="20174552", source="off", status="FOUND",
        candidate_json=json.dumps(old), fetched_at=datetime.utcnow(), expires_at=datetime.utcnow()+timedelta(hours=1)))
    db_session.flush()
    assert get_fresh_cache_entries(db_session, "20174552")["off"].candidate.brands == ()


@pytest.mark.parametrize("source,brand,size,expected", [
    ("Beck's Pils 0,33 l", "Beck's", None, "Pils 0,33 l"),
    ("Ben & Jerry's Cookie Dough Eis 465 ml", "Ben & Jerry's", "465 ml", "Cookie Dough Eis 465 ml"),
    ("Unser Norden Butter", "Unser Norden", None, "Butter"),
    ("Zum Dorfkrug Bratwurst", "Zum Dorfkrug", None, "Bratwurst"),
    ("Nutella", "Nutella", None, "Nutella"),
    ("Coca-Cola", "Coca-Cola", None, "Coca-Cola"),
    ("Coca-Cola Zero", "Coca-Cola", None, "Coca-Cola Zero"),
    ("Coca-Cola Zero 1 l", "Coca-Cola", "1 l", "Coca-Cola Zero 1 l"),
    ("Bio-M\u00f6hren", "Bio", None, "Bio-M\u00f6hren"),
    ("Milch 1 l", "", None, "Milch 1 l"),
    ("Cola 1 Liter", "", "1 l", "Cola 1 l"),
    ("2 St. Pauli", "", "1 l", "2 St. Pauli 1 l"),
    ("2 St Pauli", "", "1 l", "2 St Pauli 1 l"),
    ("Eine Lange Marke eins zwei drei vier fuenf sechs sieben", "Eine Lange Marke", None,
     "eins zwei drei vier fuenf sechs sieben"),
])
def test_cleaned_core_contract(source, brand, size, expected):
    assert compose_product_name(source, [brand], size, "Ersatz", True) == expected


@pytest.mark.parametrize("name", ["Beck's Pils 0,33 l", "Rotwein 0,75 l",
    "Schweppes 1,25 l", "Butter 0,25 kg", "Saft 0.33ml", "Box 1,25 stk", "Box 1,25 st"])
def test_decimal_sizes_are_usable(name):
    assert not is_unusable_name(name)


@pytest.mark.parametrize("name", ["3\u20ac", "Milch \u20ac", "Milch \u00a3",
    "Milch 0,33 l EUR", "Milch 0,33 l \u20ac", "Preis 1,25", "Preis 1,25 label"])
def test_currency_and_prices_rejected(name):
    assert is_unusable_name(name)
    assert compose_product_name(name, [], None) is None


def test_hyphenated_compounds_and_core_limit():
    name = "Erdbeer Joghurt mit Erdbeer-St\u00fcckchen"
    assert dedupe_words(name) == name
    assert dedupe_words("Erdbeer-St\u00fcckchen Erdbeer-St\u00fcckchen") == "Erdbeer-St\u00fcckchen Erdbeer-St\u00fcckchen"
    assert compose_product_name("Marke eins zwei drei vier fuenf sechs sieben acht", ["Marke"], None) is None
    assert compose_product_name("Nutella", ["Nutella"], None, "Aufstrich", True) == "Nutella"


@pytest.mark.parametrize("unit", ["Rolle", "Pkg.", "Beutel", "Blatt", "Portion", "Tabl.", "Kaps.", "Sonder-Einheit"])
def test_arbitrary_size_idempotence(unit):
    size = format_size(8, unit)
    expected = "Produkt " + size
    assert compose_product_name("Produkt", [], size) == expected
    assert compose_product_name(expected, [], size) == expected
    assert compose_product_name("Produkt " + size.upper() + " Extra", [], size) == "Produkt Extra " + size


@pytest.mark.parametrize("unit", ["Liter", "Litre", "Gramm", "Kilogramm", "Milliliter"])
def test_long_size_units(unit):
    assert compose_product_name("Produkt 1 " + unit, [], "2 g") == "Produkt 2 g"


def test_fixed_decimal_and_canonical_unit_format():
    assert format_size(1000000, "g") == "1000000 g"
    assert format_size(0.0001, "l") == "0 l"
    assert format_size(1.2346, "g") == "1,235 g"
    assert format_size(2, "stk", 6) == "6 x 2 Stk."
    assert format_size(2, "pkg.") == "2 Pkg."
