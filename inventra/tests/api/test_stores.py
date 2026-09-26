from tests.ids import test_uuid


def test_create_store(api_client_with_device):
    client, device = api_client_with_device
    store_id = test_uuid("s1")
    resp = client.post(
        "/api/v1/stores",
        json={"operationId": test_uuid("op1"), "id": store_id, "name": "Rewe"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body == {"id": store_id, "name": "Rewe", "version": 1, "deletedAt": None}


def test_create_store_duplicate_normalized_name_rejected(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    client.post("/api/v1/stores", json={"operationId": test_uuid("op1"), "id": test_uuid("s1"), "name": "Rewe"}, headers=headers)
    resp = client.post("/api/v1/stores", json={"operationId": test_uuid("op2"), "id": test_uuid("l2"), "name": "  REWE "}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_ENTITY"


def test_create_store_replay_same_operation_id_is_a_safe_noop(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    body_payload = {"operationId": test_uuid("op1"), "id": test_uuid("s1"), "name": "Rewe"}
    first = client.post("/api/v1/stores", json=body_payload, headers=headers)
    second = client.post("/api/v1/stores", json=body_payload, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


def test_update_store_with_current_version_succeeds(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    store_id = test_uuid("s1")
    client.post("/api/v1/stores", json={"operationId": test_uuid("op1"), "id": store_id, "name": "Rewe"}, headers=headers)
    resp = client.patch(
        f"/api/v1/stores/{store_id}",
        json={"operationId": test_uuid("op2"), "name": "Rewe Hauptstraße", "version": 1},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": store_id, "name": "Rewe Hauptstraße", "version": 2, "deletedAt": None}


def test_update_store_with_stale_version_returns_409_with_canonical_state(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    store_id = test_uuid("s1")
    client.post("/api/v1/stores", json={"operationId": test_uuid("op1"), "id": store_id, "name": "Rewe"}, headers=headers)
    client.patch(f"/api/v1/stores/{store_id}", json={"operationId": test_uuid("op2"), "name": "Rewe Hauptstraße", "version": 1}, headers=headers)
    # A second device, still on version 1, retries its own (now stale) update:
    resp = client.patch(
        f"/api/v1/stores/{store_id}",
        json={"operationId": test_uuid("op3"), "name": "Rewe Nebenstraße", "version": 1},
        headers=headers,
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "STALE_VERSION"
    assert body["current"] == {"id": store_id, "name": "Rewe Hauptstraße", "version": 2, "deletedAt": None}


def test_soft_delete_store_hides_it_from_list_but_keeps_it_bumping_version(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    store_id = test_uuid("s1")
    client.post("/api/v1/stores", json={"operationId": test_uuid("op1"), "id": store_id, "name": "Rewe"}, headers=headers)
    resp = client.request(
        "DELETE", f"/api/v1/stores/{store_id}", json={"operationId": test_uuid("op2"), "version": 1}, headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["deletedAt"] is not None
    listing = client.get("/api/v1/stores", headers=headers)
    assert listing.json() == []
