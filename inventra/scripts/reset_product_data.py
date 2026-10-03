#!/usr/bin/env python3
"""Backed-up productive fresh start; run in the installed backend environment.

Stop backend activity for this maintenance operation. A BEGIN IMMEDIATE lock
protects the plan through backup and Bring cleanup. External Bring removals
cannot be rolled back if a later removal or database commit fails; the database
and image backup remain available. No schema creation or migration is performed.

Missing products get tombstones only when their latest Product change_log row
(ordered by revision, then id) is not DELETE. Existing Product DELETE rows are
retained; only non-DELETE Product rows and other product entity types are purged
when new tombstones are required. Repeated resets therefore do not re-tombstone
missing products, and phones behind earlier DELETEs can still receive them.

OPERATOR NOTES
Every phone must have an empty outbox and one finished sync, then be closed/offline
until done. Retried newProduct/create operations would recreate products after
processed_operations were cleared.
The DB write lock is held across backup, image copy and Bring HTTP calls (typically
1-5 s, worst about 10 s per HTTP call); backend requests wait up to busy_timeout 15 s.
Restore: stop the add-on, copy the database backup over /data/inventra.db, remove
/data/inventra.db-journal, -wal and -shm, restore product_images from the backup
directory, then start the add-on. Removed Bring items are NOT restored.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys

source_dir = Path(__file__).resolve().parents[1] / "src"
if (source_dir / "inventra_backend").is_dir():
    sys.path.insert(0, str(source_dir))

from sqlalchemy import and_, create_engine, delete, event, func, select
from sqlalchemy.orm import Session

from inventra_backend.db.base import Base, configure_engine
from inventra_backend.db import models as m
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services.bring_ha_client import BringHaClient
from inventra_backend.services.bring_service import _normalize
from inventra_backend.services.product_service import _to_dict as product_snapshot

CLEAR_MODELS = (
    m.BringWatchState, m.Batch, m.PurchaseEvent, m.ConsumptionEvent,
    m.CorrectionEvent, m.RelocationEvent, m.Barcode, m.Product,
    m.ResolverSourceCache, m.ResolutionResult, m.MhdWarningAckState,
)
KEEP_MODELS = (m.Location, m.Store, m.Unit, m.Device, m.PairingCode,
               m.InstanceMeta, m.ProcessedOperation)
REPORT_MODELS = (*CLEAR_MODELS, *KEEP_MODELS, m.ChangeLog)
PRODUCT_ENTITY_TYPES = (
    "Product", "Barcode", "Batch", "PurchaseEvent", "ConsumptionEvent",
    "CorrectionEvent", "RelocationEvent", "MhdWarningAckState",
)
BACKUP_TABLES = ("products", "barcodes", "purchase_events", "devices", "locations")
IMAGE_PREFIX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                          r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=$|[^0-9a-fA-F])")


def _counts(db):
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in REPORT_MODELS}


def _check_foreign_keys(db):
    violations = db.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise ValueError(f"foreign_key_check failed: {violations!r}")


def _backup(path, directory, before):
    directory = directory.resolve()
    images = path.parent / "product_images"
    if directory == images.resolve() or images.resolve() in directory.parents:
        raise ValueError("Backup directory must be outside product_images")
    directory.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    target = directory / f"inventra-pre-reset-{ts}.db"
    # Refuse to overwrite an earlier backup, including a same-second invocation.
    with target.open("xb"):
        pass
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as source:
        with sqlite3.connect(target) as destination:
            source.backup(destination)
    with sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True) as copy:
        if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Backup integrity_check failed")
        for table in BACKUP_TABLES:
            if copy.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] != before[table]:
                raise ValueError(f"Backup count mismatch: {table}")
    image_target = None
    if images.exists():
        if images.is_symlink() or not images.is_dir():
            raise ValueError("product_images must be a regular directory")
        image_target = directory / f"product_images-{ts}"
        shutil.copytree(images, image_target, symlinks=True)
    paths = [f"Database backup: {target}"]
    if image_target is not None:
        paths.append(f"Image backup: {image_target}")
    for line in paths:
        print(line, flush=True)
    return paths


def _default_bring_client(entity):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token or not entity:
        raise ValueError("Bring requires SUPERVISOR_TOKEN and --bring-entity or INVENTRA_BRING_TODO_ENTITY_ID; use --skip-bring explicitly to skip")
    return BringHaClient("http://supervisor/core/api", token, entity)


def _match(watch, items, watches):
    # Mirrors bring_service._remove_inventra_item_for_row; resolve separately so
    # ambiguity and missing UIDs abort the complete plan before any HTTP removal.
    claimed = {w["uid"] for w in watches
               if w["product_id"] != watch["product_id"] and w["uid"]}
    actionable = [item for item in items if item.get("status") == "needs_action"
                  and item.get("uid") not in claimed]
    if watch["state"] == "ON_LIST_CONFIRMED":
        matches = [item for item in actionable
                   if watch["uid"] and item.get("uid") == watch["uid"]]
    else:
        matches = [item for item in actionable
                   if _normalize(item.get("summary", "")) == _normalize(watch["name"])]
    if any(not item.get("uid") for item in matches):
        raise ValueError(f'Bring item missing uid: {watch["name"]}')
    if len(matches) > 1:
        raise ValueError(f'Ambiguous Bring item: {watch["name"]}')
    return matches[0] if matches else None


async def _bring(watches, *, dry_run, skip, factory, entity):
    candidates = [w for w in watches if w["origin"] == "INVENTRA_CREATED"
                  and w["state"] in ("PENDING_ADD", "ON_LIST_CONFIRMED")]
    def description(w):
        return (f'{w["name"]} ({w["product_id"]}, uid={w["uid"]}, '
                f'origin={w["origin"]}, state={w["state"]})')
    lines = ["Bring candidate: " + description(w) for w in candidates]
    lines.extend(f'Bring {w["name"]}: not removed (state={w["state"]}, origin={w["origin"]})'
                 for w in watches if w not in candidates)
    if skip:
        return lines + ["Bring phase explicitly skipped (--skip-bring)."]
    if not candidates:
        return lines + ["Bring: no Inventra-created candidates."]
    try:
        client = factory(entity)
        items = await client.get_items()
        matched = [(w, _match(w, items, watches)) for w in candidates]
        removed = set()
        for watch, item in matched:
            if item is None:
                lines.append(f'Bring absent: {watch["name"]}')
            elif dry_run:
                lines.append(f'Bring currently on list: {description(watch)} ({item["uid"]})')
            elif item["uid"] not in removed:
                await client.remove_item(item["uid"])
                removed.add(item["uid"])
                line = f'Bring removed: {description(watch)} ({item["uid"]})'
                print(line, flush=True)
                lines.append(line)
    except ValueError as exc:
        if not dry_run:
            raise
        lines.append(f"Bring plan error: {exc}")
    except Exception as exc:
        if not dry_run:
            raise
        lines.append(f"Bring read-only inspection unavailable: {exc}")
    return lines


def _remove_images(directory):
    removed = 0
    errors = []
    try:
        if directory.is_symlink():
            raise ValueError("Refusing image cleanup through a directory symlink")
        if directory.exists():
            for file in directory.iterdir():
                if not file.is_symlink() and file.is_file() and IMAGE_PREFIX.match(file.name):
                    try:
                        file.unlink()
                        removed += 1
                    except OSError as exc:
                        errors.append(f"Image cleanup error: {file}: {exc}")
    except (OSError, ValueError) as exc:
        errors.append(f"Image cleanup error: {exc}")
    return [f"Product image files removed: {removed}", *errors]


def reset_database(database: str | Path, *, dry_run=False, confirm=False,
                   backup_dir=None, bring_entity=None, skip_bring=False,
                   bring_client_factory=None) -> str:
    """Factory receives the todo entity id and returns an async Bring client.

    Delete processed_operations whose result_snapshot contains a deleted product
    id. Keep all other results, including pairing, which contains no product ids.
    Existing Product DELETE rows are retained, including on repeated resets.
    """
    if dry_run == confirm:
        raise ValueError("Specify exactly one of --dry-run or --confirm; no changes made")
    path = Path(database).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a database file: {path}")
    uri = f"{path.as_uri()}?mode={'ro' if dry_run else 'rw'}&uri=true"
    engine = create_engine(f"sqlite:///{uri}") if dry_run else configure_engine(uri)

    @event.listens_for(engine, "connect")
    def _options(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        if dry_run:
            connection.isolation_level = None
            connection.execute("PRAGMA query_only=ON")

    if dry_run:
        @event.listens_for(engine, "begin")
        def _begin(connection):
            connection.exec_driver_sql("BEGIN")

    backup_paths = []
    try:
        with Session(engine, autoflush=False) as db, db.begin():
            counter = db.get(m.RevisionCounter, 0)
            maximum = db.scalar(select(func.max(m.ChangeLog.revision))) or 0
            if counter is None or counter.current_revision < maximum:
                raise ValueError(f"revision_counter missing or below change_log max revision {maximum}")
            revision_before = counter.current_revision
            before = _counts(db)
            _check_foreign_keys(db)
            products = db.scalars(select(m.Product).order_by(m.Product.id)).all()
            product_ids = [product.id for product in products]
            latest = {}
            for row in db.scalars(select(m.ChangeLog).where(
                    m.ChangeLog.entity_type == "Product").order_by(
                        m.ChangeLog.revision, m.ChangeLog.id)):
                latest[row.entity_id] = row
            stale_ids = sorted(id_ for id_, row in latest.items()
                               if id_ not in product_ids and row.change_kind != "DELETE")
            targeted_ids = product_ids + stale_ids
            purge_predicate = and_(
                m.ChangeLog.entity_type.in_(PRODUCT_ENTITY_TYPES),
                ~and_(m.ChangeLog.entity_type == "Product", m.ChangeLog.change_kind == "DELETE"))
            # Select once for both the projected count and deletion. Raw substring
            # matching also handles nested results without relying on JSON shape.
            product_operations = [operation_id for operation_id, snapshot in db.execute(
                select(m.ProcessedOperation.operation_id, m.ProcessedOperation.result_snapshot))
                if any(product_id in snapshot for product_id in targeted_ids)] if targeted_ids else []
            watches = [{"product_id": w.product_id, "name": w.bring_item_name,
                        "uid": w.bring_uid, "origin": w.origin, "state": w.state}
                       for w in db.scalars(select(m.BringWatchState).order_by(m.BringWatchState.product_id))]
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            tombstones = []
            for product in products:
                snapshot = product_snapshot(product)
                snapshot.update(version=product.version + 1, deletedAt=now)
                tombstones.append((product.id, snapshot))
            tombstones.extend((id_, {"id": id_, "deletedAt": now}) for id_ in stale_ids)
            after = dict(before)
            after.update({model.__tablename__: 0 for model in CLEAR_MODELS})
            after["processed_operations"] -= len(product_operations)
            if tombstones:
                purged = db.scalar(select(func.count()).select_from(m.ChangeLog).where(
                    purge_predicate))
                after["change_log"] = before["change_log"] - purged + len(tombstones)
            revision_after = revision_before + bool(tombstones)
            images = path.parent / "product_images"
            image_count = sum(f.is_file() and not f.is_symlink() for f in images.iterdir()) if images.is_dir() else 0
            needed = bool(tombstones) or any(before[model.__tablename__] for model in CLEAR_MODELS) or (
                images.is_dir() and any(not f.is_symlink() and f.is_file() and IMAGE_PREFIX.match(f.name)
                                       for f in images.iterdir()))
            if not dry_run and needed:
                backup_paths = _backup(path, Path(backup_dir) if backup_dir else path.parent / "backups", before)
            bring_lines = asyncio.run(_bring(watches, dry_run=dry_run, skip=skip_bring,
                factory=bring_client_factory or _default_bring_client,
                entity=bring_entity or os.environ.get("INVENTRA_BRING_TODO_ENTITY_ID")))
            if not dry_run:
                # Bound parameter counts even when many operations reference products.
                for offset in range(0, len(product_operations), 200):
                    db.execute(delete(m.ProcessedOperation).where(
                        m.ProcessedOperation.operation_id.in_(product_operations[offset:offset + 200])))
                clear_tables = {model.__table__ for model in CLEAR_MODELS}
                for table in reversed(Base.metadata.sorted_tables):
                    if table in clear_tables and before[table.name]:
                        db.execute(delete(table))
                if tombstones:
                    db.execute(delete(m.ChangeLog).where(purge_predicate))
                    changes = ChangeSet(db)
                    for id_, snapshot in tombstones:
                        changes.record("Product", id_, m.ChangeKind.DELETE, snapshot)
                    db.flush()
                    if changes.revision <= maximum or changes.revision != revision_after:
                        raise ValueError("Reset revision did not advance monotonically")
                if _counts(db) != after:
                    raise ValueError("Actual row counts differ from reset plan")
            _check_foreign_keys(db)
            lines = ["DRY RUN (read-only; projected counts)" if dry_run else "RESET COMMITTED"]
            lines.extend(f"{table}: {count} -> {after[table]}" for table, count in before.items())
            lines.extend(f"Product targeted: {p.name} ({p.id})" for p in products)
            lines.extend([
                f"Product image files before reset: {image_count}",
                f"revision_counter: {revision_before} -> {revision_after}; pre-reset max change_log revision: {maximum}",
                f"New Product DELETE tombstones: {len(tombstones)}" + (f" at revision {revision_after}" if tombstones else ""),
                f"Tombstones for current products: {len(products)}; missing products: {len(stale_ids)}",
                "processed_operations: delete results containing targeted product ids, including MHD acknowledgedSignature results; keep results without targeted product ids (pairing, location/store/unit).",
                "All unlisted tables unchanged; foreign_key_check: 0 violations.",
                *bring_lines, *backup_paths,
            ])
            if not tombstones and not any(before[model.__tablename__] for model in CLEAR_MODELS):
                lines.append("Already reset: change_log and revision_counter unchanged.")
        # Only a successful transaction exit authorizes filesystem deletion.
        if not dry_run and needed:
            lines.extend(_remove_images(images))
        return "\n".join(lines)
    finally:
        engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--bring-entity")
    parser.add_argument("--skip-bring", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = reset_database(args.database, dry_run=args.dry_run, confirm=args.confirm,
            backup_dir=args.backup_dir, bring_entity=args.bring_entity, skip_bring=args.skip_bring)
    except Exception as exc:
        print(f"ABORT: {exc}. No reset changes committed; images retained. Earlier Bring removals may have succeeded.", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
