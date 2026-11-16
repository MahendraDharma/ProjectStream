"""Request attestation seals — the ProjectStream equivalent of flystream.net's
"X-FS-Seal" + /api/guard-attest flow (learning implementation).

A seal is a short-lived HMAC token binding a client id to an expiry. Sensitive
endpoints (playback-search) require a valid X-FS-Seal header, mirroring how
flystream.net raises the cost of headless abuse of its APIs.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

_TTL_SECONDS = 600

_secret: bytes | None = None


def _get_secret() -> bytes:
    """Process-wide secret: PS_SECRET env when set, else random per boot."""
    global _secret
    if _secret is None:
        import os

        env = os.getenv("PS_SECRET", "")
        _secret = env.encode() if env else secrets.token_bytes(32)
    return _secret


def reset_secret() -> None:
    """Drop the cached secret — used by the test suite."""
    global _secret
    _secret = None


def issue(client_id: str) -> dict:
    """Create a seal for a client id: base64(payload).signature."""
    expires = int(time.time()) + _TTL_SECONDS
    nonce = secrets.token_hex(8)
    payload = f"{client_id}|{expires}|{nonce}"
    sig = hmac.new(_get_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return {"seal": f"{payload}|{sig}", "expires": expires, "client_id": client_id}


def verify(seal: str | None, client_id: str | None = None) -> bool:
    """Check a seal's signature and expiry; optionally bind it to a client id."""
    if not seal:
        return False

    parts = seal.split("|")
    if len(parts) != 4:
        return False

    seal_client, raw_exp, nonce, sig = parts

    if client_id is not None and not hmac.compare_digest(seal_client, client_id):
        return False

    payload = f"{seal_client}|{raw_exp}|{nonce}"
    expected = hmac.new(_get_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False

    try:
        expires = int(raw_exp)
    except ValueError:
        return False

    return time.time() <= expires
