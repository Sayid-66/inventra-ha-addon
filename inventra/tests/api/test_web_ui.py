import json
import re
from datetime import datetime

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventra_backend.auth.device_token import hash_token
from inventra_backend.config import get_settings
from inventra_backend.db.base import get_engine
from inventra_backend.db.models import (
    BringWatchOrigin,
    BringWatchState,
    BringWatchStateEnum,
    ChangeLog,
    ChangeKind,
    Device,
    Location,
    Product,
    RevisionCounter,
)
from inventra_backend.main import create_app
from tests.ids import test_uuid


INGRESS_HEADERS = {"X-Remote-User-Id": "dennis"}
VENDOR_ASSETS = (
    "htmx-1.9.12.min.js",
    "alpine-3.14.1.min.js",
    "chart-4.5.1.min.js",
)
NAV_PATHS = ("", "bestand", "historie", "geraete", "pairing")
STYLESHEET_ASSET = "style.css"


def _internal_urls(html: str) -> list[str]:
    return re.findall(r'(?:href|src)="([^"]+)"', html)


def _seed_stock_via_api() -> None:
    token = "bestand-test-token"
    with Session(get_engine()) as db:
        db.add(
            Device(
                device_id="bestand-device",
                user_id="dennis",
                device_name="Bestand Test",
                token_hash=hash_token(token),
            )
        )
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    api_client = TestClient(create_app("api"))
    locations = (
        (test_uuid("bestand-l1"), test_uuid("bestand-op-l1"), "Keller"),
        (test_uuid("bestand-l2"), test_uuid("bestand-op-l2"), "Küche"),
    )
    for location_id, operation_id, name in locations:
        response = api_client.post(
            "/api/v1/locations",
            json={"operationId": operation_id, "id": location_id, "name": name},
            headers=headers,
        )
        assert response.status_code == 201

    product_id = test_uuid("bestand-p1")
    purchases = (
        {
            "operationId": test_uuid("bestand-op-p1"),
            "id": test_uuid("bestand-e1"),
            "productId": product_id,
            "newProduct": {
                "name": "Hafermilch",
                "imageUrl": "https://example.invalid/hafermilch.jpg",
            },
            "barcode": "bestand-4001",
            "locationId": locations[0][0],
            "quantity": 2,
            "storeId": None,
            "pricePerUnitCents": None,
            "mhd": "2026-09-10",
            "minStock": 1,
            "contentUnitLabel": None,
            "contentTotal": None,
            "contentBreakdown": None,
            "timestamp": 1000,
        },
        {
            "operationId": test_uuid("bestand-op-p2"),
            "id": test_uuid("bestand-e2"),
            "productId": product_id,
            "newProduct": None,
            "barcode": "bestand-4002",
            "locationId": locations[1][0],
            "quantity": 1,
            "storeId": None,
            "pricePerUnitCents": None,
            "mhd": None,
            "minStock": 1,
            "contentUnitLabel": "ml",
            "contentTotal": 750,
            "contentBreakdown": None,
            "timestamp": 2000,
        },
    )
    for purchase in purchases:
        response = api_client.post("/api/v1/purchases", json=purchase, headers=headers)
        assert response.status_code == 201
    api_client.close()


