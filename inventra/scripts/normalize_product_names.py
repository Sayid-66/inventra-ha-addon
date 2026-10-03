#!/usr/bin/env python3
"""One-off name maintenance. Stop backend activity while running confirm.

No schema creation or migration. Dry-run opens SQLite read-only; confirm verifies
a backup first, then uses one BEGIN IMMEDIATE transaction and normal sync logs.
"""
import argparse
from datetime import datetime, timezone
import json
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
from inventra_backend.db.models import Product
from inventra_backend.resolver.product_naming import compose_product_name, format_size
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
                size = format_size(product.quantity, product.unit.abbreviation) if product.unit else None
                name = compose_product_name(product.name, [product.brand] if product.brand else [], size)
                status = ("skipped: manual" if manual and not include_manual else
                          "skipped: unusable" if name is None else
                          "skipped: unchanged" if name == product.name else
                          "would update" if dry_run else "updated")
                before = product.name
                if status == "updated":
                    update_product(db, changes, str(uuid4()), product.id, product.version, name=name)
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
