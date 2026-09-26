#!/usr/bin/env python3
"""One-off Inventra reset, using the installed backend's SQLAlchemy models.

Usage (in a backend Python environment, or copy this script into the container):
    python3 reset_production_data.py /data/inventra.db --dry-run
    python3 reset_production_data.py /data/inventra.db --confirm

No schema creation/migration is performed. Live writes use db.base's serialized
BEGIN IMMEDIATE transaction and ChangeSet's normal shared revision allocation.
Dry runs instead use a SQLite mode=ro, query_only connection and explicit read
transaction: configure_engine's BEGIN IMMEDIATE requires write access. Counts
are a consistent snapshot, not a reservation against subsequent backend writes.

Snapshots reuse the product/location service serializers, including all fields,
incremented version and ISO deletedAt, as in their soft-delete paths. The current
product service/backfill emits UPDATE to retain history; this *physical reset*
deliberately emits DELETE so paired clients cascade-delete product children.

Only the supplied device prefix is known: require one unique matching row with
the expected name, capture its full primary key, then exclude that exact key.
No reset marker or schema additions: when all deletion targets are already gone,
leave change_log and revision_counter unchanged. This is essential for retries
before paired clients have consumed the first reset's tombstones. A later reset
after newly created data is a new operation, purging the then-current log.

The container image does not bundle scripts/; copy this file into the running
container before invoking it. It imports the backend already installed there.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

# Also support invocation directly from a source checkout, from any working dir.
source_dir = Path(__file__).resolve().parents[1] / "src"
if (source_dir / "inventra_backend").is_dir():
    sys.path.insert(0, str(source_dir))

from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.orm import Session

from inventra_backend.db.base import Base, configure_engine
from inventra_backend.db import models as m
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services.location_service import _to_dict as location_snapshot
from inventra_backend.services.product_service import _to_dict as product_snapshot


DEVICE_PREFIX = "c0dcea-1ea5-7a20-936d-a0f6ed9eb35d"
DEVICE_NAME = "TestPhone-PostFix"
LOCATION_IDS = (
    "01a07313-9cc6-7337-8feb-ca869b408ce3",
    "01a07107-8803-7073-9ced-53e159c1ed73",
)
CLEAR_MODELS = (
    m.Product, m.Barcode, m.Batch, m.PurchaseEvent, m.ConsumptionEvent,
    m.CorrectionEvent, m.RelocationEvent, m.MhdWarningAckState,
    m.ResolverSourceCache, m.ResolutionResult, m.ProcessedOperation, m.BringWatchState,
)
REPORT_MODELS = (*CLEAR_MODELS, m.Location, m.Device, m.Store, m.ChangeLog)


def _counts(db):
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in REPORT_MODELS}


def _check_foreign_keys(db):
    violations = db.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise ValueError(f"foreign_key_check failed: {violations!r}")


def reset_database(database: str | Path, *, dry_run: bool = False, confirm: bool = False) -> str:
    """Return a report only after a successful read transaction or write commit.

    Raises on every safety failure; neither mode flag is destructive authority.
    Exactly one of dry_run/confirm is required, also for direct Python callers.
    """
    if dry_run == confirm:
        raise ValueError("Specify exactly one of --dry-run or --confirm; no changes made")
    path = Path(database).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a database file: {path}")
    # URI rw also prevents SQLite silently creating a file if it disappears.
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
            candidates = db.scalars(select(m.Device).where(m.Device.device_id.startswith(DEVICE_PREFIX))).all()
            if len(candidates) != 1 or candidates[0].device_name != DEVICE_NAME:
                raise ValueError(f"Expected exactly one device starting {DEVICE_PREFIX} named {DEVICE_NAME}; found {len(candidates)} candidate(s)")
            device = candidates[0]
            keep_device_id = device.device_id
            if device.default_location_id in LOCATION_IDS:
                raise ValueError("Preserved device references a location scheduled for deletion")
            counter = db.get(m.RevisionCounter, 0)
            maximum = db.scalar(select(func.max(m.ChangeLog.revision))) or 0
            if counter is None or counter.current_revision < maximum:
                raise ValueError(f"revision_counter missing or below change_log max revision {maximum}; refusing unsafe revision allocation")
            revision_before = counter.current_revision
            before = _counts(db)
            products = db.scalars(select(m.Product).order_by(m.Product.id)).all()
            locations = db.scalars(select(m.Location).where(m.Location.id.in_(LOCATION_IDS)).order_by(m.Location.id)).all()
            kept_locations = [(r.id, r.name) for r in db.scalars(select(m.Location).where(m.Location.id.not_in(LOCATION_IDS)).order_by(m.Location.id))]
            stores = [(r.id, r.name) for r in db.scalars(select(m.Store).order_by(m.Store.name, m.Store.id))]
            tombstones = []
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            for entity_type, rows, serialize in (("Product", products, product_snapshot), ("Location", locations, location_snapshot)):
                for row in rows:
                    snapshot = serialize(row)
                    snapshot.update(version=row.version + 1, deletedAt=now)
                    tombstones.append((entity_type, row.id, snapshot))
            needed = any(before[model.__tablename__] for model in CLEAR_MODELS) or bool(locations) or before["devices"] > 1
            after = dict(before)
            if needed:
                after.update({model.__tablename__: 0 for model in CLEAR_MODELS})
                after.update(locations=before["locations"] - len(locations), devices=1, change_log=len(tombstones))
                # Never erase outstanding sync data without replacement deletions.
                if before["change_log"] and not tombstones:
                    after["change_log"] = before["change_log"]
            revision_after = revision_before + bool(tombstones)
            if not dry_run and needed:
                clear_tables = {model.__table__ for model in CLEAR_MODELS}
                # Derive child-before-parent order from the backend FK metadata.
                for table in reversed(Base.metadata.sorted_tables):
                    if table in clear_tables:
                        db.execute(delete(table))
                db.execute(delete(m.Device).where(m.Device.device_id != keep_device_id))
                db.execute(delete(m.Location).where(m.Location.id.in_(LOCATION_IDS)))
                if tombstones:
                    db.execute(delete(m.ChangeLog))
                    changes = ChangeSet(db)
                    for entity_type, id_, snapshot in tombstones:
                        changes.record(entity_type, id_, m.ChangeKind.DELETE, snapshot)
                    db.flush()
                    if changes.revision <= maximum or changes.revision != revision_after:
                        raise ValueError("Reset revision did not advance monotonically")
                if db.scalar(select(m.Device.device_id).where(m.Device.device_id == keep_device_id)) != keep_device_id:
                    raise ValueError("Preserved device disappeared during reset")
                if _counts(db) != after:
                    raise ValueError("Actual row counts differ from reset plan")
            # This executes inside the transaction, after flushing all new rows.
            _check_foreign_keys(db)
            lines = ["DRY RUN (read-only; projected counts)" if dry_run else "RESET COMMITTED"]
            for table, count in before.items():
                added = len(tombstones) if table == "change_log" and needed else 0
                kept = after[table] - added
                lines.append(f"{table}: {count} -> {after[table]} (deleted={count - kept}, kept={kept}, added={added})")
            lines.extend([
                f"revision_counter: {revision_before} -> {revision_after}; pre-reset max change_log revision: {maximum}",
                f"New DELETE tombstones: {len(tombstones)}" + (f" at revision {revision_after}" if tombstones else ""),
                "Locations targeted: " + ", ".join(LOCATION_IDS),
                "Locations preserved: " + ", ".join(f"{id_} ({name})" for id_, name in kept_locations),
                f"Device preserved: {keep_device_id} ({device.device_name})",
                "Stores preserved: " + ", ".join(f"{name} ({id_})" for id_, name in stores),
                "All other tables unchanged (including pairing_codes).",
                "foreign_key_check: 0 violations" + (" (current database; no writes performed)" if dry_run else ""),
            ])
            if not needed:
                lines.append("Already reset: retained change_log tombstones and revision_counter unchanged.")
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
