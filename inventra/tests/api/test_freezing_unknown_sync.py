from sqlalchemy.orm import Session

from inventra_backend.db.base import get_engine
from inventra_backend.db import models as m
from inventra_backend.revision.change_log import ChangeSet
from inventra_backend.services.inventory_service import _batch_to_dict


def test_sync_api_round_trips_unknown_freezing_date(api_client_with_device):
    client, device = api_client_with_device
    with Session(get_engine()) as db:
        db.add(m.Product(id="p", name="Stock"))
        db.add(m.Location(id="f", name="Gefrierfach", normalized_name="gefrierfach"))
        db.flush()
        batch = m.Batch(id="b", product_id="p", location_id="f", event_timestamp=1000,
                        stored_at=None, is_content_tracked=False, remaining_quantity=1)
        db.add(batch)
        db.flush()
        ChangeSet(db).record("Batch", batch.id, m.ChangeKind.UPDATE, _batch_to_dict(batch))
        db.commit()
    response = client.get("/api/v1/sync?since_revision=0",
                          headers={"Authorization": f"Bearer {device.token}"})
    assert response.status_code == 200
    change = next(row for row in response.json()["changes"] if row["entityId"] == "b")
    assert "storedAt" in change["snapshot"]
    assert change["snapshot"]["storedAt"] is None


def test_sync_openapi_documents_nullable_freezing_date(api_client):
    schema = api_client.get("/openapi.json").json()
    stored_at = schema["paths"]["/api/v1/sync"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["changes"]["items"]["properties"]["snapshot"]["properties"]["storedAt"]
    assert {entry["type"] for entry in stored_at["anyOf"]} == {"integer", "null"}
    assert "unknown" in stored_at["description"]
