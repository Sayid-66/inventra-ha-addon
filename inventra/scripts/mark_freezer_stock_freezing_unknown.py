#!/usr/bin/env python3
"""Mark old freezer stock's freezing date unknown; back up the database first.

python3 mark_freezer_stock_freezing_unknown.py /data/inventra.db --dry-run --entered-before 2026-10-05T00:00:00+00:00
python3 mark_freezer_stock_freezing_unknown.py /data/inventra.db --confirm --entered-before 2026-10-05T00:00:00+00:00
No schema creation or migration is performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

source_dir = Path(__file__).resolve().parents[1] / "src"
if (source_dir / "inventra_backend").is_dir():
    sys.path.insert(0, str(source_dir))

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from inventra_backend.db.base import configure_engine
from inventra_backend.db import models as m
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services.inventory_service import _batch_to_dict
from inventra_backend.services.location_kind import is_freezer_location_name


def parse_cutoff(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("offset required")
        return result
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--entered-before must be an ISO datetime with an offset") from exc


def mark_database(database: str | Path, *, entered_before: datetime,
                  dry_run: bool = False, confirm: bool = False) -> str:
    if dry_run == confirm:
        raise ValueError("Specify exactly one of --dry-run or --confirm")
    if entered_before.tzinfo is None or entered_before.utcoffset() is None:
        raise ValueError("entered_before requires a timezone offset")
    # Compare epoch milliseconds without rounding a sub-millisecond cutoff.
    delta = entered_before.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    microseconds = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    cutoff_ms = -(-microseconds // 1000)
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
            rows = [row for row in db.execute(
                select(m.Batch, m.Product.name, m.Location.name)
                .join(m.Product, m.Batch.product_id == m.Product.id)
                .join(m.Location, m.Batch.location_id == m.Location.id)
                .where(m.Batch.remaining_quantity > 0, m.Batch.event_timestamp < cutoff_ms,
                       m.Batch.stored_at.is_not(None), m.Batch.stored_at < cutoff_ms)
                .order_by(m.Batch.id)
            ).all() if is_freezer_location_name(row[2])]
            lines = ["DRY RUN (read-only)" if dry_run else "UPDATE COMMITTED",
                     "batch id | product name | location | mhd | event time (epoch ms) | old stored_at (epoch ms) | relocated"]
            for batch, product_name, location_name in rows:
                lines.append(f"{batch.id} | {product_name} | {location_name} | {batch.mhd} | {batch.event_timestamp} | {batch.stored_at} | {batch.stored_at != batch.event_timestamp}")
            if rows:
                counter = db.get(m.RevisionCounter, 0)
                maximum = db.scalar(select(func.max(m.ChangeLog.revision))) or 0
                if counter is None or counter.current_revision < maximum:
                    raise ValueError("revision_counter missing or below change_log max; refusing unsafe revision allocation")
                expected_revision = counter.current_revision + 1
                if not dry_run:
                    changes = ChangeSet(db)
                    for batch, _product, _location in rows:
                        batch.stored_at = None
                        db.flush()
                        changes.record("Batch", batch.id, m.ChangeKind.UPDATE, _batch_to_dict(batch))
                    db.flush()
                    if changes.revision != expected_revision or changes.revision <= maximum:
                        raise ValueError("Revision did not advance monotonically")
                    lines.append(f"UPDATE changes: {len(rows)} at revision {changes.revision}")
            violations = db.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ValueError(f"foreign_key_check failed: {violations!r}")
            lines.append(f"Matching batches: {len(rows)}; {'would change' if dry_run else 'changed'}: {len(rows)}")
            if not rows:
                lines.append("nothing to do")
            lines.append("foreign_key_check: 0 violations")
        return "\n".join(lines)
    finally:
        engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm", action="store_true")
    parser.add_argument("--entered-before", type=parse_cutoff, required=True)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        report = mark_database(args.database, entered_before=args.entered_before,
                               dry_run=args.dry_run, confirm=args.confirm)
    except Exception as exc:
        print(f"ABORT: {exc}. No changes committed (transaction rolled back if started).", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