def _seed_depleted_stock_via_api() -> None:
    token = "historie-test-token"
    with Session(get_engine()) as db:
        db.add(
            Device(
                device_id="historie-device",
                user_id="dennis",
                device_name="Historie Test",
                token_hash=hash_token(token),
            )
        )
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    api_client = TestClient(create_app("api"))
    location_id = test_uuid("historie-l1")
    product_id = test_uuid("historie-p1")

    location_response = api_client.post(
        "/api/v1/locations",
        json={
            "operationId": test_uuid("historie-op-l1"),
            "id": location_id,
            "name": "Vorratsraum",
        },
        headers=headers,
    )
    assert location_response.status_code == 201

    purchase_response = api_client.post(
        "/api/v1/purchases",
        json={
            "operationId": test_uuid("historie-op-p1"),
            "id": test_uuid("historie-e1"),
            "productId": product_id,
            "newProduct": {
                "name": "Aufgebrauchter Reis",
                "imageUrl": "https://example.invalid/reis.jpg",
            },
            "barcode": "historie-4001",
            "locationId": location_id,
            "quantity": 2,
            "storeId": None,
            "pricePerUnitCents": None,
            "mhd": "2027-03-15",
            "minStock": 3,
            "contentUnitLabel": None,
            "contentTotal": None,
            "contentBreakdown": None,
            "timestamp": 1000,
        },
        headers=headers,
    )
    assert purchase_response.status_code == 201

    consumption_response = api_client.post(
        "/api/v1/consumptions",
        json={
            "operationId": test_uuid("historie-op-c1"),
            "id": test_uuid("historie-c1"),
            "productId": product_id,
            "locationId": location_id,
            "stockKind": "STK",
            "quantity": 2,
            "timestamp": 2000,
        },
        headers=headers,
    )
    assert consumption_response.status_code == 201
    api_client.close()


def _seed_product_detail_via_api() -> str:
    token = "produktdetail-test-token"
    with Session(get_engine()) as db:
        db.add(
            Device(
                device_id="produktdetail-device",
                user_id="dennis",
                device_name="Produktdetail Test",
                token_hash=hash_token(token),
            )
        )
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    api_client = TestClient(create_app("api"))
    source_location_id = test_uuid("produktdetail-l1")
    target_location_id = test_uuid("produktdetail-l2")
    product_id = test_uuid("produktdetail-p1")

    for location_id, operation_id, name in (
        (source_location_id, test_uuid("produktdetail-op-l1"), "Keller"),
        (target_location_id, test_uuid("produktdetail-op-l2"), "Küche"),
    ):
        response = api_client.post(
            "/api/v1/locations",
            json={"operationId": operation_id, "id": location_id, "name": name},
            headers=headers,
        )
        assert response.status_code == 201

    events = (
        (
            "/api/v1/purchases",
            {
                "operationId": test_uuid("produktdetail-op-p1"),
                "id": test_uuid("produktdetail-e1"),
                "productId": product_id,
                "newProduct": {
                    "name": "Detail-Nudeln",
                    "imageUrl": "https://example.invalid/nudeln.jpg",
                },
                "barcode": "produktdetail-4001",
                "locationId": source_location_id,
                "quantity": 5,
                "storeId": None,
                "pricePerUnitCents": 249,
                "mhd": "2027-04-30",
                "minStock": 2,
                "contentUnitLabel": None,
                "contentTotal": None,
                "contentBreakdown": None,
                "timestamp": 43_201_000,
            },
        ),
        (
            "/api/v1/consumptions",
            {
                "operationId": test_uuid("produktdetail-op-c1"),
                "id": test_uuid("produktdetail-c1"),
                "productId": product_id,
                "locationId": source_location_id,
                "stockKind": "STK",
                "quantity": 1,
                "timestamp": 43_202_000,
            },
        ),
        (
            "/api/v1/relocations",
            {
                "operationId": test_uuid("produktdetail-op-r1"),
                "id": test_uuid("produktdetail-r1"),
                "productId": product_id,
                "fromLocationId": source_location_id,
                "toLocationId": target_location_id,
                "stockKind": "STK",
                "quantity": 4,
                "timestamp": 43_203_000,
            },
        ),
        (
            "/api/v1/corrections",
            {
                "operationId": test_uuid("produktdetail-op-k1"),
                "id": test_uuid("produktdetail-k1"),
                "productId": product_id,
                "locationId": target_location_id,
                "stockKind": "STK",
                "contentUnitLabel": None,
                "newQuantity": 6,
                "mhdForIncrease": "2027-05-31",
                "timestamp": 43_204_000,
            },
        ),
    )
    for path, payload in events:
        response = api_client.post(path, json=payload, headers=headers)
        assert response.status_code == 201

    api_client.close()
    return product_id


