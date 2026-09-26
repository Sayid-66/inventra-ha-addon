import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import inspect

from inventra_backend.db.base import configure_engine


ADDON_ROOT = Path(__file__).resolve().parents[2]


def test_products_table_has_resolver_columns(tmp_path):
    db_path = str(tmp_path / "mig.db")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ADDON_ROOT,
        env={**os.environ, "INVENTRA_DB_PATH": db_path},
        check=True,
    )

    engine = configure_engine(db_path)
    inspector = inspect(engine)

    product_columns = {column["name"] for column in inspector.get_columns("products")}
    assert {
        "brand",
        "quantity",
        "quantity_unit",
        "category",
        "variant",
        "field_provenance",
    } <= product_columns

    assert "resolver_source_cache" in inspector.get_table_names()
    cache_columns = {
        column["name"] for column in inspector.get_columns("resolver_source_cache")
    }
    assert cache_columns == {
        "barcode",
        "source",
        "status",
        "candidate_json",
        "fetched_at",
        "expires_at",
    }

    assert "resolution_results" in inspector.get_table_names()
    result_columns = {
        column["name"] for column in inspector.get_columns("resolution_results")
    }
    assert result_columns == {
        "resolution_id",
        "barcode",
        "proposed_fields_json",
        "created_at",
        "expires_at",
    }
