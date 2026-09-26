from tests.ids import test_uuid


def test_assign_barcode_to_existing_product(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    product_id = test_uuid("p1")
    client.post("/api/v1/products", json={"operationId": test_uuid("op1"), "id": product_id, "name": "Milch"}, headers=headers)
    resp = client.post(
        "/api/v1/barcodes", json={"operationId": test_uuid("op2"), "code": "4001", "productId": product_id}, headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json() == {"code": "4001", "productId": product_id, "version": 1, "deletedAt": None}


def test_assign_duplicate_barcode_code_rejected(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    product_id = test_uuid("p1")
    other_product_id = test_uuid("p2")
    client.post("/api/v1/products", json={"operationId": test_uuid("op1"), "id": product_id, "name": "Milch"}, headers=headers)
    client.post("/api/v1/products", json={"operationId": test_uuid("op2"), "id": other_product_id, "name": "Joghurt"}, headers=headers)
    client.post("/api/v1/barcodes", json={"operationId": test_uuid("op3"), "code": "4001", "productId": product_id}, headers=headers)
    resp = client.post(
        "/api/v1/barcodes", json={"operationId": test_uuid("op4"), "code": "4001", "productId": other_product_id}, headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_ENTITY"


def test_soft_delete_barcode(api_client_with_device):
    client, device = api_client_with_device
    headers = {"Authorization": f"Bearer {device.token}"}
    product_id = test_uuid("p1")
    client.post("/api/v1/products", json={"operationId": test_uuid("op1"), "id": product_id, "name": "Milch"}, headers=headers)
    client.post("/api/v1/barcodes", json={"operationId": test_uuid("op2"), "code": "4001", "productId": product_id}, headers=headers)
    resp = client.request(
        "DELETE", "/api/v1/barcodes/4001", json={"operationId": test_uuid("op3"), "version": 1}, headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["deletedAt"] is not None
