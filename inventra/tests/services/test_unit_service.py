import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from inventra_backend.db.models import Product, Unit
from inventra_backend.errors import BusinessRuleViolation
from inventra_backend.revision.change_log import change_set
from inventra_backend.services.product_service import create_product, update_product, list_products
from inventra_backend.services.unit_service import save_unit


def test_rename_only_updates_unit_row(db_session):
    unit = save_unit(db_session, "Dose", "Ds.")
    with change_set(db_session) as cs:
        create_product(db_session, cs, "create", "product", "Soup", None, None, None, quantity=2, unit_id=unit.id)
    statements = []
    connection = db_session.connection()

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        save_unit(db_session, "Dosen", "Dose", unit)
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert not any(s.lstrip().upper().startswith("UPDATE PRODUCTS") for s in statements)
    assert list_products(db_session)[0]["unit"]["abbreviation"] == "Dose"
    assert db_session.get(Product, "product").unit_id == unit.id
    assert db_session.get(Product, "product").version == 1


def test_referenced_unit_cannot_be_deleted_on_backend_connection(db_session):
    unit = save_unit(db_session, "Dose", "Ds.")
    db_session.add(Product(id="product", name="Soup", unit=unit))
    db_session.commit()
    db_session.delete(unit)
    with pytest.raises(IntegrityError, match="unit is referenced"):
        db_session.flush()
    db_session.rollback()


def test_product_service_validates_and_switches_units(db_session):
    grams = db_session.scalar(select(Unit).where(Unit.abbreviation == "g"))
    litres = db_session.scalar(select(Unit).where(Unit.abbreviation == "l"))
    with change_set(db_session) as cs:
        create_product(db_session, cs, "create", "product", "Soup", None, None, None, quantity=400, unit_id=grams.id)
    with change_set(db_session) as cs:
        result = update_product(db_session, cs, "update", "product", 1, "Soup", None, None, None, quantity=1.5, unit_id=litres.id)
    assert result["quantity"] == 1.5
    assert result["unit"]["abbreviation"] == "l"
    with pytest.raises(BusinessRuleViolation, match="Unknown unitId"):
        with change_set(db_session) as cs:
            create_product(db_session, cs, "invalid", "bad", "Soup", None, None, None, unit_id="missing")
