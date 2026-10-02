from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .validation import Barcode


class BarcodeAssignRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    code: Barcode
    product_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="productId")

    model_config = {"populate_by_name": True}


class BarcodeDeleteRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    version: int

    model_config = {"populate_by_name": True}


class BarcodeResponse(BaseModel):
    code: str
    product_id: str = Field(alias="productId")
    version: int
    deleted_at: Optional[str] = Field(default=None, alias="deletedAt")

    model_config = {"populate_by_name": True}
