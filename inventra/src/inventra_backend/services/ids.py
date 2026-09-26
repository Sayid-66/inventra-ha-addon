from __future__ import annotations

from uuid6 import uuid7


def new_id() -> str:
    return str(uuid7())
