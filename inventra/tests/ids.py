from __future__ import annotations

import hashlib


def test_uuid(seed: str) -> str:
    """Deterministic, human-traceable UUIDv7-*shaped* test id derived
    from a stable, readable seed string (e.g. test_uuid("l1")). Not a
    real timestamp-ordered UUIDv7, but always satisfies the production
    schemas' UUIDv7-format Field(pattern=...) validation (correct
    version nibble '7' and variant nibble in 8/9/a/b), and — being a
    pure deterministic function of `seed` — is stable across every
    test run and every call with the same seed, so existing test
    logic and assertions keep working unchanged with the seed as the
    only thing that changed shape."""
    h = hashlib.sha256(seed.encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-7{h[13:16]}-8{h[17:20]}-{h[20:32]}"


test_uuid.__test__ = False
