"""Managed package units, preserving existing product quantities and labels."""
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from uuid6 import uuid7


revision = "0006_units_catalog"
down_revision = "0005_product_history"
branch_labels = None
depends_on = None

# Frozen migration data: later catalog or normalizer changes must not alter it.
STANDARD_UNITS = [
    ("Gramm", "g"), ("Kilogramm", "kg"), ("Milliliter", "ml"), ("Liter", "l"),
    ("Stück", "Stk."), ("Packung", "Pkg."), ("Rolle", "Rolle"), ("Blatt", "Blatt"),
    ("Portion", "Portion"), ("Tablette", "Tabl."), ("Kapsel", "Kaps."), ("Beutel", "Beutel"),
]


STANDARD_UNIT_IDS = {'g': '01a0e1f3-1846-731c-8e60-bbd0970212b8', 'kg': '01a0e1f3-1847-74be-b7e8-68a5e6ef60b4', 'ml': '01a0e1f3-1848-7a85-9ec2-1028e90cdb9f', 'l': '01a0e1f3-1849-7a08-9e5b-c524f2953c58', 'Stk.': '01a0e1f3-184a-7e83-88e7-9cec2b172ce8', 'Pkg.': '01a0e1f3-184b-7ecc-b5d9-fcc489e08194', 'Rolle': '01a0e1f3-184c-7daa-ac63-f2354d5307bb', 'Blatt': '01a0e1f3-184d-79a0-9271-09699564a9b2', 'Portion': '01a0e1f3-184e-7b13-bdd8-5dd316310830', 'Tabl.': '01a0e1f3-184f-7215-8085-592d3e56a555', 'Kaps.': '01a0e1f3-1850-782d-9695-08bcc58b7e80', 'Beutel': '01a0e1f3-1851-7353-82a6-0a20b4f53873'}

def upgrade() -> None:
    units = op.create_table(
        "units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("abbreviation", sa.String(32, collation="NOCASE"), nullable=False),
        sa.Column("is_standard", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("abbreviation", name="uq_unit_abbreviation"),
    )
    db = op.get_bind()
    by_label = {}
    for name, abbreviation in STANDARD_UNITS:
        unit_id = STANDARD_UNIT_IDS[abbreviation]
        db.execute(units.insert().values(id=unit_id, name=name, abbreviation=abbreviation,
                                        is_standard=True, created_at=datetime.utcnow()))
        by_label[name.casefold()] = by_label[abbreviation.casefold()] = unit_id
    # Native SQLite ALTER keeps all incoming product/event FKs and indexes intact;
    # a batch table rebuild would delete the referenced products table temporarily.
    op.execute("ALTER TABLE products ADD COLUMN unit_id VARCHAR(36) REFERENCES units(id) ON DELETE RESTRICT")
    for product_id, label in db.execute(sa.text("SELECT id, quantity_unit FROM products WHERE quantity_unit IS NOT NULL")).all():
        key = label.strip().casefold()
        unit_id = by_label.get(key)
        if unit_id is None:
            unit_id = str(uuid7())
            # Even an old empty label is retained verbatim rather than losing data.
            db.execute(units.insert().values(id=unit_id, name=label, abbreviation=label,
                                            is_standard=False, created_at=datetime.utcnow()))
            by_label[key] = unit_id
        db.execute(sa.text("UPDATE products SET unit_id=:unit_id WHERE id=:id"), {"unit_id": unit_id, "id": product_id})
    op.drop_column("products", "quantity_unit")
    # Existing connections do not enable SQLite foreign_keys. Enforce this new
    # catalog's deletion rule there too, without changing event-table behavior.
    op.execute("""CREATE TRIGGER units_restrict_delete BEFORE DELETE ON units
        WHEN EXISTS (SELECT 1 FROM products WHERE unit_id=OLD.id)
        BEGIN SELECT RAISE(ABORT, 'unit is referenced by products'); END""")


def downgrade() -> None:
    op.execute("DROP TRIGGER units_restrict_delete")
    op.add_column("products", sa.Column("quantity_unit", sa.String(32), nullable=True))
    op.execute("UPDATE products SET quantity_unit=(SELECT abbreviation FROM units WHERE units.id=products.unit_id)")
    op.drop_column("products", "unit_id")
    op.drop_table("units")
