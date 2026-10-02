"""Persist the dataset identity without allocating a sync revision."""
from datetime import datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0011_instance_identity"
down_revision = "0010_scrub_pairing_tokens_from_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    table = sa.Table(
        "instance_meta", sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("instance_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime),
        sa.CheckConstraint("id = 0", name="ck_instance_meta_single_row"),
    )
    table.create(db, checkfirst=True)
    if db.execute(sa.select(table.c.instance_id).where(table.c.id == 0)).first() is None:
        db.execute(table.insert().values(id=0, instance_id=str(uuid4()), created_at=datetime.utcnow()))


def downgrade() -> None:
    op.drop_table("instance_meta")
