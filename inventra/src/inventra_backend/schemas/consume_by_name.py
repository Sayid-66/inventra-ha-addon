from __future__ import annotations

from pydantic import BaseModel, Field

from .validation import Name


class ConsumeByNameRequest(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", alias="operationId")
    product_name: Name = Field(alias="productName", min_length=1)
    quantity: int = Field(gt=0, alias="quantity")

    model_config = {"populate_by_name": True}