def _seed_price_history_via_api(prices_cents: tuple[int | None, ...]) -> str:
    token = "price-history-test-token"
    with Session(get_engine()) as db:
        db.add(
            Device(
                device_id="price-history-device",
                user_id="dennis",
                device_name="Preishistorie Test",
                token_hash=hash_token(token),
            )
        )
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    api_client = TestClient(create_app("api"))
    location_id = test_uuid("price-history-l1")
    product_id = test_uuid("price-history-p1")

    location_response = api_client.post(
        "/api/v1/locations",
        json={
            "operationId": test_uuid("price-history-op-l1"),
            "id": location_id,
            "name": "Speisekammer",
        },
        headers=headers,
    )
    assert location_response.status_code == 201

    for index, price_cents in enumerate(prices_cents):
        purchase_response = api_client.post(
            "/api/v1/purchases",
            json={
                "operationId": test_uuid(f"price-history-op-p{index}"),
                "id": test_uuid(f"price-history-e{index}"),
                "productId": product_id,
                "newProduct": (
                    {"name": "Preis-Testprodukt", "imageUrl": None}
                    if index == 0
                    else None
                ),
                "barcode": f"price-history-{index}",
                "locationId": location_id,
                "quantity": 1,
                "storeId": None,
                "pricePerUnitCents": price_cents,
                "mhd": None,
                "minStock": None,
                "contentUnitLabel": None,
                "contentTotal": None,
                "contentBreakdown": None,
                "timestamp": (index + 1) * 86_400_000,
            },
            headers=headers,
        )
        assert purchase_response.status_code == 201

    api_client.close()
    return product_id


@pytest.mark.parametrize("asset", VENDOR_ASSETS)
def test_static_assets_are_served_from_ingress_zone(ingress_client, asset):
    asset_path = f"/static/vendor/{asset}"

    ingress_response = ingress_client.get(asset_path)

    assert ingress_response.status_code == 200
    assert len(ingress_response.content) > 1_000


@pytest.mark.parametrize("asset", VENDOR_ASSETS)
def test_static_assets_served_with_ingress_path_header_present(
    ingress_client, asset
):
    response = ingress_client.get(
        f"/static/vendor/{asset}",
        headers={"X-Ingress-Path": "/api/hassio_ingress/faketoken"},
    )

    assert response.status_code == 200
    assert response.content


def test_static_asset_is_not_registered_in_api_zone(api_client):
    api_response = api_client.get("/static/vendor/htmx-1.9.12.min.js")

    assert api_response.status_code == 404


def test_base_stylesheet_is_served_and_rendered_from_ingress_zone(ingress_client):
    asset_response = ingress_client.get(f"/static/css/{STYLESHEET_ASSET}")
    dashboard_response = ingress_client.get("/", headers=INGRESS_HEADERS)

    assert asset_response.status_code == 200
    assert asset_response.headers["content-type"].startswith("text/css")
    assert asset_response.content
    assert dashboard_response.status_code == 200
    assert f'href="/static/css/{STYLESHEET_ASSET}"' in dashboard_response.text


