from datetime import datetime

import pytest
from sqlalchemy import func, select

from inventra_backend.db.models import Location, Product, Batch, ChangeLog, CorrectionEvent, ProcessedOperation
from inventra_backend.errors import BusinessRuleViolation
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services.inventory_service import consume, correct_stock, relocate, _deplete_fifo
from tests.ids import test_uuid


@pytest.mark.parametrize("operation", ["consume", "correct", "relocate_from", "relocate_to"])
@pytest.mark.parametrize("deleted", [False, True])
def test_inventory_requires_active_locations_before_writes(db_session, operation, deleted):
    db = db_session
    product_id, bad_id, good_id = (test_uuid(x) for x in ["product", "bad", "good"])
    db.add(Product(id=product_id, name="Milk", version=1))
    db.add(Location(id=good_id, name="Kitchen", normalized_name="kitchen", version=1))
    if deleted:
        db.add(Location(id=bad_id, name="Old", normalized_name="old", version=1, deleted_at=datetime.now()))
    db.commit()
    common = dict(db=db, cs=ChangeSet(db), operation_id=test_uuid("operation"),
                  event_id=test_uuid("event"), product_id=product_id, stock_kind="STK",
                  timestamp=0, user_id="u1", device_id=None, source="APP")
    with pytest.raises(BusinessRuleViolation) as error:
        if operation == "consume":
            consume(**common, location_id=bad_id, quantity=1)
        elif operation == "correct":
            correct_stock(**common, location_id=bad_id, new_quantity=1,
                          content_unit_label=None, mhd_for_increase=None)
        else:
            relocate(**common, from_location_id=bad_id if operation == "relocate_from" else good_id,
                     to_location_id=bad_id if operation == "relocate_to" else good_id, quantity=1)
    assert error.value.code == "LOCATION_NOT_FOUND"
    for model in [Batch, ChangeLog, CorrectionEvent, ProcessedOperation]:
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert not db.new and not db.dirty and not db.deleted


def test_negative_correction_rejected_before_event_write(db_session):
    db = db_session
    db.add(Product(id=test_uuid("p"), name="Milk", version=1))
    db.add(Location(id=test_uuid("l"), name="Kitchen", normalized_name="kitchen", version=1))
    db.commit()
    with pytest.raises(BusinessRuleViolation) as error:
        correct_stock(db, ChangeSet(db), test_uuid("op"), test_uuid("e"), test_uuid("p"),
                      test_uuid("l"), "STK", None, -1, None, 0, "u1", None, "APP")
    assert error.value.code == "INVALID_QUANTITY"
    assert db.scalar(select(func.count()).select_from(CorrectionEvent)) == 0
    assert not db.new and not db.dirty


@pytest.mark.parametrize("amount", [0, -1])
def test_fifo_invalid_amount_is_business_error(db_session, amount):
    with pytest.raises(BusinessRuleViolation) as error:
        _deplete_fifo(db_session, ChangeSet(db_session), test_uuid("p"), test_uuid("l"), False, amount)
    assert error.value.code == "INVALID_QUANTITY"
