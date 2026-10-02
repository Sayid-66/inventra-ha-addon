#!/usr/bin/env python3
"""One-off, strictly ID-targeted Inventra test-product cleanup.

Usage (with the installed backend, or from a source checkout):
    python3 reset_test_products.py /data/inventra.db --dry-run
    python3 reset_test_products.py /data/inventra.db --confirm

Uses reset_production_data.py's transaction and ChangeSet pattern. No schema
creation/migration is performed. Confirm uses serialized BEGIN IMMEDIATE;
dry-run uses mode=ro, query_only and an explicit read transaction. Its projected
counts are a consistent snapshot, not a reservation against later writes.

Existing sync history is retained; three Product DELETE tombstones are appended
at one new revision. All three exact ID/name pairs must exist, including on a
repeat invocation. The only optional deletion is the named non-standard unit,
provided no products reference it after the product DELETE has executed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

source_dir = Path(__file__).resolve().parents[1] / "src"
if (source_dir / "inventra_backend").is_dir():
    sys.path.insert(0, str(source_dir))

from sqlalchemy import LargeBinary, cast, create_engine, delete, event, func, select
from sqlalchemy.orm import Session

from inventra_backend.db.base import Base, configure_engine
from inventra_backend.db import models as m
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services.product_service import _to_dict as product_snapshot


TARGET_PRODUCTS = (
    ("01a0e232-a65f-73c2-b14d-bf242a23e27a", "E2E_Testprodukt_Standard"),
    ("01a0e23d-5c43-75d6-89ce-3c584aad818d", "E2E_Testprodukt_Custom"),
    ("01a0e23e-f09c-72dc-9159-4b07dc67d3c9", "Coca-Cola"),
)
CANDIDATE_UNIT_ID = "01a0e23c-6868-7bbd-b54a-195fc8a12ad3"
CANDIDATE_UNIT_NAME = "Grosseimer"
TARGET_IDS = tuple(id_ for id_, _name in TARGET_PRODUCTS)
CLEAR_MODELS = (
    m.Product, m.Barcode, m.Batch, m.PurchaseEvent, m.ConsumptionEvent,
    m.CorrectionEvent, m.RelocationEvent, m.BringWatchState,
)
CONTROL_MODELS = (m.Location, m.Store, m.Device)
REPORT_MODELS = (*CLEAR_MODELS, m.Unit, m.ChangeLog, *CONTROL_MODELS)


def _counts(db):
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in REPORT_MODELS}


def _check_foreign_keys(db):
    violations = db.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise ValueError(f"foreign_key_check failed: {violations!r}")


def _target_filter(table):
    column = table.c.id if table is m.Product.__table__ else table.c.product_id
    return column.in_(TARGET_IDS)


def _stored_rows(db, table, predicate=None):
    """Compare every stored value's SQLite type and bytes, bypassing ORM caches.

    In particular, DateTime conversion must not hide changes in stored text.
    These in-memory snapshots are never printed (devices contain credentials).
    """
    columns = [expression for column in table.columns
               for expression in (func.typeof(column), cast(column, LargeBinary))]
    statement = select(*columns).order_by(*table.primary_key.columns)
    if predicate is not None:
        statement = statement.where(predicate)
    return db.execute(statement).all()


def _unit_decision(db, references):
    unit = db.execute(select(m.Unit.__table__).where(m.Unit.id == CANDIDATE_UNIT_ID)).mappings().one_or_none()
    if references:
        return False, f"kept: {references} product reference(s) remain"
    if unit is None:
        return False, "kept (no deletion): candidate row already absent"
    if unit["name"] != CANDIDATE_UNIT_NAME:
        return False, f"kept: live name {unit['name']!r} differs from {CANDIDATE_UNIT_NAME!r}"
    if unit["is_standard"]:
        return False, "kept: candidate is marked standard"
    return True, "deleted: zero product references; exact name matches; non-standard"


def reset_database(database: str | Path, *, dry_run: bool = False, confirm: bool = False) -> str:
    """Return a report only after a successful read transaction or write commit."""
    if dry_run == confirm:
        raise ValueError("Specify exactly one of --dry-run or --confirm; no changes made")
    path = Path(database).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a database file: {path}")
    uri = f"{path.as_uri()}?mode={'ro' if dry_run else 'rw'}&uri=true"
    engine = create_engine(f"sqlite:///{uri}") if dry_run else configure_engine(uri)

    @event.listens_for(engine, "connect")
    def _connection_options(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        if dry_run:
            connection.isolation_level = None
            connection.execute("PRAGMA query_only=ON")

    if dry_run:
        @event.listens_for(engine, "begin")
        def _read_transaction(connection):
            connection.exec_driver_sql("BEGIN")

    try:
        with Session(engine, autoflush=False) as db, db.begin():
            products = []
            for id_, expected_name in TARGET_PRODUCTS:
                product = db.get(m.Product, id_)
                if product is None or product.name != expected_name:
                    actual = "missing" if product is None else repr(product.name)
                    raise ValueError(f"Expected product {id_} named {expected_name!r}; found {actual}")
                products.append(product)
            counter = db.get(m.RevisionCounter, 0)
            maximum = db.scalar(select(func.max(m.ChangeLog.revision))) or 0
            if counter is None or counter.current_revision < maximum:
                raise ValueError(f"revision_counter missing or below change_log max revision {maximum}; refusing unsafe revision allocation")
            revision_before = counter.current_revision
            revision_after = revision_before + 1
            before = _counts(db)
            controls = {model.__table__: _stored_rows(db, model.__table__)
                        for model in CONTROL_MODELS}
            preserved = {model.__table__: _stored_rows(db, model.__table__, ~_target_filter(model.__table__))
                         for model in CLEAR_MODELS}
            units_before = _stored_rows(db, m.Unit.__table__)
            other_units = _stored_rows(db, m.Unit.__table__, m.Unit.id != CANDIDATE_UNIT_ID)
            standard_units = _stored_rows(db, m.Unit.__table__, m.Unit.is_standard.is_(True))
            tombstones = []
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            for row in products:
                snapshot = product_snapshot(row)
                snapshot.update(version=row.version + 1, deletedAt=now)
                tombstones.append(("Product", row.id, snapshot))

            after = dict(before)
            for model in CLEAR_MODELS:
                targeted = db.scalar(select(func.count()).select_from(model).where(_target_filter(model.__table__)))
                after[model.__tablename__] -= targeted
            # Do not purge unrelated or outstanding sync history.
            after["change_log"] += len(tombstones)
            projected_references = db.scalar(select(func.count()).select_from(m.Product).where(
                m.Product.unit_id == CANDIDATE_UNIT_ID, m.Product.id.not_in(TARGET_IDS)))
            delete_unit, unit_report = _unit_decision(db, projected_references)
            after["units"] -= int(delete_unit)

            if not dry_run:
                clear_tables = {model.__table__ for model in CLEAR_MODELS}
                # Derive child-before-parent order from the backend FK metadata.
                for table in reversed(Base.metadata.sorted_tables):
                    if table in clear_tables:
                        db.execute(delete(table).where(_target_filter(table)))
                # Recompute after the actual product DELETE, inside this transaction.
                references = db.scalar(select(func.count()).select_from(m.Product).where(
                    m.Product.unit_id == CANDIDATE_UNIT_ID))
                actual_delete_unit, unit_report = _unit_decision(db, references)
                if references != projected_references or actual_delete_unit != delete_unit:
                    raise ValueError("Post-delete unit state differs from reset plan")
                if actual_delete_unit:
                    db.execute(delete(m.Unit.__table__).where(
                        m.Unit.id == CANDIDATE_UNIT_ID,
                        m.Unit.name == CANDIDATE_UNIT_NAME,
                        m.Unit.is_standard.is_(False)))
                changes = ChangeSet(db)
                for entity_type, id_, snapshot in tombstones:
                    changes.record(entity_type, id_, m.ChangeKind.DELETE, snapshot)
                db.flush()
                if changes.revision <= maximum or changes.revision != revision_after:
                    raise ValueError("Reset revision did not advance monotonically")
                if db.scalar(select(m.RevisionCounter.current_revision).where(m.RevisionCounter.id == 0)) != revision_after:
                    raise ValueError("Actual revision_counter differs from reset plan")
                if _counts(db) != after:
                    raise ValueError("Actual row counts differ from reset plan")
                for model in CLEAR_MODELS:
                    if db.scalar(select(func.count()).select_from(model).where(_target_filter(model.__table__))):
                        raise ValueError(f"Target rows remain in {model.__tablename__}")

            for table, rows in controls.items():
                if _stored_rows(db, table) != rows:
                    raise ValueError(f"Preserved {table.name} rows changed")
            for table, rows in preserved.items():
                if _stored_rows(db, table, ~_target_filter(table)) != rows:
                    raise ValueError(f"Non-target {table.name} rows changed")
            if _stored_rows(db, m.Unit.__table__, m.Unit.id != CANDIDATE_UNIT_ID) != other_units:
                raise ValueError("Other units changed")
            if _stored_rows(db, m.Unit.__table__, m.Unit.is_standard.is_(True)) != standard_units:
                raise ValueError("Standard units changed")
            if (dry_run or not delete_unit) and _stored_rows(db, m.Unit.__table__) != units_before:
                raise ValueError("Preserved units changed")
            _check_foreign_keys(db)
            lines = ["DRY RUN (read-only; projected counts)" if dry_run else "RESET COMMITTED"]
            for table, count in before.items():
                added = len(tombstones) if table == "change_log" else 0
                kept = after[table] - added
                lines.append(f"{table}: {count} -> {after[table]} (deleted={count - kept}, kept={kept}, added={added})")
            lines.extend([
                f"revision_counter: {revision_before} -> {revision_after}; pre-reset max change_log revision: {maximum}",
                f"New DELETE tombstones: {len(tombstones)} at revision {revision_after}",
                "Products targeted: " + ", ".join(f"{id_} ({name})" for id_, name in TARGET_PRODUCTS),
                "Location: unchanged (all stored values verified).",
                "Store: unchanged (all stored values verified).",
                "Device: unchanged (all stored values verified).",
                f"All standard units unchanged: {len(standard_units)} (all stored values verified).",
                "All other units unchanged (all stored values verified).",
                f"{CANDIDATE_UNIT_NAME} ({CANDIDATE_UNIT_ID}): {unit_report}" + (" (projected)" if dry_run else ""),
                "All non-target products and their listed child rows unchanged (all stored values verified).",
                "Existing change_log retained; pairing_codes, processed_operations, resolver_source_cache, resolution_results, mhd_warning_ack_state untouched.",
                "foreign_key_check: 0 violations" + (" (current database; no writes performed)" if dry_run else ""),
            ])
        return "\n".join(lines)
    finally:
        engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", type=Path, help="Existing SQLite database path")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Read-only preview; never writes")
    mode.add_argument("--confirm", action="store_true", help="Commit the reset in one transaction")
    args = parser.parse_args(argv)
    try:
        report = reset_database(args.database, dry_run=args.dry_run, confirm=args.confirm)
    except Exception as exc:
        print(f"ABORT: {exc}. No reset changes committed (transaction rolled back if started).", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
