"""Shared constraints for write requests; dates stay strings on the wire."""
from datetime import date
import re
from typing import Annotated

from pydantic import AfterValidator, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Barcode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
ContentLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


def validate_iso_date(value: str) -> str:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("Expected an ISO date YYYY-MM-DD")
    date.fromisoformat(value)
    return value


IsoDate = Annotated[str, AfterValidator(validate_iso_date)]
