from tests.ids import test_uuid


def test_create_location(api_client_with_device):
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    resp = client.post(
        "/api/v1/locations",
        json={"operationId": test_uuid("op1"), "id": location_id, "name": "Keller"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body == {"id": location_id, "name": "Keller", "version": 1, "deletedAt": None}


def test_create_location_duplicate_normalized_name_rejected(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    client.post("/api/v1/locations", json={"operationId": test_uuid("op1"), "id": test_uuid("l1"), "name": "Keller"}, headers=headers)
    resp = client.post("/api/v1/locations", json={"operationId": test_uuid("op2"), "id": test_uuid("l2"), "name": "  KELLER "}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_ENTITY"


def test_create_location_replay_same_operation_id_is_a_safe_noop(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    body_payload = {"operationId": test_uuid("op1"), "id": test_uuid("l1"), "name": "Keller"}
    first = client.post("/api/v1/locations", json=body_payload, headers=headers)
    second = client.post("/api/v1/locations", json=body_payload, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


def test_update_location_with_current_version_succeeds(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    location_id = test_uuid("l1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("op1"), "id": location_id, "name": "Keller"}, headers=headers)
    resp = client.patch(
        f"/api/v1/locations/{location_id}",
        json={"operationId": test_uuid("op2"), "name": "Vorratskeller", "version": 1},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": location_id, "name": "Vorratskeller", "version": 2, "deletedAt": None}


def test_update_location_with_stale_version_returns_409_with_canonical_state(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    location_id = test_uuid("l1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("op1"), "id": location_id, "name": "Keller"}, headers=headers)
    client.patch(f"/api/v1/locations/{location_id}", json={"operationId": test_uuid("op2"), "name": "Vorratskeller", "version": 1}, headers=headers)
    # A second device, still on version 1, retries its own (now stale) update:
    resp = client.patch(
        f"/api/v1/locations/{location_id}",
        json={"operationId": test_uuid("op3"), "name": "Kellerregal", "version": 1},
        headers=headers,
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "STALE_VERSION"
    assert body["current"] == {"id": location_id, "name": "Vorratskeller", "version": 2, "deletedAt": None}


def test_soft_delete_location_hides_it_from_list_but_keeps_it_bumping_version(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    location_id = test_uuid("l1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("op1"), "id": location_id, "name": "Keller"}, headers=headers)
    resp = client.request(
        "DELETE", f"/api/v1/locations/{location_id}", json={"operationId": test_uuid("op2"), "version": 1}, headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["deletedAt"] is not None
    listing = client.get("/api/v1/locations", headers=headers)
    assert listing.json() == []
