from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.ingress_identity import require_ingress_identity
from ..db.base import get_db
from ..db.models import BringWatchState, Device, Location
from ..services.dashboard_service import get_dashboard_status
from ..services.stock_query_service import get_product_detail, list_current_stock, list_stock_history
from ..web_templates import ingress_url, templates


router = APIRouter(tags=["web-ui"])


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    _user_id: str = Depends(require_ingress_identity),
) -> HTMLResponse:
    status = get_dashboard_status(db)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "current_revision": status.current_revision,
            "last_activity_at": status.last_activity_at,
            "active_device_count": status.active_device_count,
        },
    )


@router.get("/bestand", response_class=HTMLResponse)
def bestand(
    request: Request,
    db: Session = Depends(get_db),
    _user_id: str = Depends(require_ingress_identity),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="bestand.html",
        context={"products": list_current_stock(db)},
    )


@router.get("/historie", response_class=HTMLResponse)
def historie(
    request: Request,
    db: Session = Depends(get_db),
    _user_id: str = Depends(require_ingress_identity),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="historie.html",
        context={"products": list_stock_history(db)},
    )


def _format_timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M")


def _format_chart_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%d.%m.%Y")


def _format_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


@router.get("/geraete", response_class=HTMLResponse)
def geraete(
    request: Request,
    db: Session = Depends(get_db),
    user_id: str = Depends(require_ingress_identity),
) -> HTMLResponse:
    devices = db.scalars(
        select(Device)
        .where(Device.user_id == user_id)
        .order_by(Device.created_at, Device.device_id)
    ).all()
    locations = db.scalars(
        select(Location)
        .where(Location.deleted_at.is_(None))
        .order_by(Location.name, Location.id)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="geraete.html",
        context={
            "devices": [
                {
                    "device_id": device.device_id,
                    "device_name": device.device_name,
                    "created_at": _format_datetime(device.created_at),
                    "revoked_at": (
                        _format_datetime(device.revoked_at)
                        if device.revoked_at is not None
                        else None
                    ),
                    "last_seen_at": (
                        _format_datetime(device.last_seen_at)
                        if device.last_seen_at is not None
                        else None
                    ),
                    "default_location_id": device.default_location_id,
                }
                for device in devices
            ],
            "locations": [
                {"id": location.id, "name": location.name}
                for location in locations
            ],
        },
    )


@router.post("/geraete/{device_id}/revoke")
def revoke_geraet(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user_id: str = Depends(require_ingress_identity),
) -> Response:
    device = db.get(Device, device_id)
    if device is None or device.user_id != user_id:
        return HTMLResponse(
            "<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\">"
            "<title>Gerät nicht gefunden – Inventra</title></head>"
            "<body><main><h1>Gerät nicht gefunden</h1></main></body></html>",
            status_code=404,
        )

    if device.revoked_at is None:
        device.revoked_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(
        url=ingress_url(request, "geraete"),
        status_code=303,
    )


@router.post("/geraete/{device_id}/default-location")
async def set_default_location(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user_id: str = Depends(require_ingress_identity),
) -> Response:
    device = db.get(Device, device_id)
    if device is None or device.user_id != user_id:
        return HTMLResponse(
            "<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\">"
            "<title>Gerät nicht gefunden – Inventra</title></head>"
            "<body><main><h1>Gerät nicht gefunden</h1></main></body></html>",
            status_code=404,
        )

    form = parse_qs((await request.body()).decode(), keep_blank_values=True)
    location_id = form.get("locationId", [""])[0]
    device.default_location_id = location_id or None
    db.commit()
    return RedirectResponse(
        url=ingress_url(request, "geraete"),
        status_code=303,
    )


@router.get("/produkt/{product_id}", response_class=HTMLResponse)
def produktdetail(
    product_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user_id: str = Depends(require_ingress_identity),
) -> HTMLResponse:
    product = get_product_detail(db, product_id)
    if product is None:
        return HTMLResponse(
            "<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\">"
            "<title>Produkt nicht gefunden – Inventra</title></head>"
            "<body><main><h1>Produkt nicht gefunden</h1></main></body></html>",
            status_code=404,
        )

    product["history"] = [
        {**event, "formattedTimestamp": _format_timestamp(event["timestamp"])}
        for event in product["history"]
    ]
    priced_purchases = [
        purchase
        for purchase in product["purchases"]
        if purchase["pricePerUnitCents"] is not None
    ]
    prices_cents = [purchase["pricePerUnitCents"] for purchase in priced_purchases]
    cheapest_price_cents = min(prices_cents, default=None)
    average_price_cents = (
        (sum(prices_cents) + len(prices_cents) // 2) // len(prices_cents)
        if prices_cents
        else None
    )
    most_expensive_price_cents = max(prices_cents, default=None)
    chart_data = [
        {
            "date": _format_chart_date(purchase["timestamp"]),
            "priceCents": purchase["pricePerUnitCents"],
        }
        for purchase in priced_purchases
    ]
    watch = db.get(BringWatchState, product_id)
    return templates.TemplateResponse(
        request=request,
        name="produktdetail.html",
        context={
            "product": product,
            "cheapest_price_cents": cheapest_price_cents,
            "average_price_cents": average_price_cents,
            "most_expensive_price_cents": most_expensive_price_cents,
            "chart_data": chart_data,
            "bring_watch": watch,
        },
    )
