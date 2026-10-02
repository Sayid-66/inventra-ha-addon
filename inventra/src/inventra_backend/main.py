from __future__ import annotations

import asyncio
from typing import Literal

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from .api import bring as bring_api
from .api import barcodes as barcodes_api
from .api import consume_by_name as consume_by_name_api
from .api import events as events_api
from .api import events_query as events_query_api
from .api import locations as locations_api
from .api import mhd as mhd_api
from .api import pairing_api, pairing_ingress, web_ui
from .api import product_resolver as product_resolver_api
from .api import products as products_api
from .api import snapshot as snapshot_api
from .api import stock as stock_api
from .api import stores as stores_api
from .api import sync as sync_api
from .api import units as units_api
from .config import get_settings
from .db.base import get_engine, init_engine
from .errors import install_error_handlers
from .services import bring_service
from .web_templates import STATIC_DIR

Zone = Literal["api", "ingress"]


def create_app(zone: Zone) -> FastAPI:
    app = FastAPI(title=f"Inventra Backend ({zone})")
    app.state.trust_zone = zone
    init_engine(get_settings().db_path)
    install_error_handlers(app)

    @app.middleware("http")
    async def enforce_trust_zone(request: Request, call_next):
        request.state.trust_zone = zone
        if zone == "ingress":
            settings = get_settings()
            client = request.scope.get("client")
            client_ip = client[0] if client else None
            if client_ip != settings.ingress_proxy_ip:
                return JSONResponse(
                    {"error": {"code": "FORBIDDEN_INGRESS_SOURCE", "message": "request did not arrive via the HA ingress proxy"}},
                    status_code=403,
                )
            ingress_path = request.headers.get("X-Ingress-Path")
            if ingress_path:
                request.state.ingress_path = ingress_path
        return await call_next(request)

    if zone == "api":
        app.include_router(locations_api.router, prefix="/api/v1")
        app.include_router(stores_api.router, prefix="/api/v1")
        app.include_router(units_api.router, prefix="/api/v1")
        app.include_router(products_api.router, prefix="/api/v1")
        app.include_router(bring_api.router, prefix="/api/v1")
        app.include_router(product_resolver_api.router, prefix="/api/v1")
        app.include_router(barcodes_api.router, prefix="/api/v1")
        app.include_router(events_api.router, prefix="/api/v1")
        app.include_router(consume_by_name_api.router, prefix="/api/v1")
        app.include_router(stock_api.router, prefix="/api/v1")
        app.include_router(events_query_api.router, prefix="/api/v1")
        app.include_router(mhd_api.router, prefix="/api/v1")
        app.include_router(sync_api.router, prefix="/api/v1")
        app.include_router(snapshot_api.router, prefix="/api/v1")
        app.include_router(pairing_api.router, prefix="/api/v1")
    else:
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        app.include_router(web_ui.router)
        app.include_router(pairing_ingress.router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "zone": zone}

    return app


async def run() -> None:
    settings = get_settings()
    api_app = create_app("api")
    ingress_app = create_app("ingress")
    api_config = uvicorn.Config(api_app, host="0.0.0.0", port=settings.api_port, log_level="info")
    ingress_config = uvicorn.Config(ingress_app, host="0.0.0.0", port=settings.ingress_port, log_level="info")
    await asyncio.gather(
        uvicorn.Server(api_config).serve(),
        uvicorn.Server(ingress_config).serve(),
        bring_service.run_bring_reconcile_loop(settings.bring_reconcile_interval_seconds, engine=get_engine()),
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
