from inventra_backend.resolver.plausibility import (
    is_plausible_text, is_plausible_brand, is_plausible_quantity, is_plausible_image_url,
)
from inventra_backend.resolver.quantity import parse_quantity


def test_plausible_ordinary_name():
    assert is_plausible_text("Go Nrgy Boost Berries Zero") is True


def test_rejects_ean_4260456731392_garbage_name():
    # The frozen regression case (spec §0): ingredient/nutrition-text garbage
    # must never pass, regardless of product identity.
    garbage = "Pantothensure ZERO GO NRGY by 9180 BOOST BERRIES G"
    ingredients = "Wasser, Kohlensaeure, Citronensaeure, Pantothensaeure, Taurin, Koffein"
    assert is_plausible_text(garbage, reference_texts=[ingredients]) is False


def test_rejects_percent_in_parens():
    # Unconditional, like the ported Android isPlausibleProductName() —
    # a percent-in-parens match is always ingredient-list-shaped, so
    # length/separator headroom never overrides it.
    assert is_plausible_text("Frucht (12,5%) Konzentrat aus Apfel und Birne extra lang") is False
    assert is_plausible_text("Zutat (12,5%)") is False


def test_rejects_url_or_email():
    assert is_plausible_text("Besuche www.example.com fuer mehr Infos") is False


def test_rejects_bare_number_without_unit():
    assert is_plausible_text("Artikel 123456789") is False


def test_accepts_unusual_but_real_name_with_legitimate_numbers():
    # Guards against over-aggressive rejection (spec §6/§8): a real product
    # name containing numbers must survive.
    assert is_plausible_text("7Up Zero") is True
    assert is_plausible_text("Kartoffel 5-Minuten-Terrine") is True


def test_rejects_too_long_text():
    assert is_plausible_text("x" * 81) is False


def test_plausible_brand_accepts_ordinary_brand():
    assert is_plausible_brand("Balea") is True


def test_plausible_brand_rejects_address_like_text():
    assert is_plausible_brand("Musterstrasse 1, 12345 Musterstadt") is False


def test_plausible_quantity_accepts_parsed_candidate():
    assert is_plausible_quantity(parse_quantity("500 ml")) is True


def test_plausible_quantity_rejects_none():
    assert is_plausible_quantity(None) is False


def test_plausible_image_url_accepts_https_url():
    assert is_plausible_image_url("https://images.example.org/product/123.jpg") is True


def test_plausible_image_url_rejects_non_url_text():
    assert is_plausible_image_url("kein-bild") is False
    assert is_plausible_image_url("") is False
