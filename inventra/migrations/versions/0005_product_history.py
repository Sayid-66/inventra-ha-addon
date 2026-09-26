"""Preserve soft-deleted products and repair unambiguous event tombstones.

Revision ID: 0005_product_history
Revises: 0004_product_resolver_fields
"""
from alembic import op
from sqlalchemy.orm import Session

from inventra_backend.services.product_service import backfill_deleted_product_history

revision = "0005_product_history"
down_revision = "0004_product_resolver_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Participate in Alembic's transaction; ChangeSet allocates the revision.
    with Session(bind=op.get_bind()) as db:
        backfill_deleted_product_history(db)


def downgrade() -> None:
    # Data repair is intentionally retained; never re-tombstone history.
    pass
