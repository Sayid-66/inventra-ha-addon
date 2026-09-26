"""add bring_watch_state table and device default_location_id

Revision ID: 0003_bring_watch_state
Revises: 0002_device_last_seen_at
Create Date: 2026-09-07

"""
from alembic import op
import sqlalchemy as sa


revision = '0003_bring_watch_state'
down_revision = '0002_device_last_seen_at'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('devices') as batch_op:
        batch_op.add_column(sa.Column(
            'default_location_id',
            sa.String(36),
            sa.ForeignKey('locations.id', name='fk_devices_default_location_id_locations'),
            nullable=True,
        ))
    op.create_table(
        'bring_watch_state',
        sa.Column('product_id', sa.String(36), sa.ForeignKey('products.id'), primary_key=True),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('origin', sa.String(20), nullable=False),
        sa.Column('bring_item_name', sa.String(255), nullable=False),
        sa.Column('bring_uid', sa.String(64), nullable=True),
        sa.Column('lock_reason', sa.String(32), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('confirmation_deadline_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(255), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('bring_watch_state')
    with op.batch_alter_table('devices') as batch_op:
        batch_op.drop_column('default_location_id')
