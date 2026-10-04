import pytest
from inventra_backend.resolver.product_naming import compose_product_name, split_brands


@pytest.mark.parametrize("name,brand_raw,expected", [
    ('Barilla Spaghetti n.5 500g', 'Barilla', 'Spaghetti n.5'),
    ('Müller Joghurt mild 3,5% Fett 150g', 'Müller', 'Joghurt mild 3,5% Fett'),
    ('Ja! Vollmilch 3,5% 1l', 'Ja!', 'Vollmilch 3,5%'),
    ('REWE Bio Haferflocken Kernig 500 g', 'REWE Bio', 'Haferflocken Kernig'),
    ('Hipp Bio Milchbrei 4x250g', 'Hipp', 'Bio Milchbrei'),
    ('Pampers Windeln Gr. 4 Maxi 46 Stück', 'Pampers', 'Windeln Gr. 4 Maxi'),
    ('Ferrero Nutella 450g', 'Ferrero', 'Nutella'),
    ('Dr. Oetker Ristorante Pizza Salami 320g', 'Dr. Oetker', 'Ristorante Pizza Salami'),
    ('Kerrygold Original Irische Butter 250g', 'Kerrygold', 'Original Irische Butter'),
    ('Coca Cola 1,5L', 'Coca-Cola', None),
    ('Gut&Günstig Spaghetti 500g', 'Gut&Günstig', 'Spaghetti'),
    ('Rittersport Alpenmilch 100 g', 'Ritter Sport', 'Alpenmilch'),
    ('Hähnchen-Geschnetzeltes 400g', 'Gut Ponholz', 'Hähnchen-Geschnetzeltes'),
    ('Wasser still 6 x 1,5 l', 'Gerolsteiner', 'Wasser still'),
    ('Pril Spülmittel 500ml', 'Pril', 'Spülmittel'),
    ('Tee 20 Beutel', 'Teekanne', 'Tee'),
    ('MILCH 1L', '', 'Milch'),
    ('Gouda 48% 200 g', 'Milbona', 'Gouda 48%'),
    ('Pringles Original 165g', 'Pringles', None),
    ('Iglo Fischstäbchen 30 Stück 750g', 'Iglo', 'Fischstäbchen 30 Stück 750g'),
    ('Dr Oetker Pizza 320g', 'Dr. Oetker', 'Pizza'),
    ('CocaCola 1l', 'Coca-Cola', None),
    ('Bio Eier', 'Other', 'Bio Eier'),
    ('Zero Light', 'Other', None),
])
def test_naming_probe(name, brand_raw, expected):
    assert compose_product_name(name, split_brands(brand_raw), None) == expected


def test_modifier_category_fallback():
    assert compose_product_name("Pringles Original 165g", ["Pringles"], "Kartoffelchips", True) == "Kartoffelchips"
