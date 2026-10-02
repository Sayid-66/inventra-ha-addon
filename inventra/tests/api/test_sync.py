from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def test_sync_from_zero_returns_all_changes_in_one_call(api_client_with_device):
    client, device = api_client_with_device
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": test_uuid("l1"), "name": "Keller"}, headers=_headers(device))
    resp = client.get("/api/v1/sync?since_revision=0", headers=_headers(device))
    assert resp.status_code == 200
    body = resp.json()
    assert body["dataEpoch"] == 2
    assert len(body["changes"]) == 1
    assert body["changes"][0]["entityType"] == "Location"
    assert body["hasMore"] is False
    next_revision = body["nextRevision"]

    resp2 = client.get(f"/api/v1/sync?since_revision={next_revision}", headers=_headers(device))
    assert resp2.json()["changes"] == []
    assert resp2.json()["dataEpoch"] == 2


def test_sync_never_splits_one_revision_even_with_a_small_limit(api_client_with_device):
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    # A purchase of a brand-new product with a brand-new barcode produces 4
    # change_log rows (Product CREATE, Barcode CREATE, PurchaseEvent EVENT,
    # Batch CREATE) under ONE revision — verified against the actual Task 17
    # commit_purchase implementation (barcode "4001" does not exist yet in
    # this test, so it is created on demand alongside the product).
    client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("opP"), "id": test_uuid("e1"), "productId": test_uuid("p1"), "newProduct": {"name": "Milch", "imageUrl": None},
            "barcode": "4001", "locationId": location_id, "quantity": 2, "storeId": None, "pricePerUnitCents": None,
            "mhd": None, "minStock": None, "contentUnitLabel": None, "contentTotal": None,
            "contentBreakdown": None, "timestamp": 1000,
        },
        headers=_headers(device),
    )
    resp = client.get("/api/v1/sync?since_revision=1&limit=2", headers=_headers(device))
    body = resp.json()
    assert body["dataEpoch"] == 2
    revisions_seen = {c["revision"] for c in body["changes"]}
    assert len(revisions_seen) == 1  # exactly one revision, fully delivered, even though limit was 2
    assert len(body["changes"]) == 4
    assert body["hasMore"] is False


def test_sync_pages_across_multiple_revisions_without_gaps(api_client_with_device):
    client, device = api_client_with_device
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL1"), "id": test_uuid("l1"), "name": "Keller"}, headers=_headers(device))
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL2"), "id": test_uuid("l2"), "name": "Speisekammer"}, headers=_headers(device))
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL3"), "id": test_uuid("l3"), "name": "Kueche"}, headers=_headers(device))

    seen_entity_ids: list[str] = []
    since = 0
    for _ in range(10):  # generous upper bound on round-trips
        resp = client.get(f"/api/v1/sync?since_revision={since}&limit=1", headers=_headers(device))
        body = resp.json()
        assert body["dataEpoch"] == 2
        seen_entity_ids += [c["entityId"] for c in body["changes"]]
        since = body["nextRevision"]
        if not body["hasMore"]:
            break
    assert seen_entity_ids == [test_uuid("l1"), test_uuid("l2"), test_uuid("l3")]


def test_sync_identity_and_rewind_contract(api_client_with_device):
    from uuid import UUID
    client, device = api_client_with_device
    headers = _headers(device)
    empty = client.get("/api/v1/sync?since_revision=1000", headers=headers).json()
    assert UUID(empty["instanceId"]).version == 4
    assert empty["currentRevision"] == 0
    assert empty["nextRevision"] == 1000
    assert empty["changes"] == []
    assert empty["hasMore"] is False
    client.post("/api/v1/locations", json={"operationId": test_uuid("identity-op"), "id": test_uuid("identity-loc"), "name": "Identity"}, headers=headers)
    populated = client.get("/api/v1/sync?since_revision=0", headers=headers).json()
    assert populated["instanceId"] == empty["instanceId"]
    assert populated["currentRevision"] == populated["nextRevision"] == 1
    assert len(populated["changes"]) == 1
    rewind = client.get("/api/v1/sync?since_revision=1000", headers=headers).json()
    assert rewind["instanceId"] == empty["instanceId"]
    assert rewind["currentRevision"] == 1
    assert rewind["nextRevision"] == 1000
    assert rewind["changes"] == []
    assert rewind["hasMore"] is False
