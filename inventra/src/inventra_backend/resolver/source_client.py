from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..services.unit_normalizer import normalize_quantity
from .name_normalizer import normalize_product_name, normalize_category


@dataclass(frozen=True)
class SourceCandidate:
    name: str | None
    brand: str | None
    quantity_text: str | None
    image_url: str | None
    category: str | None
    variant: str | None


@dataclass(frozen=True)
class SourceResult:
    source: str
    status: str  # "FOUND" | "NOT_FOUND" | "ERROR"
    candidate: SourceCandidate | None
    error: str | None = None


def _first_brand(brands_raw: str | None) -> str | None:
    if not brands_raw:
        return None
    first = brands_raw.split(",")[0].strip()
    return first or None


class OpenFactsClient:
    """Shared HTTP client + response normalizer for any Product-Opener-based
    Open-Facts service (OFF/OBF/OPFF/OPF share one JSON shape). No automatic
    retries (spec §4.1) — one fetch is one attempt."""

    def __init__(
        self, source_id: str, base_url: str, timeout_seconds: float, user_agent: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self._source_id = source_id
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._user_agent = user_agent
        self._transport_override = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport_override)

    async def fetch(self, barcode: str) -> SourceResult:
        url = f"{self._base_url}/api/v2/product/{barcode}.json"
        try:
            async with self._client() as client:
                response = await client.get(url, headers={"User-Agent": self._user_agent})
        except httpx.TimeoutException as exc:
            return SourceResult(source=self._source_id, status="ERROR", candidate=None, error=str(exc))
        except httpx.HTTPError as exc:
            return SourceResult(source=self._source_id, status="ERROR", candidate=None, error=str(exc))

        if response.status_code == 404:
            return SourceResult(source=self._source_id, status="NOT_FOUND", candidate=None)
        if response.status_code == 429 or response.status_code >= 500:
            return SourceResult(
                source=self._source_id, status="ERROR", candidate=None,
                error=f"http_{response.status_code}",
            )
        if response.status_code != 200:
            return SourceResult(
                source=self._source_id, status="ERROR", candidate=None,
                error=f"http_{response.status_code}",
            )

        body = response.json()
        product = body.get("product")
        if body.get("status") != 1 or not product:
            return SourceResult(source=self._source_id, status="NOT_FOUND", candidate=None)

        amount, abbreviation = normalize_quantity(
            amount=product.get("product_quantity"), unit_string=product.get("product_quantity_unit"),
        )
        # Structured OFF package totals take precedence when usable. Keep the
        # original text as the fallback, including its multipack information.
        quantity_text = (f"{amount:g} {abbreviation}" if amount is not None and abbreviation
                         else product.get("quantity"))
        candidate = SourceCandidate(
            name=normalize_product_name(product.get("product_name_de")) or normalize_product_name(product.get("product_name")),
            brand=_first_brand(product.get("brands")),
            quantity_text=quantity_text,
            image_url=product.get("image_url"),
            category=normalize_category((product.get("categories") or "").split(",")[0]),
            variant=None,
        )
        return SourceResult(source=self._source_id, status="FOUND", candidate=candidate)
