from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .units import UnitReference


class ProductCreateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    name: str
    image_url: Optional[str] = Field(default=None, alias="imageUrl")
    min_stock: Optional[int] = Field(default=None, alias="minStock")
    content_unit_label: Optional[str] = Field(default=None, alias="contentUnitLabel")
    resolution_id: Optional[str] = Field(default=None, alias="resolutionId")
    brand: Optional[str] = None
    quantity: Optional[float] = Field(default=None, allow_inf_nan=False)
    unit_id: Optional[str] = Field(default=None, alias="unitId")
    quantity_text: Optional[str] = Field(default=None, alias="quantityText")
    product_quantity: Optional[float] = Field(default=None, alias="productQuantity")
    product_quantity_unit: Optional[str] = Field(default=None, alias="productQuantityUnit")
    category: Optional[str] = None
    variant: Optional[str] = None

    model_config = {"populate_by_name": True}


class ProductUpdateRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    name: str
    image_url: Optional[str] = Field(default=None, alias="imageUrl")
    min_stock: Optional[int] = Field(default=None, alias="minStock")
    content_unit_label: Optional[str] = Field(default=None, alias="contentUnitLabel")
    version: int
    resolution_id: Optional[str] = Field(default=None, alias="resolutionId")
    brand: Optional[str] = None
    quantity: Optional[float] = Field(default=None, allow_inf_nan=False)
    unit_id: Optional[str] = Field(default=None, alias="unitId")
    quantity_text: Optional[str] = Field(default=None, alias="quantityText")
    product_quantity: Optional[float] = Field(default=None, alias="productQuantity")
    product_quantity_unit: Optional[str] = Field(default=None, alias="productQuantityUnit")
    category: Optional[str] = None
    variant: Optional[str] = None

    model_config = {"populate_by_name": True}


class ProductDeleteRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    version: int

    model_config = {"populate_by_name": True}


class ProductResponse(BaseModel):
    id: str
    name: str
    image_url: Optional[str] = Field(default=None, alias="imageUrl")
    min_stock: Optional[int] = Field(default=None, alias="minStock")
    content_unit_label: Optional[str] = Field(default=None, alias="contentUnitLabel")
    version: int
    deleted_at: Optional[str] = Field(default=None, alias="deletedAt")
    brand: Optional[str] = None
    quantity: Optional[float] = Field(default=None, allow_inf_nan=False)
    unit: Optional[UnitReference] = None
    category: Optional[str] = None
    variant: Optional[str] = None
    field_provenance: dict = Field(default_factory=dict, alias="fieldProvenance")

    model_config = {"populate_by_name": True}
