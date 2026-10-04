from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class ResolveRequest(BaseModel):
    barcode: str


class ReResolveRequest(BaseModel):
    barcode: str


class FieldResultResponse(BaseModel):
    value: Optional[str] = None
    suggested: Optional[str] = None
    confidence: Optional[str] = None
    selected_source: Optional[str] = Field(default=None, alias="selectedSource")
    contributing_sources: list[str] = Field(default_factory=list, alias="contributingSources")
    conflicting_sources: list[str] = Field(default_factory=list, alias="conflictingSources")

    model_config = {"populate_by_name": True}


class ResolveResponse(BaseModel):
    resolution_id: Optional[str] = Field(default=None, alias="resolutionId")
    matched_locally: bool = Field(alias="matchedLocally")
    product: Optional[dict] = None
    fields: Optional[dict[str, FieldResultResponse]] = None
    raw_sources: Optional[dict[str, dict]] = Field(default=None, alias="rawSources")

    model_config = {"populate_by_name": True}


class ReResolveResponse(BaseModel):
    resolution_id: str = Field(alias="resolutionId")
    diff: dict[str, dict]

    model_config = {"populate_by_name": True}
