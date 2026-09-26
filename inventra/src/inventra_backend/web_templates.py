from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request


PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
TEMPLATE_DIR = PACKAGE_DIR / "templates"


def ingress_url(request: Request, path: str) -> str:
    return (getattr(request.state, "ingress_path", "") or "") + "/" + path.lstrip("/")


templates = Jinja2Templates(directory=TEMPLATE_DIR)
templates.env.globals["ingress_url"] = ingress_url
