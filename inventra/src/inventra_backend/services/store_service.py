from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .location_service import normalize_name
from ..db.models import ChangeKind, PurchaseEvent, Store
from ..errors import BusinessRuleViolation, DuplicateEntityError, StaleVersionError
from ..idempotency.operations import run_idempotent
from ..revision.change_log import ChangeSet


def _to_dict(store: Store) -> dict:
    return {
        "id": store.id,
        "name": store.name,
        "version": store.version,
        "deletedAt": store.deleted_at.isoformat() if store.deleted_at else None,
    }


def create_store(db: Session, cs: ChangeSet, operation_id: str, id: str, name: str) -> dict:
    def perform() -> dict:
        normalized = normalize_name(name)
        if db.execute(select(Store).where(Store.normalized_name == normalized, Store.deleted_at.is_(None))).scalar_one_or_none():
            raise DuplicateEntityError("Store", "normalizedName", normalized)
        store = Store(id=id, name=name, normalized_name=normalized, version=1)
        db.add(store)
        db.flush()
        result = _to_dict(store)
        cs.record("Store", id, ChangeKind.CREATE, result)
        return result

    return run_idempotent(db, operation_id, {"op": "create_store", "id": id, "name": name}, perform)


def update_store(db: Session, cs: ChangeSet, operation_id: str, id: str, name: str, version: int) -> dict:
    def perform() -> dict:
        store = db.get(Store, id)
        if store is None or store.deleted_at is not None:
            raise DuplicateEntityError("Store", "id", id)
        if store.version != version:
            raise StaleVersionError("Store", id, _to_dict(store))
        normalized = normalize_name(name)
        if db.execute(
            select(Store).where(Store.normalized_name == normalized, Store.id != id, Store.deleted_at.is_(None))
        ).scalar_one_or_none():
            raise DuplicateEntityError("Store", "normalizedName", normalized)
        store.name = name
        store.normalized_name = normalized
        store.version += 1
        db.flush()
        result = _to_dict(store)
        cs.record("Store", id, ChangeKind.UPDATE, result)
        return result

    return run_idempotent(db, operation_id, {"op": "update_store", "id": id, "name": name, "version": version}, perform)


def soft_delete_store(db: Session, cs: ChangeSet, operation_id: str, id: str, version: int) -> dict:
    def perform() -> dict:
        store = db.get(Store, id)
        if store is None or store.deleted_at is not None:
            raise DuplicateEntityError("Store", "id", id)
        if store.version != version:
            raise StaleVersionError("Store", id, _to_dict(store))
        if db.execute(select(PurchaseEvent.id).where(PurchaseEvent.store_id == id).limit(1)).first() is not None:
            raise BusinessRuleViolation("STORE_IN_USE", "This store is referenced by purchases and cannot be deleted. You can rename it.")

        store.version += 1
        store.deleted_at = datetime.utcnow()
        store.normalized_name = f"~deleted~{id}~{store.normalized_name[:150]}"
        db.flush()
        result = _to_dict(store)
        cs.record("Store", id, ChangeKind.DELETE, result)
        return result

    return run_idempotent(db, operation_id, {"op": "delete_store", "id": id, "version": version}, perform)


def list_stores(db: Session) -> list[dict]:
    rows = db.execute(select(Store).where(Store.deleted_at.is_(None))).scalars().all()
    return [_to_dict(r) for r in rows]
