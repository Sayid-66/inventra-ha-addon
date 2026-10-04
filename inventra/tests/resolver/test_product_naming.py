import json

import httpx
import pytest

from inventra_backend.resolver.product_naming import (
    split_brands, strip_brands, strip_size_tokens, dedupe_words,
    is_unusable_name, format_size, compose_product_name, extract_size_token, SLOGAN_OPENERS,
)
from inventra_backend.resolver.resolver_service import _merge_all_fields
from inventra_backend.resolver.source_client import SourceCandidate, SourceResult, OpenFactsClient

ALDI = "Aldi, ALDI MEINE METZGEREI, ALDI MEINE METZGEREI Huhn, Meine Metzgerei, geka-frisch-frost"
JUNK = "ALDI MEINE METZGEREI Huhn H\u00e4hnchen-Geschnetzeltes Frisch aus dem Brustfilet zum Braten Aus der Frischetruhe 3.29\u20ac 400g Packung 1kg 9.98\u20ac"
CASES = [
    ("Gyros Geschnetzeltes Gyros", "Gut Ponholz", None, "400 g", "Gyros Geschnetzeltes"),
    (JUNK, ALDI, "H\u00e4hnchenbrustfilet", "400 g", "H\u00e4hnchenbrustfilet"),
    ("Frisch vom Schwein", "", "Hackfleisch", "500 g", "Hackfleisch"),
    ("H\u00e4hnchen Geschnetzeltes", "Gut Ponholz", "H\u00e4hnchen", "400 g", "H\u00e4hnchen Geschnetzeltes"),
    ("Pazifischer Wildlachs", "Ocean Sea", "Wild salmons", "200 g", "Pazifischer Wildlachs"),
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
    assert compose_product_name("", [], "eins zwei drei vier", True) is None


@pytest.mark.parametrize("name,raw,category,size,expected", CASES)
def test_real_cases(name, raw, category, size, expected):
    brands = split_brands(raw)
    assert compose_product_name(name, brands, category, bool(category)) == expected
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
    assert fields["name"].value == "Fisch"
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


@pytest.mark.parametrize("source,brand,size,expected,category", [
    ("Beck's Pils 0,33 l", "Beck's", None, "Pils", "Ersatz"),
    ("Ben & Jerry's Cookie Dough Eis 465 ml", "Ben & Jerry's", "465 ml", "Cookie Dough Eis", "Ersatz"),
    ("Unser Norden Butter", "Unser Norden", None, "Butter", "Ersatz"),
    ("Zum Dorfkrug Bratwurst", "Zum Dorfkrug", None, "Bratwurst", "Ersatz"),
    ("Nutella", "Nutella", None, "Ersatz", "Ersatz"),
    ("Coca-Cola", "Coca-Cola", None, "Ersatz", "Ersatz"),
    ("Coca-Cola Zero", "Coca-Cola", None, None, None),
    ("Coca-Cola Zero 1 l", "Coca-Cola", "1 l", None, None),
    ("Bio-M\u00f6hren", "Bio", None, "Bio-M\u00f6hren", "Ersatz"),
    ("Milch 1 l", "", None, "Milch", "Ersatz"),
    ("Cola 1 Liter", "", "1 l", "Cola", "Ersatz"),
    ("2 St. Pauli", "", "1 l", "2 St. Pauli", "Ersatz"),
    ("2 St Pauli", "", "1 l", "2 St Pauli", "Ersatz"),
    ("Eine Lange Marke eins zwei drei vier fuenf sechs sieben", "Eine Lange Marke", None,
     "Eins Zwei Drei Vier Fuenf Sechs Sieben", "Ersatz"),
])
def test_cleaned_core_contract(source, brand, size, expected, category):
    assert compose_product_name(source, [brand], category, category is not None) == expected


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
    assert compose_product_name("Nutella", ["Nutella"], "Aufstrich", True) == "Aufstrich"


@pytest.mark.parametrize("unit", ["Rolle", "Pkg.", "Beutel", "Blatt", "Portion", "Tabl.", "Kaps."])
def test_catalog_size_idempotence(unit):
    size = format_size(8, unit)
    assert compose_product_name("Produkt " + size, []) == "Produkt"
    assert compose_product_name("Produkt " + size.upper() + " Extra", []) == "Produkt Extra"
    assert compose_product_name("Produkt", []) == "Produkt"
    assert compose_product_name("Produkt 8 Sonder-Einheit", []) == "Produkt 8 Sonder-Einheit"


@pytest.mark.parametrize("unit", ["Liter", "Litre", "Gramm", "Kilogramm", "Milliliter"])
def test_long_size_units(unit):
    assert compose_product_name("Produkt 1 " + unit, []) == "Produkt"


def test_fixed_decimal_and_canonical_unit_format():
    assert format_size(1000000, "g") == "1000000 g"
    assert format_size(0.0001, "l") == "0 l"
    assert format_size(1.2346, "g") == "1,235 g"
    assert format_size(2, "stk", 6) == "6 x 2 Stk."
    assert format_size(2, "pkg.") == "2 Pkg."


@pytest.mark.parametrize("token,expected", [
    ("400g", "400 g"), ("400 g", "400 g"), ("1,5L", "1,5 l"),
    ("0,33 l", "0,33 l"), ("6 x 0,33 l", "6 x 0,33 l"),
    ("2x250g", "2 x 250 g"), ("2\u00d7250g", "2 x 250 g"), ("500 Gramm", "500 g"),
    ("1kg", "1 kg"), ("750ml", "750 ml"), ("10 Stk", "10 Stk"),
    ("4 Rollen", "4 Rolle"), ("4 Blatt", "4 Blatt"), ("10 cl", "10 cl"),
])
def test_extract_size(token, expected):
    assert extract_size_token("Produkt " + token) == ("Produkt", expected)


@pytest.mark.parametrize("name", ["B12", "Vitamin C 500mg", "7Up", "Cola 0,0%",
    "Produkt 400g 1kg", "Produkt abc400g", "Produkt F-18g", "Produkt B12 g",
    "Produkt 4 x", "Produkt 400grammwort"])
def test_extract_size_negative(name):
    assert extract_size_token(name) == (name, None)


@pytest.mark.parametrize("name", ["@@@ ||| 9qz", "0o0 ||| ???", "ab"])
def test_junk_names(name):
    assert is_unusable_name(name)


@pytest.mark.parametrize("core", ["Pils", "Gin", "Tee", "Heu"])
def test_short_core_without_brand(core):
    assert compose_product_name("Kellerkrone " + core, ["Kellerkrone"]) == core


@pytest.mark.parametrize("name,brand", [("Milka 100g", "Milka"), ("Bio 100 g", "Bio")])
def test_brand_size_only(name, brand):
    assert compose_product_name(name, [brand]) is None
    assert compose_product_name(name, [brand], "Schokolade", True) == "Schokolade"


@pytest.mark.parametrize("token,amount,unit,pack", [
    ("6 x 0,33 l", .33, "l", 6), ("2 x 4 Rolle", 4, "rolle", 2),
    ("2 x 4 Stk.", 4, "stk", 2),
])
def test_extracted_size_preserves_pack_structure(token, amount, unit, pack):
    from inventra_backend.resolver.quantity import parse_quantity
    parsed = parse_quantity(extract_size_token("Produkt " + token)[1])
    assert (parsed.amount, parsed.unit, parsed.pack_count) == (amount, unit, pack)


@pytest.mark.parametrize("core", ["GOLDB\u00c4REN", "goldb\u00e4ren"])
def test_brand_removal_casing_is_idempotent(core):
    first = compose_product_name("Haribo " + core, ["Haribo"])
    assert first == "Goldb\u00e4ren"
    assert compose_product_name(first, ["Haribo"]) == first
