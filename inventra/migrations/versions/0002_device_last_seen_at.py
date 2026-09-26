"""add device last seen timestamp

Revision ID: 0002_device_last_seen_at
Revises: cfe82b2fc527
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa


revision = '0002_device_last_seen_at'
down_revision = 'cfe82b2fc527'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('last_seen_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('devices', 'last_seen_at')
