def test_health_reports_zone(api_client, ingress_client):
    api_resp = api_client.get("/healthz")
    assert api_resp.status_code == 200
    assert api_resp.json() == {"status": "ok", "zone": "api"}

    ingress_resp = ingress_client.get("/healthz")
    assert ingress_resp.json()["zone"] == "ingress"
