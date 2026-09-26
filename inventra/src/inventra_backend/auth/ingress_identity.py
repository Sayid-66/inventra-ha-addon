from __future__ import annotations

from fastapi import HTTPException, Request


def require_ingress_identity(request: Request) -> str:
    """Returns the calling HA user's id. Trusted ONLY because this
    dependency refuses to run for any request whose trust_zone is not
    "ingress" — and that zone is only reachable, per Task 9, from the
    documented Supervisor proxy address. An API-zone route must never
    depend on this."""
    if getattr(request.state, "trust_zone", None) != "ingress":
        raise HTTPException(status_code=403, detail="ingress-only endpoint")
    user_id = request.headers.get("X-Remote-User-Id")
    if not user_id:
        raise HTTPException(status_code=401, detail="missing X-Remote-User-Id")
    return user_id
