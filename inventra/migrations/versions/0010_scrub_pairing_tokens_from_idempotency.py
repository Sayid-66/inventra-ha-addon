"""Remove plaintext device tokens from legacy idempotency snapshots."""
import json

from alembic import op
import sqlalchemy as sa

revision = "0010_scrub_pairing_tokens_from_idempotency"
down_revision = "0009_release_barcodes_of_deleted_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    rows = db.execute(sa.text(
        "SELECT operation_id, result_snapshot FROM processed_operations"
    )).mappings().all()
    for row in rows:
        try:
            snapshot = json.loads(row["result_snapshot"])
        except (ValueError, TypeError):
            continue
        if not isinstance(snapshot, dict) or "deviceId" not in snapshot or "token" not in snapshot:
            continue
        del snapshot["token"]
        db.execute(sa.text(
            "UPDATE processed_operations SET result_snapshot = :snapshot WHERE operation_id = :id"
        ), {"snapshot": json.dumps(snapshot), "id": row["operation_id"]})


def downgrade() -> None:
    pass
