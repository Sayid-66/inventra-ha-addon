import pytest

from inventra_backend.services.unit_normalizer import normalize_quantity


@pytest.mark.parametrize("text,amount,unit,expected", [
    ("400 g", None, None, (400, "g")),
    ("1,5l", None, None, (1.5, "l")),
    (None, 400, "GRAMM", (400, "g")),
    (None, 2, "kilo", (2, "kg")),
    (None, 6, "pieces", (6, "Stk.")),
    ("6x125g", None, None, (750, "g")),
    ("6 × 0,5 l", None, None, (3, "l")),
    ("nonsense", None, None, (None, None)),
    ("12 flurbs", None, None, (12, None)),
    (None, 12, "flurbs", (12, None)),
    ("400 g", 1, "kg", (1, "kg")),
    (None, float("nan"), "g", (None, "g")),
    ("400 g", 2, "flurbs", (400, "g")),
    (None, 6, "Stück", (6, "Stk.")),
    (None, 3, "Stk.", (3, "Stk.")),
    (None, 3, "Packung", (3, "Pkg.")),
    (None, 10, "Tabletten", (10, "Tabl.")),
])
def test_normalize_quantity(text, amount, unit, expected):
    assert normalize_quantity(text, amount, unit) == expected
