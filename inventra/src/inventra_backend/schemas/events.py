from __future__ import annotations

from typing import Optional, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .validation import Barcode, ContentLabel, IsoDate, Name


class NewProductInfo(BaseModel):
    name: Name
    image_url: Optional[str] = Field(default=None, alias="imageUrl", max_length=1024)
    resolution_id: Optional[str] = Field(default=None, alias="resolutionId", max_length=36)
    brand: Optional[str] = Field(default=None, max_length=200)
    variant: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=200)

    model_config = {"populate_by_name": True}


class ProductUpdateInPurchase(BaseModel):
    name: Optional[Name] = None
    brand: Optional[str] = Field(default=None, max_length=200)
    variant: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=200)
    quantity: Optional[float] = Field(default=None, gt=0, le=100_000_000, allow_inf_nan=False)
    unit_id: Optional[str] = Field(default=None, alias="unitId", max_length=36)
    min_stock: Optional[int] = Field(default=None, alias="minStock", ge=0, le=1_000_000)

    model_config = {"populate_by_name": True}

    @field_validator("name")
    @classmethod
    def name_cannot_be_cleared(cls, value):
        if value is None:
            raise ValueError("name may not be null")
        return value


class PurchaseCreateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    product_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="productId")
    new_product: Optional[NewProductInfo] = Field(default=None, alias="newProduct")
    product_update: Optional[ProductUpdateInPurchase] = Field(default=None, alias="productUpdate")
    barcode: Barcode
    location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="locationId")
    quantity: int = Field(ge=1, le=1_000_000)
    store_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="storeId")
    price_per_unit_cents: Optional[int] = Field(default=None, alias="pricePerUnitCents", ge=0, le=100_000_000)
    mhd: Optional[IsoDate] = None
    min_stock: Optional[int] = Field(default=None, alias="minStock", ge=0, le=1_000_000)
    content_unit_label: Optional[ContentLabel] = Field(default=None, alias="contentUnitLabel")
    content_total: Optional[int] = Field(default=None, alias="contentTotal", ge=1, le=100_000_000)
    content_breakdown: Optional[str] = Field(default=None, alias="contentBreakdown", min_length=1, max_length=255, pattern=r"^[1-9][0-9]*(,[1-9][0-9]*)*$")
    timestamp: int = Field(ge=0)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_content(self):
        if (self.content_total is None) != (self.content_unit_label is None):
            raise ValueError("contentTotal and contentUnitLabel must be supplied together")
        if self.content_breakdown is not None and self.content_total is None:
            raise ValueError("contentBreakdown requires contentTotal and contentUnitLabel")
        return self


class ConsumptionCreateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    product_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="productId")
    location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="locationId")
    stock_kind: Literal["STK", "CONTENT"] = Field(alias="stockKind")
    quantity: int = Field(ge=1)
    timestamp: int = Field(ge=0)

    model_config = {"populate_by_name": True}


class CorrectionCreateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    product_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="productId")
    location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="locationId")
    stock_kind: Literal["STK", "CONTENT"] = Field(alias="stockKind")
    content_unit_label: Optional[ContentLabel] = Field(default=None, alias="contentUnitLabel")
    new_quantity: int = Field(alias="newQuantity", ge=0)
    mhd_for_increase: Optional[IsoDate] = Field(default=None, alias="mhdForIncrease")
    timestamp: int = Field(ge=0)

    model_config = {"populate_by_name": True}


class RelocationCreateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    product_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="productId")
    from_location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="fromLocationId")
    to_location_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="toLocationId")
    stock_kind: Literal["STK", "CONTENT"] = Field(alias="stockKind")
    quantity: int = Field(ge=1)
    timestamp: int = Field(ge=0)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def different_locations(self):
        if self.from_location_id == self.to_location_id:
            raise ValueError("fromLocationId and toLocationId must differ")
        return self
