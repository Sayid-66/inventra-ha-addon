from sqlalchemy import select
from sqlalchemy.orm import Session
from uuid6 import uuid7

from ..db.models import Unit
from ..errors import BusinessRuleViolation, DuplicateEntityError


def unit_reference(unit: Unit | None) -> dict | None:
    return {"id": unit.id, "name": unit.name, "abbreviation": unit.abbreviation} if unit else None


def unit_response(unit: Unit) -> dict:
    return {**unit_reference(unit), "isStandard": unit.is_standard, "createdAt": unit.created_at}


def require_unit(db: Session, unit_id: str | None) -> Unit | None:
    unit = db.get(Unit, unit_id) if unit_id is not None else None
    if unit_id is not None and unit is None:
        raise BusinessRuleViolation("UNKNOWN_UNIT", f"Unknown unitId: {unit_id}")
    return unit


def save_unit(db: Session, name: str, abbreviation: str, unit: Unit | None = None) -> Unit:
    # Python casefold covers Unicode as well as the SQLite NOCASE constraint.
    for other in db.scalars(select(Unit)):
        if other.abbreviation.casefold() == abbreviation.casefold() and (unit is None or other.id != unit.id):
            raise DuplicateEntityError("Unit", "abbreviation", abbreviation)
    if unit is None:
        unit = Unit(id=str(uuid7()), name=name, abbreviation=abbreviation, is_standard=False)
        db.add(unit)
    else:
        unit.name, unit.abbreviation = name, abbreviation
    db.flush()
    return unit
