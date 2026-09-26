from inventra_backend.resolver.sources import (
    ALL_SOURCES, OPEN_FOOD_FACTS, OPEN_BEAUTY_FACTS, OPEN_PET_FOOD_FACTS, OPEN_PRODUCTS_FACTS,
    build_client,
)


def test_all_four_sources_present_with_distinct_ids_and_domains():
    assert {c.source_id for c in ALL_SOURCES} == {"off", "obf", "opff", "opf"}
    assert len(ALL_SOURCES) == 4
    assert OPEN_FOOD_FACTS.base_url == "https://world.openfoodfacts.org"
    assert OPEN_BEAUTY_FACTS.base_url == "https://world.openbeautyfacts.org"
    assert OPEN_PET_FOOD_FACTS.base_url == "https://world.openpetfoodfacts.org"
    assert OPEN_PRODUCTS_FACTS.base_url == "https://world.openproductsfacts.org"


def test_build_client_wires_config_into_a_working_client():
    client = build_client(OPEN_FOOD_FACTS, timeout_seconds=3.0, user_agent="Inventra/test")
    assert client._source_id == "off"
    assert client._base_url == "https://world.openfoodfacts.org"
