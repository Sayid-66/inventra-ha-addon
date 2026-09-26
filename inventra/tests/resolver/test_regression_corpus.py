"""Frozen regression corpus (spec §8): each case is a real product *shape*,
not necessarily a live-fetched payload (external data changes constantly —
spec §8's "fixture-based, not live-API" rule). Covers the calibration
priority order name -> brand -> quantity -> imageUrl, then category/variant
(user instruction), across several product types so the resolver isn't
tuned only for the EAN 4260456731392 energy-drink case."""
from inventra_backend.resolver.scoring import Confidence, merge_text_field, merge_quantity_field


def test_ean_4260456731392_garbled_ocr_name_is_rejected():
    """The frozen regression case from spec §0 — OCR/nutrition-text
    garbage must never become a stored name, from any single source."""
    garbage = "Pantothensure ZERO GO NRGY by 9180 BOOST BERRIES G"
    result = merge_text_field("name", {"off": garbage})
    assert result.value is None
    assert result.suggested is None


def test_clean_food_item():
    result = merge_text_field("name", {"off": "Vollmilch 3,5%", "opf": "Vollmilch 3,5%"})
    assert result.value == "Vollmilch 3,5%"
    assert result.confidence == Confidence.HIGH
    quantity = merge_quantity_field({"off": "1 l"})
    assert quantity.value == "1 l"


def test_beverage_with_legitimate_numeric_name():
    result = merge_text_field("name", {"off": "7Up Zero", "opf": "7up zero"})
    assert result.value is not None
    assert result.confidence == Confidence.HIGH


def test_cosmetics_item():
    result = merge_text_field("brand", {"obf": "Balea"})
    assert result.value == "Balea"
    result_name = merge_text_field("name", {"obf": "Handcreme Ultra Sensitive"})
    assert result_name.value == "Handcreme Ultra Sensitive"


def test_household_cleaner_from_open_products_facts():
    result = merge_text_field("name", {"opf": "Allzweckreiniger Zitrone"})
    assert result.value == "Allzweckreiniger Zitrone"


def test_pet_food_from_open_pet_food_facts():
    result = merge_text_field("name", {"opff": "Adult Huhn & Reis 4kg"})
    assert result.value == "Adult Huhn & Reis 4kg"
    quantity = merge_quantity_field({"opff": "4 kg"})
    assert quantity.value == "4 kg"


def test_unusual_but_real_brand_name_survives_the_gate():
    """Guards against an over-aggressive plausibility gate (spec §6/§8) —
    a short, unusual, but real brand must not be rejected just for being
    unconventional."""
    result = merge_text_field("brand", {"off": "Ur-Krostitzer"})
    assert result.value == "Ur-Krostitzer"


def test_barcode_missing_from_every_source():
    result = merge_text_field("name", {"off": None, "obf": None, "opff": None, "opf": None})
    assert result.value is None
    assert result.suggested is None


def test_conflicting_sources_are_recorded_not_silently_resolved():
    result = merge_text_field("name", {"off": "Cola Zero", "opf": "Pepsi Max"})
    assert result.conflicting_sources != []
    assert result.value is not None  # still usable, just flagged


def test_equivalent_but_differently_spelled_quantities_count_as_consensus():
    result = merge_quantity_field({"off": "500 ml", "opf": "0,5 l"})
    assert result.confidence == Confidence.HIGH
    assert set(result.contributing_sources) == {"off", "opf"}


def test_multipack_quantity_is_never_collapsed_in_this_corpus_either():
    result = merge_quantity_field({"off": "6 x 1,5 l"})
    assert result.value == "6 x 1,5 l"


def test_image_url_field_priority_after_name_brand_quantity():
    """imageUrl is the fourth calibration priority (user instruction) —
    still goes through the same generic text-plausibility path since it's
    an image URL string, not a QuantityCandidate."""
    result = merge_text_field("url", {"off": "https://images.example.org/p.jpg"})
    assert result.value == "https://images.example.org/p.jpg"


def test_category_and_variant_are_lowest_calibration_priority_but_still_functional():
    category = merge_text_field("generic", {"off": "Milchprodukte"})
    variant = merge_text_field("generic", {"off": "Erdbeere"})
    assert category.value == "Milchprodukte"
    assert variant.value == "Erdbeere"
