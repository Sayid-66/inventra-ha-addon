from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator


UnitName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
UnitAbbreviation = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]


class UnitReference(BaseModel):
    id: str
    name: str
    abbreviation: str


class UnitResponse(UnitReference):
    is_standard: bool = Field(alias="isStandard")
    created_at: datetime = Field(alias="createdAt")


class UnitCreateRequest(BaseModel):
    name: UnitName
    abbreviation: UnitAbbreviation


class UnitUpdateRequest(BaseModel):
    name: UnitName | None = None
    abbreviation: UnitAbbreviation | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Supply a non-null name and/or abbreviation")
        return self
