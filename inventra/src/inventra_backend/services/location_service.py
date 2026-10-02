from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db.models import Batch, ChangeKind, ConsumptionEvent, CorrectionEvent, Location, PurchaseEvent, RelocationEvent
from ..errors import BusinessRuleViolation, DuplicateEntityError, StaleVersionError
from ..idempotency.operations import run_idempotent
from ..revision.change_log import ChangeSet


def normalize_name(raw: str) -> str:
    return raw.strip().casefold()


def _to_dict(location: Location) -> dict:
    return {
        "id": location.id,
        "name": location.name,
        "version": location.version,
        "deletedAt": location.deleted_at.isoformat() if location.deleted_at else None,
    }


def create_location(
    db: Session, cs: ChangeSet, operation_id: str, id: str, name: str,
) -> dict:
    def perform() -> dict:
        normalized = normalize_name(name)
        existing = db.execute(
            select(Location).where(Location.normalized_name == normalized, Location.deleted_at.is_(None))
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicateEntityError("Location", "normalizedName", normalized)

        # The existing global unique constraint also reserves tombstone names.
        if db.execute(select(Location.id).where(
            Location.normalized_name == normalized, Location.deleted_at.is_not(None)
        ).limit(1)).first() is not None:
            raise DuplicateEntityError("Location", "normalizedName (reserved by deleted row)", normalized)
        location = Location(id=id, name=name, normalized_name=normalized, version=1)
        db.add(location)
        db.flush()
        result = _to_dict(location)
        cs.record("Location", id, ChangeKind.CREATE, result)
        return result

    return run_idempotent(db, operation_id, {"op": "create_location", "id": id, "name": name}, perform)


def update_location(
    db: Session, cs: ChangeSet, operation_id: str, id: str, name: str, version: int,
) -> dict:
    def perform() -> dict:
        location = db.get(Location, id)
        if location is None or location.deleted_at is not None:
            raise DuplicateEntityError("Location", "id", id)  # not found is out of scope here; kept simple
        if location.version != version:
            raise StaleVersionError("Location", id, _to_dict(location))

        normalized = normalize_name(name)
        clash = db.execute(
            select(Location).where(Location.normalized_name == normalized, Location.id != id, Location.deleted_at.is_(None))
        ).scalar_one_or_none()
        if clash is not None:
            raise DuplicateEntityError("Location", "normalizedName", normalized)

        # Renames are subject to the same global constraint as creates.
        if db.execute(select(Location.id).where(
            Location.normalized_name == normalized, Location.deleted_at.is_not(None)
        ).limit(1)).first() is not None:
            raise DuplicateEntityError("Location", "normalizedName (reserved by deleted row)", normalized)
        location.name = name
        location.normalized_name = normalized
        location.version += 1
        db.flush()
        result = _to_dict(location)
        cs.record("Location", id, ChangeKind.UPDATE, result)
        return result

    return run_idempotent(
        db, operation_id, {"op": "update_location", "id": id, "name": name, "version": version}, perform
    )


def soft_delete_location(db: Session, cs: ChangeSet, operation_id: str, id: str, version: int) -> dict:
    def perform() -> dict:
        from datetime import datetime

        location = db.get(Location, id)
        if location is None:
            raise DuplicateEntityError("Location", "id", id)
        if location.version != version:
            raise StaleVersionError("Location", id, _to_dict(location))

        references = (
            select(Batch.id).where(Batch.location_id == id),
            select(PurchaseEvent.id).where(PurchaseEvent.location_id == id),
            select(ConsumptionEvent.id).where(ConsumptionEvent.location_id == id),
            select(CorrectionEvent.id).where(CorrectionEvent.location_id == id),
            select(RelocationEvent.id).where(or_(
                RelocationEvent.from_location_id == id, RelocationEvent.to_location_id == id
            )),
        )
        if any(db.execute(query.limit(1)).first() is not None for query in references):
            raise BusinessRuleViolation("LOCATION_IN_USE", "This location is referenced by stock or history and cannot be deleted. You can rename it.")

        location.version += 1
        location.deleted_at = datetime.utcnow()
        db.flush()
        result = _to_dict(location)
        cs.record("Location", id, ChangeKind.DELETE, result)
        return result

    return run_idempotent(db, operation_id, {"op": "delete_location", "id": id, "version": version}, perform)


def list_locations(db: Session) -> list[dict]:
    rows = db.execute(select(Location).where(Location.deleted_at.is_(None))).scalars().all()
    return [_to_dict(r) for r in rows]
