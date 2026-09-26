from __future__ import annotations

from html import escape

import segno
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..auth.ingress_identity import require_ingress_identity
from ..config import get_settings
from ..db.base import get_db
from ..services.pairing_service import create_pairing_code

router = APIRouter(tags=["pairing"])


@router.post("/internal/pairing-codes", status_code=201)
def create_pairing_code_route(db: Session = Depends(get_db), user_id: str = Depends(require_ingress_identity)):
    return create_pairing_code(db, user_id, get_settings().pairing_code_ttl_seconds)


@router.get("/pairing", response_class=HTMLResponse)
def pairing_page(db: Session = Depends(get_db), user_id: str = Depends(require_ingress_identity)) -> HTMLResponse:
    pairing = create_pairing_code(db, user_id, get_settings().pairing_code_ttl_seconds)
    code = pairing["code"]
    qr_data_uri = segno.make(code).svg_data_uri(scale=6)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pair Inventra</title>
  <style>
    body {{ font-family: sans-serif; max-width: 32rem; margin: 2rem auto; padding: 0 1rem; text-align: center; }}
    code {{ display: block; margin: 1rem 0; font-size: 1.5rem; word-break: break-all; }}
    img {{ width: min(18rem, 100%); height: auto; }}
  </style>
</head>
<body>
  <main>
    <h1>Pair Inventra</h1>
    <p>Enter this pairing code in the Inventra app or scan the QR code.</p>
    <code>{escape(code)}</code>
    <img src="{escape(qr_data_uri, quote=True)}" alt="QR code containing the pairing code">
    <p>This code expires at {escape(pairing["expiresAt"])}.</p>
  </main>
</body>
</html>"""
    return HTMLResponse(content=html)
