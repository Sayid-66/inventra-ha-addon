from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def test_snapshot_first_page_fixes_the_revision_for_later_pages(api_client_with_device):
    client, device = api_client_with_device
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL1"), "id": test_uuid("l1"), "name": "Keller"}, headers=_headers(device))

    first = client.get("/api/v1/snapshot?limit=1", headers=_headers(device))
    body = first.json()
    assert body["dataEpoch"] == 2
    snapshot_revision = body["snapshotRevision"]
    assert len(body["entities"]) == 1
    assert body["nextCursor"] is None  # only one entity exists so far — snapshot already complete

    # A write happens AFTER the snapshot was taken — must not appear if we re-request the same snapshotRevision.
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL2"), "id": test_uuid("l2"), "name": "Speisekammer"}, headers=_headers(device))

    resp = client.get(f"/api/v1/snapshot?snapshot_revision={snapshot_revision}", headers=_headers(device))
    assert len(resp.json()["entities"]) == 1  # still just l1 — l2 is excluded by the fixed snapshotRevision


def test_snapshot_paginates_by_entity_cursor_without_gaps_or_duplicates(api_client_with_device):
    client, device = api_client_with_device
    for i in range(3):
        client.post(
            "/api/v1/locations", json={"operationId": test_uuid(f"opL{i}"), "id": test_uuid(f"l{i}"), "name": f"Ort{i}"}, headers=_headers(device),
        )
    first = client.get("/api/v1/snapshot?limit=1", headers=_headers(device))
    snapshot_revision = first.json()["snapshotRevision"]
    seen_ids = [e["entityId"] for e in first.json()["entities"]]
    cursor = first.json()["nextCursor"]
    while cursor is not None:
        resp = client.get(
            f"/api/v1/snapshot?snapshot_revision={snapshot_revision}&limit=1"
            f"&entity_type_cursor={cursor['entityType']}&entity_id_cursor={cursor['entityId']}",
            headers=_headers(device),
        )
        body = resp.json()
        assert body["dataEpoch"] == 2
        seen_ids += [e["entityId"] for e in body["entities"]]
        cursor = body["nextCursor"]
    assert sorted(seen_ids) == [test_uuid("l1"), test_uuid("l0"), test_uuid("l2")]


def test_snapshot_includes_tombstoned_entities(api_client_with_device):
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    client.request(
        "DELETE", f"/api/v1/locations/{location_id}", json={"operationId": test_uuid("opDel"), "version": 1}, headers=_headers(device),
    )
    resp = client.get("/api/v1/snapshot", headers=_headers(device))
    entities = resp.json()["entities"]
    assert len(entities) == 1
    assert entities[0]["changeKind"] == "DELETE"
    assert entities[0]["snapshot"]["deletedAt"] is not None


def test_snapshot_entity_is_the_full_state_not_a_patch_of_only_the_changed_field(api_client_with_device):
    """Locks in the Task 7 invariant: an UPDATE that changed only `name`
    must still carry every other field (id, version, deletedAt) in its
    change_log snapshot — /snapshot's correctness depends on this."""
    client, device = api_client_with_device
    location_id = test_uuid("l1")
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": location_id, "name": "Keller"}, headers=_headers(device))
    client.patch(
        f"/api/v1/locations/{location_id}", json={"operationId": test_uuid("opU"), "name": "Vorratskeller", "version": 1}, headers=_headers(device),
    )
    resp = client.get("/api/v1/snapshot", headers=_headers(device))
    entity = resp.json()["entities"][0]
    assert entity["snapshot"] == {"id": location_id, "name": "Vorratskeller", "version": 2, "deletedAt": None}


def test_snapshot_pagination_is_stable_and_deterministic_across_repeated_calls(api_client_with_device):
    """The same (snapshotRevision, cursor) must always yield the same page."""
    client, device = api_client_with_device
    for i in range(3):
        client.post(
            "/api/v1/locations", json={"operationId": test_uuid(f"opL{i}"), "id": test_uuid(f"l{i}"), "name": f"Ort{i}"}, headers=_headers(device),
        )
    first_call = client.get("/api/v1/snapshot?limit=1", headers=_headers(device)).json()
    second_call = client.get("/api/v1/snapshot?limit=1", headers=_headers(device)).json()
    assert first_call == second_call


def test_snapshot_does_not_duplicate_unrelated_entities_on_an_unrelated_write(api_client_with_device):
    """A write only produces change_log rows for what it actually
    touched — the snapshot for an unrelated entity must not grow or
    change just because something else was written."""
    client, device = api_client_with_device
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL"), "id": test_uuid("l1"), "name": "Keller"}, headers=_headers(device))
    before = client.get("/api/v1/snapshot", headers=_headers(device)).json()
    client.post("/api/v1/products", json={"operationId": test_uuid("opP"), "id": test_uuid("p1"), "name": "Milch"}, headers=_headers(device))
    after = client.get("/api/v1/snapshot", headers=_headers(device)).json()
    location_entities_before = [e for e in before["entities"] if e["entityType"] == "Location"]
    location_entities_after = [e for e in after["entities"] if e["entityType"] == "Location"]
    assert location_entities_before == location_entities_after  # unaffected by the unrelated Product write


def test_snapshot_identity_and_current_counter(api_client_with_device):
    from uuid import UUID
    client, device = api_client_with_device
    headers = _headers(device)
    empty = client.get("/api/v1/snapshot", headers=headers).json()
    assert UUID(empty["instanceId"]).version == 4
    assert empty["currentRevision"] == 0
    assert empty["entities"] == []
    client.post("/api/v1/locations", json={"operationId": test_uuid("identity-op"), "id": test_uuid("identity-loc"), "name": "Identity"}, headers=headers)
    populated = client.get("/api/v1/snapshot", headers=headers).json()
    assert populated["instanceId"] == empty["instanceId"]
    assert populated["currentRevision"] == 1
    assert len(populated["entities"]) == 1
    historical = client.get("/api/v1/snapshot?snapshot_revision=0", headers=headers).json()
    assert historical["currentRevision"] == 1
    assert historical["snapshotRevision"] == 0
    assert historical["entities"] == []
    future = client.get("/api/v1/snapshot?snapshot_revision=1000", headers=headers).json()
    assert future["currentRevision"] == 1
    assert future["snapshotRevision"] == 1000
    assert future["instanceId"] == empty["instanceId"]
