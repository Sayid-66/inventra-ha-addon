from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import ResolverSourceCache
from .source_client import SourceCandidate, SourceResult

_TTL_BY_STATUS = {
    "FOUND": "resolver_cache_found_ttl_seconds",
    "NOT_FOUND": "resolver_cache_not_found_ttl_seconds",
    "ERROR": "resolver_cache_error_ttl_seconds",
}


def get_fresh_cache_entries(db: Session, barcode: str) -> dict[str, SourceResult]:
    now = datetime.utcnow()
    rows = db.execute(
        select(ResolverSourceCache).where(
            ResolverSourceCache.barcode == barcode,
            ResolverSourceCache.expires_at > now,
        )
    ).scalars().all()
    out: dict[str, SourceResult] = {}
    for row in rows:
        candidate = None
        if row.candidate_json:
            candidate = SourceCandidate(**json.loads(row.candidate_json))
        out[row.source] = SourceResult(source=row.source, status=row.status, candidate=candidate)
    return out


def upsert_cache_entries(
    db: Session, barcode: str, results: dict[str, SourceResult], settings: Settings,
) -> None:
    now = datetime.utcnow()
    for source, result in results.items():
        ttl_seconds = getattr(settings, _TTL_BY_STATUS[result.status])
        candidate_json = json.dumps(asdict(result.candidate)) if result.candidate else None
        stmt = sqlite_insert(ResolverSourceCache).values(
            barcode=barcode, source=source, status=result.status,
            candidate_json=candidate_json, fetched_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["barcode", "source"],
            set_={
                "status": stmt.excluded.status,
                "candidate_json": stmt.excluded.candidate_json,
                "fetched_at": stmt.excluded.fetched_at,
                "expires_at": stmt.excluded.expires_at,
            },
        )
        db.execute(stmt)
