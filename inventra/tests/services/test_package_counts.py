from types import SimpleNamespace

import pytest

from inventra_backend.services.stock_query_service import stock_packs
from inventra_backend.services.unit_normalizer import quantity_diff


@pytest.mark.parametrize("amount,unit,proposed,display,changed", [
    (500.0, "g", "500 g", "500 g", False),
    (500.0, "Gramm", "500 g", "500 Gramm", False),
    (500.0, "g", "0,5 kg", "500 g", True),
    (750.0, "g", "6x125g", "750 g", False),
    (500.0, "g", "abc", "500 g", True),
    (None, None, "500 g", None, True),
    (1.5, "l", "1,5 l", "1.5 l", False),
    (500.0, None, "500", "500", False),
    (500.0, "g", "500", "500 g", True),
    (500.0, None, "500 g", "500", True),
    (500.0, "g", None, "500 g", False),
    (500.0000001, "g", "500 g", "500 g", False),
])
def test_quantity_diff(amount, unit, proposed, display, changed):
    assert quantity_diff(amount, unit, proposed) == (display, changed)


@pytest.mark.parametrize("pieces,content,amount,label,unit,expected", [
    (3, None, 500, "ml", "ml", 3),
    (0, 1000, 500, "ml", "ml", 2),
    (0, 1001, 500, "ml", "ml", 3),
    (0, 1000, 500, "Milliliter", "ml", 2),
    (0, 1000, 500, "g", "ml", 1),
    (2, 1000, 500, "ml", "ml", 4),
    (0, None, None, None, None, 0),
    (0, 0, 500, "ml", "ml", 0),
    (0, 1000, None, "ml", "ml", 1),
    (0, 1000, 0, "ml", "ml", 1),
    (0, 1000, -500, "ml", "ml", 1),
    (0, 1000, 500, None, None, 1),
    (0, 1000, 500, "unknown", "unknown", 1),
])
def test_stock_packs(pieces, content, amount, label, unit, expected):
    product = SimpleNamespace(
        quantity=amount, content_unit_label=label,
        unit=SimpleNamespace(abbreviation=unit) if unit else None,
    )
    assert stock_packs(product, {"totalStk": pieces, "totalContent": content}) == expected
