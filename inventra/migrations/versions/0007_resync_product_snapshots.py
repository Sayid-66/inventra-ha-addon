"""Refresh product change-log snapshots after the unit catalog migration."""
import json
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "0007_resync_product_snapshots"
down_revision = "0006_units_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    products = db.execute(sa.text("""
        SELECT p.*, u.id AS reference_id, u.name AS unit_name,
               u.abbreviation AS unit_abbreviation
        FROM products p LEFT JOIN units u ON u.id = p.unit_id
    """)).mappings().all()
    if not products:
        return

    # Keep the normal write allocator in step with this single changeset.
    new_revision = db.execute(sa.text(
        "SELECT COALESCE(MAX(revision), 0) + 1 FROM change_log"
    )).scalar_one()
    db.execute(sa.text(
        "UPDATE revision_counter SET current_revision = :revision WHERE id = 0"
    ), {"revision": new_revision})
    for product in products:
        # Frozen equivalent of product_service._to_dict and unit_reference.
        snapshot = {
            "id": product["id"],
            "name": product["name"],
            "imageUrl": product["image_url"],
            "minStock": product["min_stock"],
            "contentUnitLabel": product["content_unit_label"],
            "brand": product["brand"],
            "quantity": product["quantity"],
            "unit": {
                "id": product["reference_id"],
                "name": product["unit_name"],
                "abbreviation": product["unit_abbreviation"],
            } if product["reference_id"] is not None else None,
            "category": product["category"],
            "variant": product["variant"],
            "fieldProvenance": json.loads(product["field_provenance"]) if product["field_provenance"] else {},
            "version": product["version"],
            "deletedAt": datetime.fromisoformat(product["deleted_at"]).isoformat() if product["deleted_at"] else None,
        }
        db.execute(sa.text("""
            INSERT INTO change_log
                (revision, entity_type, entity_id, change_kind, snapshot, created_at)
            VALUES (:revision, 'Product', :id, 'UPDATE', :snapshot, :created_at)
        """), {"revision": new_revision, "id": product["id"],
               "snapshot": json.dumps(snapshot), "created_at": datetime.utcnow()})


def downgrade() -> None:
    pass