def test_dashboard_renders_assets_and_navigation_without_ingress_prefix(ingress_client):
    response = ingress_client.get("/", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert "Inventra Dashboard" in response.text
    for asset in VENDOR_ASSETS:
        assert f'/static/vendor/{asset}' in response.text
    for path in NAV_PATHS:
        assert f'href="/{path}"' in response.text

    urls = _internal_urls(response.text)
    assert set(urls) == {
        "/",
        "/bestand",
        "/historie",
        "/geraete",
        "/pairing",
        f"/static/css/{STYLESHEET_ASSET}",
        *(f"/static/vendor/{asset}" for asset in VENDOR_ASSETS),
    }


def test_dashboard_shows_empty_database_status(ingress_client):
    response = ingress_client.get("/", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert "Aktuelle Revision" in response.text
    assert "Noch keine Aktivität" in response.text
    assert "Aktive Geräte" in response.text
    assert re.search(r"Aktuelle Revision</dt>\s*<dd>0</dd>", response.text)
    assert re.search(r"Aktive Geräte</dt>\s*<dd>0</dd>", response.text)


def test_dashboard_shows_seeded_database_status(ingress_client):
    last_activity_at = datetime(2026, 9, 6, 14, 23, 45)
    revoked_at = datetime(2026, 9, 6, 15, 0, 0)
    with Session(get_engine()) as db:
        revision_counter = db.get(RevisionCounter, 0)
        assert revision_counter is not None
        revision_counter.current_revision = 17
        db.add(
            ChangeLog(
                revision=17,
                entity_type="Product",
                entity_id="product-1",
                change_kind=ChangeKind.UPDATE,
                snapshot="{}",
                created_at=last_activity_at,
            )
        )
        db.add_all(
            [
                Device(
                    device_id="active-device",
                    user_id="dennis",
                    device_name="Active",
                    token_hash="active-token-hash",
                ),
                Device(
                    device_id="revoked-device",
                    user_id="dennis",
                    device_name="Revoked",
                    token_hash="revoked-token-hash",
                    revoked_at=revoked_at,
                ),
            ]
        )
        db.commit()

    response = ingress_client.get("/", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert re.search(r"Aktuelle Revision</dt>\s*<dd>17</dd>", response.text)
    assert "2026-09-06 14:23:45" in response.text
    assert re.search(r"Aktive Geräte</dt>\s*<dd>1</dd>", response.text)


def test_dashboard_is_not_registered_in_api_zone(api_client):
    response = api_client.get("/", headers=INGRESS_HEADERS)

    assert response.status_code == 404


def test_bestand_is_available_in_ingress_zone(ingress_client):
    response = ingress_client.get("/bestand", headers=INGRESS_HEADERS)

    assert response.status_code == 200


def test_bestand_is_not_registered_in_api_zone(api_client):
    response = api_client.get("/bestand", headers=INGRESS_HEADERS)

    assert response.status_code == 404


def test_bestand_shows_empty_state(ingress_client):
    response = ingress_client.get("/bestand", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert "Kein Produkt auf Lager." in response.text


def test_bestand_renders_seeded_stock_summary(ingress_client):
    _seed_stock_via_api()

    response = ingress_client.get("/bestand", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert "Hafermilch" in response.text
    assert 'src="https://example.invalid/hafermilch.jpg"' in response.text
    assert re.search(r"Gesamtbestand \(STK\)</dt>\s*<dd>2</dd>", response.text)
    assert re.search(r"Gesamtinhalt</dt>\s*<dd>750 ml</dd>", response.text)
    assert "Keller: 2 STK" in response.text
    assert "Küche: 750 ml" in response.text
    assert "2026-09-10" in response.text
    assert re.search(r"Mindestbestand</dt>\s*<dd>1</dd>", response.text)
    assert f'href="/produkt/{test_uuid("bestand-p1")}"' in response.text


def test_historie_is_available_in_ingress_zone(ingress_client):
    response = ingress_client.get("/historie", headers=INGRESS_HEADERS)

    assert response.status_code == 200


def test_historie_is_not_registered_in_api_zone(api_client):
    response = api_client.get("/historie", headers=INGRESS_HEADERS)

    assert response.status_code == 404


def test_historie_shows_empty_state(ingress_client):
    response = ingress_client.get("/historie", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert "Keine Produkte in der Historie." in response.text


def test_historie_does_not_render_product_with_current_stock(ingress_client):
    _seed_stock_via_api()

    response = ingress_client.get("/historie", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert "Hafermilch" not in response.text


def test_historie_renders_depleted_product_but_bestand_does_not(ingress_client):
    _seed_depleted_stock_via_api()

    historie_response = ingress_client.get("/historie", headers=INGRESS_HEADERS)
    bestand_response = ingress_client.get("/bestand", headers=INGRESS_HEADERS)

    assert historie_response.status_code == 200
    assert bestand_response.status_code == 200
    assert "Aufgebrauchter Reis" in historie_response.text
    assert 'src="https://example.invalid/reis.jpg"' in historie_response.text
    assert re.search(r"Gesamtbestand \(STK\)</dt>\s*<dd>0</dd>", historie_response.text)
    assert re.search(r"Mindestbestand</dt>\s*<dd>3</dd>", historie_response.text)
    assert f'href="/produkt/{test_uuid("historie-p1")}"' in historie_response.text
    assert "Aufgebrauchter Reis" not in bestand_response.text


def test_geraete_is_available_only_in_ingress_zone(ingress_client):
    ingress_response = ingress_client.get("/geraete", headers=INGRESS_HEADERS)
    api_response = TestClient(create_app("api")).get(
        "/geraete", headers=INGRESS_HEADERS
    )

    assert ingress_response.status_code == 200
    assert api_response.status_code == 404


def test_setting_default_location_persists_and_shows_on_geraete_page(
    ingress_client,
):
    with Session(get_engine()) as db:
        db.add(Location(id="l1", name="Küche", normalized_name="kueche", version=1))
        db.add(
            Device(
                device_id="d1",
                user_id="u1",
                device_name="HA",
                token_hash="default-location-token-hash",
            )
        )
        db.commit()

    response = ingress_client.post(
        "/geraete/d1/default-location",
        data={"locationId": "l1"},
        headers={"X-Remote-User-Id": "u1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as db:
        device = db.get(Device, "d1")
        assert device is not None
        assert device.default_location_id == "l1"

    page = ingress_client.get(
        "/geraete",
        headers={"X-Remote-User-Id": "u1"},
    )
    assert "Küche" in page.text


@pytest.mark.parametrize("location_id", ["", "   "])
def test_clearing_default_location_with_empty_value(ingress_client, location_id):
    with Session(get_engine()) as db:
        db.add(Location(id="l1", name="Küche", normalized_name="kueche", version=1))
        db.flush()
        db.add(
            Device(
                device_id="d1",
                user_id="u1",
                device_name="HA",
                token_hash="clear-default-location-token-hash",
                default_location_id="l1",
            )
        )
        db.commit()

    response = ingress_client.post(
        "/geraete/d1/default-location",
        data={"locationId": location_id},
        headers={"X-Remote-User-Id": "u1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as db:
        device = db.get(Device, "d1")
        assert device is not None
        assert device.default_location_id is None


def test_geraete_renders_only_current_users_devices(ingress_client):
    with Session(get_engine()) as db:
        db.add_all(
            [
                Device(
                    device_id="dennis-revoked-device",
                    user_id="dennis",
                    device_name="Dennis Tablet",
                    token_hash="dennis-revoked-token-hash",
                    created_at=datetime(2026, 9, 1, 10, 30),
                    revoked_at=datetime(2026, 9, 5, 18, 45),
                ),
                Device(
                    device_id="other-users-device",
                    user_id="other-user",
                    device_name="Fremdes Gerät",
                    token_hash="other-users-token-hash",
                    created_at=datetime(2026, 8, 1, 8, 0),
                    last_seen_at=datetime(2026, 9, 6, 16, 0),
                ),
            ]
        )
        db.commit()

    response = ingress_client.get("/geraete", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert "Dennis Tablet" in response.text
    assert "2026-09-01 10:30" in response.text
    assert "Widerrufen am 2026-09-05 18:45" in response.text
    assert "Noch nie" in response.text
    assert "Fremdes Gerät" not in response.text
    assert "2026-09-06 16:00" not in response.text


def test_geraete_shows_empty_state_for_user_without_devices(ingress_client):
    response = ingress_client.get(
        "/geraete",
        headers={"X-Remote-User-Id": "user-without-devices"},
    )

    assert response.status_code == 200
    assert "Keine Geräte gekoppelt." in response.text


def test_geraete_revoke_sets_timestamp_redirects_and_removes_control(ingress_client):
    device_id = "revoke-active-device"
    with Session(get_engine()) as db:
        db.add(
            Device(
                device_id=device_id,
                user_id="dennis",
                device_name="Revoke Tablet",
                token_hash="revoke-active-token-hash",
            )
        )
        db.commit()

    initial_response = ingress_client.get("/geraete", headers=INGRESS_HEADERS)

    assert initial_response.status_code == 200
    assert f'action="/geraete/{device_id}/revoke"' in initial_response.text
    assert "Gerät widerrufen" in initial_response.text
    assert "Wirklich widerrufen?" in initial_response.text
    assert "Ja, widerrufen" in initial_response.text
    assert "Abbrechen" in initial_response.text
    assert 'x-data="{ confirming: false }"' in initial_response.text

    revoke_response = ingress_client.post(
        f"/geraete/{device_id}/revoke",
        headers=INGRESS_HEADERS,
        follow_redirects=False,
    )

    assert revoke_response.status_code == 303
    assert revoke_response.headers["location"] == "/geraete"
    with Session(get_engine()) as db:
        revoked_at = db.get(Device, device_id).revoked_at
    assert revoked_at is not None

    rendered_response = ingress_client.get("/geraete", headers=INGRESS_HEADERS)
    assert f"Widerrufen am {revoked_at:%Y-%m-%d %H:%M}" in rendered_response.text
    assert f'action="/geraete/{device_id}/revoke"' not in rendered_response.text
    assert "Gerät widerrufen" not in rendered_response.text


def test_geraete_revoke_returns_404_without_modifying_another_users_device(
    ingress_client,
):
    device_id = "other-user-revoke-device"
    with Session(get_engine()) as db:
        db.add(
            Device(
                device_id=device_id,
                user_id="other-user",
                device_name="Fremdes Tablet",
                token_hash="other-user-revoke-token-hash",
            )
        )
        db.commit()

    response = ingress_client.post(
        f"/geraete/{device_id}/revoke",
        headers=INGRESS_HEADERS,
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Gerät nicht gefunden" in response.text
    with Session(get_engine()) as db:
        assert db.get(Device, device_id).revoked_at is None


def test_geraete_revoke_returns_404_for_unknown_device(ingress_client):
    response = ingress_client.post(
        "/geraete/unknown-revoke-device/revoke",
        headers=INGRESS_HEADERS,
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Gerät nicht gefunden" in response.text


def test_geraete_revoke_is_idempotent_and_keeps_original_timestamp(ingress_client):
    device_id = "idempotent-revoke-device"
    with Session(get_engine()) as db:
        db.add(
            Device(
                device_id=device_id,
                user_id="dennis",
                device_name="Idempotent Tablet",
                token_hash="idempotent-revoke-token-hash",
            )
        )
        db.commit()

    first_response = ingress_client.post(
        f"/geraete/{device_id}/revoke",
        headers=INGRESS_HEADERS,
        follow_redirects=False,
    )
    with Session(get_engine()) as db:
        first_revoked_at = db.get(Device, device_id).revoked_at

    second_response = ingress_client.post(
        f"/geraete/{device_id}/revoke",
        headers=INGRESS_HEADERS,
        follow_redirects=False,
    )
    with Session(get_engine()) as db:
        second_revoked_at = db.get(Device, device_id).revoked_at

    assert first_response.status_code == 303
    assert second_response.status_code == 303
    assert first_revoked_at is not None
    assert second_revoked_at == first_revoked_at


def test_geraete_revoke_is_available_only_in_ingress_zone(ingress_client):
    device_id = "ingress-only-revoke-device"
    with Session(get_engine()) as db:
        db.add(
            Device(
                device_id=device_id,
                user_id="dennis",
                device_name="Ingress-only Tablet",
                token_hash="ingress-only-revoke-token-hash",
            )
        )
        db.commit()

    ingress_response = ingress_client.post(
        f"/geraete/{device_id}/revoke",
        headers=INGRESS_HEADERS,
        follow_redirects=False,
    )
    api_response = TestClient(create_app("api")).post(
        f"/geraete/{device_id}/revoke",
        headers=INGRESS_HEADERS,
    )

    assert ingress_response.status_code == 303
    assert api_response.status_code == 404


def test_product_detail_is_available_only_in_ingress_zone(ingress_client):
    product_id = _seed_product_detail_via_api()

    ingress_response = ingress_client.get(f"/produkt/{product_id}", headers=INGRESS_HEADERS)
    api_response = TestClient(create_app("api")).get(
        f"/produkt/{product_id}", headers=INGRESS_HEADERS
    )

    assert ingress_response.status_code == 200
    assert "Detail-Nudeln" in ingress_response.text
    assert api_response.status_code == 404


def test_product_detail_returns_html_404_for_unknown_product(ingress_client):
    response = ingress_client.get(
        f"/produkt/{test_uuid('unknown-product')}",
        headers=INGRESS_HEADERS,
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Produkt nicht gefunden" in response.text


def test_produktdetail_shows_bring_watch_state(ingress_client):
    with Session(get_engine()) as db:
        db.add(Product(id="p1", name="Wasser", version=1, min_stock=3))
        db.flush()
        db.add(
            BringWatchState(
                product_id="p1",
                state=BringWatchStateEnum.LOCKED_PURCHASED,
                origin=BringWatchOrigin.INVENTRA_CREATED,
                bring_item_name="Wasser",
                bring_uid="u1",
                lock_reason="completed",
                retry_count=0,
                last_error="Bring! request failed",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        db.commit()

    response = ingress_client.get("/produkt/p1", headers={"X-Remote-User-Id": "u1"})

    assert response.status_code == 200
    assert "LOCKED_PURCHASED" in response.text or "Gesperrt" in response.text
    assert "completed" in response.text
    assert "Bring! request failed" in response.text


def test_produktdetail_without_bring_watch_row_still_renders(ingress_client):
    with Session(get_engine()) as db:
        db.add(Product(id="p1", name="Wasser", version=1))
        db.commit()

    response = ingress_client.get("/produkt/p1", headers={"X-Remote-User-Id": "u1"})

    assert response.status_code == 200


def test_product_detail_renders_overview_and_all_event_types(ingress_client):
    product_id = _seed_product_detail_via_api()

    response = ingress_client.get(f"/produkt/{product_id}", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert 'src="https://example.invalid/nudeln.jpg"' in response.text
    assert re.search(r"Gesamtbestand \(STK\)</dt>\s*<dd>6</dd>", response.text)
    assert "Küche: 6 STK" in response.text
    assert "2027-04-30" in response.text
    assert "2027-05-31" in response.text
    assert re.search(r"Mindestbestand</dt>\s*<dd>2</dd>", response.text)

    event_blocks = {
        event_type: re.search(
            rf'<article data-event-type="{event_type}">(.*?)</article>',
            response.text,
            re.DOTALL,
        ).group(1)
        for event_type in ("PURCHASE", "CONSUMPTION", "RELOCATION", "CORRECTION")
    }
    assert "Einlagerung" in event_blocks["PURCHASE"]
    assert "Preis pro Einheit</dt><dd>2.49 €" in event_blocks["PURCHASE"]
    assert "MHD</dt><dd>2027-04-30" in event_blocks["PURCHASE"]
    assert "Entnahme" in event_blocks["CONSUMPTION"]
    assert re.search(r"Menge</dt><dd>1 STK", event_blocks["CONSUMPTION"])
    assert "Umlagerung" in event_blocks["RELOCATION"]
    assert re.search(r"Ausgang</dt><dd>Keller", event_blocks["RELOCATION"])
    assert re.search(r"Ziel</dt><dd>Küche", event_blocks["RELOCATION"])
    assert "Korrektur" in event_blocks["CORRECTION"]
    assert re.search(r"Alter Bestand</dt><dd>4 STK", event_blocks["CORRECTION"])
    assert re.search(r"Neuer Bestand</dt><dd>6 STK", event_blocks["CORRECTION"])
    assert all("1970-01-01" in block for block in event_blocks.values())

    assert "Preis pro Einheit" not in event_blocks["CONSUMPTION"]
    assert "Ausgang" not in event_blocks["PURCHASE"]
    assert "Alter Bestand" not in event_blocks["RELOCATION"]
    assert "Menge" not in event_blocks["CORRECTION"]


def test_product_detail_renders_price_stats_and_chronological_chart_data(ingress_client):
    product_id = _seed_price_history_via_api((99, None, 129, 117))

    response = ingress_client.get(f"/produkt/{product_id}", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert re.search(r"Günstigster</dt>\s*<dd>0.99 €</dd>", response.text)
    assert re.search(r"Durchschnitt</dt>\s*<dd>1.15 €</dd>", response.text)
    assert re.search(r"Teuerster</dt>\s*<dd>1.29 €</dd>", response.text)
    assert 'id="price-history-chart"' in response.text

    chart_data_match = re.search(r"const priceData = (\[.*\]);", response.text)
    assert chart_data_match is not None
    chart_data = json.loads(chart_data_match.group(1))
    assert [entry["priceCents"] for entry in chart_data] == [99, 129, 117]
    assert all(re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", entry["date"]) for entry in chart_data)


def test_product_detail_shows_empty_price_state_for_only_unpriced_purchases(ingress_client):
    product_id = _seed_price_history_via_api((None,))

    response = ingress_client.get(f"/produkt/{product_id}", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    assert "Noch keine Preisdaten." in response.text
    assert 'id="price-history-chart"' not in response.text
    assert "const priceData" not in response.text
    assert "Günstigster" not in response.text


@pytest.mark.anyio
async def test_dashboard_urls_use_ingress_path_prefix():
    app = create_app("ingress")
    transport = httpx.ASGITransport(
        app=app,
        client=(get_settings().ingress_proxy_ip, 12345),
    )
    prefix = "/api/hassio_ingress/abc123"
    headers = {**INGRESS_HEADERS, "X-Ingress-Path": prefix}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers=headers)
        unprefixed_response = await client.get("/", headers=INGRESS_HEADERS)

    assert response.status_code == 200
    urls = _internal_urls(response.text)
    expected_urls = {
        f"{prefix}/",
        *(f"{prefix}/{path}" for path in NAV_PATHS if path),
        f"{prefix}/static/css/{STYLESHEET_ASSET}",
        *(f"{prefix}/static/vendor/{asset}" for asset in VENDOR_ASSETS),
    }
    assert set(urls) == expected_urls
    assert f'{prefix}/static/vendor/htmx-1.9.12.min.js' in response.text
    assert 'src="/static/vendor/htmx-1.9.12.min.js"' not in response.text
    assert unprefixed_response.status_code == 200
    assert 'src="/static/vendor/htmx-1.9.12.min.js"' in unprefixed_response.text
    assert prefix not in unprefixed_response.text


@pytest.mark.anyio
async def test_api_zone_ignores_ingress_path_header():
    app = create_app("api")

    @app.get("/test-root-path")
    def show_root_path(request: Request):
        return {"rootPath": request.scope.get("root_path", "")}

    transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/test-root-path",
            headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"},
        )

    assert response.status_code == 200
    assert response.json() == {"rootPath": ""}


@pytest.mark.parametrize("deleted", [False, True])
def test_default_location_rejects_unknown_or_deleted_location(ingress_client, deleted):
    with Session(get_engine()) as db:
        if deleted:
            db.add(Location(id="invalid-location", name="Deleted", normalized_name="deleted",
                            version=1, deleted_at=datetime.utcnow()))
            db.flush()
        db.add(Device(device_id="d1", user_id="u1", device_name="HA",
                      token_hash="invalid-default-location-token-hash"))
        db.commit()

    response = ingress_client.post(
        "/geraete/d1/default-location", data={"locationId": "invalid-location"},
        headers={"X-Remote-User-Id": "u1"}, follow_redirects=False,
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    with Session(get_engine()) as db:
        assert db.get(Device, "d1").default_location_id is None
