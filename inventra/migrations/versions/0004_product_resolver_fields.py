"""add product resolver fields and resolver cache/resolution tables

Revision ID: 0004_product_resolver_fields
Revises: 0003_bring_watch_state
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa


revision = '0004_product_resolver_fields'
down_revision = '0003_bring_watch_state'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('products') as batch_op:
        batch_op.add_column(sa.Column('brand', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('quantity', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('quantity_unit', sa.String(32), nullable=True))
        batch_op.add_column(sa.Column('category', sa.String(128), nullable=True))
        batch_op.add_column(sa.Column('variant', sa.String(128), nullable=True))
        batch_op.add_column(sa.Column('field_provenance', sa.Text(), nullable=True))

    op.create_table(
        'resolver_source_cache',
        sa.Column('barcode', sa.String(64), primary_key=True),
        sa.Column('source', sa.String(16), primary_key=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('candidate_json', sa.Text(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
    )

    op.create_table(
        'resolution_results',
        sa.Column('resolution_id', sa.String(36), primary_key=True),
        sa.Column('barcode', sa.String(64), nullable=False),
        sa.Column('proposed_fields_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('resolution_results')
    op.drop_table('resolver_source_cache')
    with op.batch_alter_table('products') as batch_op:
        batch_op.drop_column('field_provenance')
        batch_op.drop_column('variant')
        batch_op.drop_column('category')
        batch_op.drop_column('quantity_unit')
        batch_op.drop_column('quantity')
        batch_op.drop_column('brand')
