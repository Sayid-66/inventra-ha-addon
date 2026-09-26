from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.device_token import require_device
from ..db.models import Device
from ..resolver import resolver_service
from ..schemas.resolver import (
    ResolveRequest, ReResolveRequest, ResolveResponse, ReResolveResponse, FieldResultResponse,
)

router = APIRouter(prefix="/products", tags=["product-resolver"])


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_route(body: ResolveRequest, device: Device = Depends(require_device)) -> ResolveResponse:
    result = await resolver_service.resolve(body.barcode)
    if result.matched_locally:
        return ResolveResponse(resolutionId=None, matchedLocally=True, product=result.product, fields=None)
    return ResolveResponse(
        resolutionId=result.resolution_id, matchedLocally=False, product=None,
        fields={
            name: FieldResultResponse(
                value=r.value, suggested=r.suggested,
                confidence=r.confidence.value if r.confidence else None,
                selectedSource=r.selected_source, contributingSources=r.contributing_sources,
                conflictingSources=r.conflicting_sources,
            )
            for name, r in result.fields.items()
        },
    )


@router.post("/{product_id}/re-resolve", response_model=ReResolveResponse)
async def re_resolve_route(
    product_id: str, body: ReResolveRequest, device: Device = Depends(require_device),
) -> ReResolveResponse:
    result = await resolver_service.re_resolve(product_id, body.barcode)
    if result is None:
        raise HTTPException(status_code=404, detail="product not found or barcode not owned by it")
    return ReResolveResponse(resolutionId=result.resolution_id, diff=result.diff)
