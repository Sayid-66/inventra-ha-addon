from tests.ids import test_uuid


def test_create_location_with_malformed_id_rejected(api_client_with_device):
    client, device = api_client_with_device
    resp = client.post(
        "/api/v1/locations",
        json={"operationId": test_uuid("op1"), "id": "not-a-uuid", "name": "Keller"},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 422


def test_update_location_with_malformed_path_id_rejected(api_client_with_device):
    client, device = api_client_with_device
    resp = client.patch(
        "/api/v1/locations/not-a-uuid",
        json={"operationId": test_uuid("op1"), "name": "Keller", "version": 1},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 422


def test_update_product_with_malformed_path_id_rejected(api_client_with_device):
    client, device = api_client_with_device
    resp = client.patch(
        "/api/v1/products/not-a-uuid",
        json={"operationId": test_uuid("op1"), "name": "Milch", "version": 1},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 422


def test_update_store_with_malformed_path_id_rejected(api_client_with_device):
    client, device = api_client_with_device
    resp = client.patch(
        "/api/v1/stores/not-a-uuid",
        json={"operationId": test_uuid("op1"), "name": "Rewe", "version": 1},
        headers={"Authorization": f"Bearer {device.token}"},
    )
    assert resp.status_code == 422


def test_reusing_a_location_id_for_a_product_is_allowed_across_types(api_client_with_device):
    """IDs are scoped per entity type (each table has its own primary
    key) — reusing the same UUID string for a Location and a Product
    is not a conflict, since they are different entity types with
    independent identity spaces. This documents that decision rather
    than asserting a rejection that the spec never actually required."""
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    shared_id = test_uuid("shared-cross-type-id")
    client.post("/api/v1/locations", json={"operationId": test_uuid("op1"), "id": shared_id, "name": "Keller"}, headers=headers)
    resp = client.post(
        "/api/v1/products",
        json={"operationId": test_uuid("op2"), "id": shared_id, "name": "Milch"},
        headers=headers,
    )
    assert resp.status_code == 201
