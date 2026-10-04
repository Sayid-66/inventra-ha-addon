"""Record the time batches entered their current location and resync snapshots."""
import json
from datetime import datetime

from alembic import op
import sqlalchemy as sa

from inventra_backend.services.location_kind import is_freezer_location_name

revision = "0012_batch_stored_at"
down_revision = "0011_instance_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("batches") as batch_op:
        batch_op.add_column(sa.Column("stored_at", sa.Integer(), nullable=True))
    db = op.get_bind()
    batches = db.execute(sa.text("""
        SELECT b.*, COALESCE(p.location_id, c.location_id) AS origin_location
        FROM batches b
        LEFT JOIN purchase_events p ON p.id = b.purchase_event_id
        LEFT JOIN correction_events c ON c.id = b.correction_event_id
    """)).mappings().all()
    for batch in batches:
        stored_at = batch["event_timestamp"]
        if batch["origin_location"] is None or batch["origin_location"] != batch["location_id"]:
            candidates = db.execute(sa.text("""
                SELECT r.timestamp, source.name AS from_name, destination.name AS to_name
                FROM relocation_events r
                JOIN locations source ON source.id = r.from_location_id
                JOIN locations destination ON destination.id = r.to_location_id
                WHERE r.product_id = :product_id AND r.to_location_id = :location_id
                  AND r.timestamp >= :event_timestamp AND r.stock_kind = :stock_kind
            """), {
                "product_id": batch["product_id"], "location_id": batch["location_id"],
                "event_timestamp": batch["event_timestamp"],
                "stock_kind": "CONTENT" if batch["is_content_tracked"] else "STK",
            }).mappings().all()
            stored_at = max((
                candidate["timestamp"] for candidate in candidates
                if not (is_freezer_location_name(candidate["from_name"])
                        and is_freezer_location_name(candidate["to_name"]))
            ), default=stored_at)
        db.execute(sa.text("UPDATE batches SET stored_at = :v WHERE id = :id"),
                   {"v": stored_at, "id": batch["id"]})
    rows = db.execute(sa.text("SELECT * FROM batches")).mappings().all()
    if not rows:
        return
    new_revision = db.execute(sa.text(
        "SELECT MAX((SELECT current_revision FROM revision_counter WHERE id = 0), "
        "COALESCE(MAX(revision), 0)) + 1 FROM change_log"
    )).scalar_one()
    db.execute(sa.text(
        "UPDATE revision_counter SET current_revision = :revision WHERE id = 0"
    ), {"revision": new_revision})
    now = datetime.utcnow()
    for row in rows:
        snapshot = {
            "id": row["id"], "productId": row["product_id"],
            "purchaseEventId": row["purchase_event_id"],
            "correctionEventId": row["correction_event_id"],
            "locationId": row["location_id"], "mhd": row["mhd"],
            "eventTimestamp": row["event_timestamp"], "storedAt": row["stored_at"],
            "isContentTracked": bool(row["is_content_tracked"]),
            "contentUnitLabel": row["content_unit_label"],
            "remainingQuantity": row["remaining_quantity"],
        }
        db.execute(sa.text("""
            INSERT INTO change_log
                (revision, entity_type, entity_id, change_kind, snapshot, created_at)
            VALUES (:revision, 'Batch', :id, 'UPDATE', :snapshot, :now)
        """), {"revision": new_revision, "id": row["id"],
               "snapshot": json.dumps(snapshot), "now": now})


def downgrade() -> None:
    with op.batch_alter_table("batches") as batch_op:
        batch_op.drop_column("stored_at")
