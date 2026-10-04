#!/usr/bin/env python3
"""One-off name maintenance. Stop backend activity while running confirm.

No schema creation or migration. Dry-run opens SQLite read-only; confirm verifies
a backup first, then uses one BEGIN IMMEDIATE transaction and normal sync logs.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

source_dir = Path(__file__).resolve().parents[1] / "src"
if (source_dir / "inventra_backend").is_dir():
    sys.path.insert(0, str(source_dir))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from inventra_backend.db.base import configure_engine
from inventra_backend.db.models import Product, Unit
from inventra_backend.resolver.product_naming import compose_product_name, extract_size_token
from inventra_backend.resolver.quantity import parse_quantity
from inventra_backend.services.unit_normalizer import canonical_unit
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services.product_service import update_product


def _backup(path, directory):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"inventra-pre-namefix-{stamp}.db"
    with target.open("xb"):
        pass
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as source:
        with sqlite3.connect(target) as destination:
            source.backup(destination)
        with sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True) as copy:
            if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Backup integrity_check failed")
            if (copy.execute("SELECT count(*) FROM products").fetchone()
                    != source.execute("SELECT count(*) FROM products").fetchone()):
                raise ValueError("Backup products count mismatch")
    return target


def normalize_database(database, *, dry_run=False, confirm=False, backup_dir=None,
                       include_manual=False):
    if dry_run == confirm:
        raise ValueError("Specify exactly one of --dry-run or --confirm")
    path = Path(database).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Database must be a regular file")
    lines = ["DRY RUN (read-only)" if dry_run else "NORMALIZATION COMMITTED"]
    if confirm:
        backup = _backup(path, Path(backup_dir).resolve() if backup_dir else path.parent / "backups")
        lines.append(f"Database backup: {backup}")
    uri = f"{path.as_uri()}?mode={'ro' if dry_run else 'rw'}&uri=true"
    engine = create_engine(f"sqlite:///{uri}") if dry_run else configure_engine(uri)
    try:
        with Session(engine) as db, db.begin():
            changes = ChangeSet(db)
            lines.append("Product | Before -> After | Status")
            for product in db.scalars(select(Product).where(Product.deleted_at.is_(None)).order_by(Product.id)):
                provenance = json.loads(product.field_provenance) if product.field_provenance else {}
                manual = bool(provenance.get("name", {}).get("manual"))
                token = extract_size_token(product.name)[1]
                parsed = parse_quantity(token)
                updates = {}
                size_status = None
                if parsed:
                    if parsed.pack_count > 1:
                        size_status = "skipped: multipack"
                    elif product.quantity is not None or product.unit_id is not None:
                        stored = (parse_quantity(f"{product.quantity} {product.unit.abbreviation}")
                                  if product.quantity is not None and product.unit else None)
                        if (stored is None or (canonical_unit(stored.unit) or stored.unit)
                                != (canonical_unit(parsed.unit) or parsed.unit)
                                or not math.isclose(stored.amount * stored.pack_count,
                                                    parsed.amount * parsed.pack_count)):
                            size_status = "skipped: size conflict"
                    else:
                        unit = db.scalar(select(Unit).where(Unit.abbreviation.collate("NOCASE") == canonical_unit(parsed.unit)))
                        if unit is None:
                            size_status = "skipped: unit unknown"
                        else:
                            updates = {"quantity": parsed.amount, "unit_id": unit.id}
                name = (product.name if size_status else
                        compose_product_name(product.name, [product.brand] if product.brand else [], None))
                status = ("skipped: manual" if manual and not include_manual else
                          size_status if size_status else
                          "skipped: unusable" if name is None else
                          "skipped: unchanged" if name == product.name and not updates else
                          "would update (size filled)" if dry_run and updates else
                          "would update" if dry_run else
                          "updated (size filled)" if updates else "updated")
                before = product.name
                if status.startswith("updated"):
                    update_product(db, changes, str(uuid4()), product.id, product.version, name=name, **updates)
                lines.append(f"{product.id} | {before} -> {name or '(no safe name)'} | {status}; manual={manual}")
        return "\n".join(lines)
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--confirm", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--include-manual", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(normalize_database(args.database, dry_run=args.dry_run, confirm=args.confirm,
              backup_dir=args.backup_dir, include_manual=args.include_manual))
    except Exception as exc:
        print(f"ABORT: {exc}. No name changes committed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
