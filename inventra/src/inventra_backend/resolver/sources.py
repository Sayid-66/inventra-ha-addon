from __future__ import annotations

from dataclasses import dataclass

import httpx

from .source_client import OpenFactsClient


@dataclass(frozen=True)
class OpenFactsSourceConfig:
    source_id: str
    base_url: str


OPEN_FOOD_FACTS = OpenFactsSourceConfig("off", "https://world.openfoodfacts.org")
OPEN_BEAUTY_FACTS = OpenFactsSourceConfig("obf", "https://world.openbeautyfacts.org")
OPEN_PET_FOOD_FACTS = OpenFactsSourceConfig("opff", "https://world.openpetfoodfacts.org")
OPEN_PRODUCTS_FACTS = OpenFactsSourceConfig("opf", "https://world.openproductsfacts.org")

ALL_SOURCES: list[OpenFactsSourceConfig] = [
    OPEN_FOOD_FACTS, OPEN_BEAUTY_FACTS, OPEN_PET_FOOD_FACTS, OPEN_PRODUCTS_FACTS,
]


def build_client(
    config: OpenFactsSourceConfig, timeout_seconds: float, user_agent: str,
    transport: httpx.BaseTransport | None = None,
) -> OpenFactsClient:
    return OpenFactsClient(
        source_id=config.source_id, base_url=config.base_url,
        timeout_seconds=timeout_seconds, user_agent=user_agent, transport=transport,
    )
