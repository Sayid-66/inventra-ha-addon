"""Free names held by soft-deleted locations and stores."""
from alembic import op
import sqlalchemy as sa

revision = "0008_free_deleted_master_names"
down_revision = "0007_resync_product_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    for table in ("locations", "stores"):
        rows = db.execute(sa.text(
            f"SELECT id, normalized_name FROM {table} WHERE deleted_at IS NOT NULL"
        )).mappings().all()
        for row in rows:
            normalized = row["normalized_name"]
            if normalized.startswith("~deleted~"):
                continue
            db.execute(sa.text(
                f"UPDATE {table} SET normalized_name = :name WHERE id = :id"
            ), {"id": row["id"], "name": f"~deleted~{row['id']}~{normalized[:150]}"})


def downgrade() -> None:
    pass
