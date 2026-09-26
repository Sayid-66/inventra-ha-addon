from tests.ids import test_uuid


def _headers(device):
    return {"Authorization": f"Bearer {device.token}"}


def test_snapshot_then_sync_equals_continuous_sync_from_zero(api_client_with_device):
    client, device = api_client_with_device
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL1"), "id": test_uuid("l1"), "name": "Keller"}, headers=_headers(device))
    client.post("/api/v1/locations", json={"operationId": test_uuid("opL2"), "id": test_uuid("l2"), "name": "Speisekammer"}, headers=_headers(device))

    continuous_ids = []
    since = 0
    while True:
        page = client.get(f"/api/v1/sync?since_revision={since}", headers=_headers(device)).json()
        continuous_ids += [c["entityId"] for c in page["changes"]]
        since = page["nextRevision"]
        if not page["hasMore"]:
            break

    snap = client.get("/api/v1/snapshot", headers=_headers(device)).json()
    snapshot_ids = [e["entityId"] for e in snap["entities"]]
    while snap["nextCursor"] is not None:
        c = snap["nextCursor"]
        snap = client.get(
            f"/api/v1/snapshot?snapshot_revision={snap['snapshotRevision']}"
            f"&entity_type_cursor={c['entityType']}&entity_id_cursor={c['entityId']}",
            headers=_headers(device),
        ).json()
        snapshot_ids += [e["entityId"] for e in snap["entities"]]

    after_snapshot = client.get(
        f"/api/v1/sync?since_revision={snap['snapshotRevision']}", headers=_headers(device),
    ).json()

    assert sorted(snapshot_ids) == sorted(continuous_ids)
    assert after_snapshot["changes"] == []  # nothing pending after resuming from snapshotRevision
