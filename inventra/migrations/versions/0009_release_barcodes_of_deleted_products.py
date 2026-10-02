"""Release active barcodes owned by deleted products."""
import json
from datetime import datetime
from alembic import op
import sqlalchemy as sa

revision = "0009_release_barcodes_of_deleted_products"
down_revision = "0008_free_deleted_master_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    rows = db.execute(sa.text("""
        SELECT b.code, b.product_id, b.version FROM barcodes b
        JOIN products p ON p.id = b.product_id
        WHERE b.deleted_at IS NULL AND p.deleted_at IS NOT NULL
    """)).mappings().all()
    if not rows:
        return
    revision = db.execute(sa.text(
        "SELECT MAX((SELECT current_revision FROM revision_counter WHERE id = 0), "
        "COALESCE(MAX(revision), 0)) + 1 FROM change_log"
    )).scalar_one()
    db.execute(sa.text(
        "UPDATE revision_counter SET current_revision = :revision WHERE id = 0"
    ), {"revision": revision})
    now = datetime.utcnow()
    for row in rows:
        db.execute(sa.text("UPDATE barcodes SET deleted_at = :now, version = version + 1 WHERE code = :code"),
                   {"now": now, "code": row["code"]})
        snapshot = {"code": row["code"], "productId": row["product_id"],
                    "version": row["version"] + 1, "deletedAt": now.isoformat()}
        db.execute(sa.text("""
            INSERT INTO change_log
                (revision, entity_type, entity_id, change_kind, snapshot, created_at)
            VALUES (:revision, 'Barcode', :code, 'DELETE', :snapshot, :now)
        """), {"revision": revision, "code": row["code"],
               "snapshot": json.dumps(snapshot), "now": now})


def downgrade() -> None:
    pass
