from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class NewProductInfo(BaseModel):
    name: str
    image_url: Optional[str] = Field(default=None, alias="imageUrl")
    resolution_id: Optional[str] = Field(default=None, alias="resolutionId")

    model_config = {"populate_by_name": True}


class PurchaseCreateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    product_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="productId")
    new_product: Optional[NewProductInfo] = Field(default=None, alias="newProduct")
    barcode: str
    location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="locationId")
    quantity: int
    store_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="storeId")
    price_per_unit_cents: Optional[int] = Field(default=None, alias="pricePerUnitCents")
    mhd: Optional[str] = None
    min_stock: Optional[int] = Field(default=None, alias="minStock")
    content_unit_label: Optional[str] = Field(default=None, alias="contentUnitLabel")
    content_total: Optional[int] = Field(default=None, alias="contentTotal")
    content_breakdown: Optional[str] = Field(default=None, alias="contentBreakdown")
    timestamp: int

    model_config = {"populate_by_name": True}


class ConsumptionCreateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    product_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="productId")
    location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="locationId")
    stock_kind: str = Field(alias="stockKind")
    quantity: int
    timestamp: int

    model_config = {"populate_by_name": True}


class CorrectionCreateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    product_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="productId")
    location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="locationId")
    stock_kind: str = Field(alias="stockKind")
    content_unit_label: Optional[str] = Field(default=None, alias="contentUnitLabel")
    new_quantity: int = Field(alias="newQuantity")
    mhd_for_increase: Optional[str] = Field(default=None, alias="mhdForIncrease")
    timestamp: int

    model_config = {"populate_by_name": True}


class RelocationCreateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    product_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="productId")
    from_location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="fromLocationId")
    to_location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="toLocationId")
    stock_kind: str = Field(alias="stockKind")
    quantity: int
    timestamp: int

    model_config = {"populate_by_name": True}
