from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .services.image_service import is_uploaded_url


PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
TEMPLATE_DIR = PACKAGE_DIR / "templates"


def ingress_url(request: Request, path: str) -> str:
    return (getattr(request.state, "ingress_path", "") or "") + "/" + path.lstrip("/")


def web_image_src(request: Request, product: dict) -> str | None:
    url = product.get("imageUrl")
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    product_id = product["productId"]
    if is_uploaded_url(product_id, url):
        digest = url.rsplit("=", 1)[1]
        return ingress_url(request, f"produkt/{product_id}/bild") + f"?v={digest}"
    return None


templates = Jinja2Templates(directory=TEMPLATE_DIR)
templates.env.globals["ingress_url"] = ingress_url
templates.env.globals["web_image_src"] = web_image_src
