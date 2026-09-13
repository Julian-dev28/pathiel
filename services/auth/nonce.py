"""Stateless login nonces, for deployments with no durable disk.

WHAT THIS IS FOR, AND WHAT IT COSTS

`AuthStore` mints a nonce, stores its hash, and burns it on use. That is the
correct design and it is what the doctrine in `siwe.py` describes: without
single-use nonces, one captured signature is a permanent password.

It needs somewhere to write. On a serverless host the database is SQLite in
/tmp, which is per-instance and wiped on cold start — so `/auth/nonce` and
`/auth/verify` routinely land on different instances, the nonce is not there,
and a legitimate sign-in is rejected. It fails closed, which is safe and
useless: the operator sees "Error verifying signature, please retry!" on a
signature that was never wrong.

This module trades the burn for an HMAC. A nonce is `<random>.<expiry>.<mac>`,
verifiable by any instance holding the same secret, stored nowhere.

THE TRADE, STATED PLAINLY

Single-use goes away. Within its TTL, a captured (message, signature) pair can
be replayed to open a second session. Three things bound that:

  - The TTL here is 120s, not the store's 600s. A replay window is a window.
  - The signed message carries `Expiration Time` and `siwe.verify` enforces it,
    so the signature dies on its own schedule too.
  - The domain is inside the signed bytes, so the pair cannot be harvested by
    another site in the first place — capture means reading the user's TLS
    session or running code in their browser, at which point a replayed login
    is not the worst thing happening.

It is still a downgrade, so it is OFF by default and must be asked for by name:
`PATHIA_AUTH_STATELESS_NONCE=1`. A deployment with a durable volume — the Fly
box, any real install — keeps burned nonces and should never set it. The public
demo sets it because its alternative is not "stronger replay protection", it is
"sign-in works at random".
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from hashlib import sha256
from typing import Optional

# Deliberately a fifth of the store's 600s. The stored nonce can afford a long
# life because it can only be spent once; this one cannot, so the window is the
# only thing doing the work.
STATELESS_NONCE_TTL_S = 120


class NonceError(Exception):
    """Malformed, expired, or forged. One message for all three — telling an
    attacker which half to keep working on is the same mistake `/auth/verify`
    already refuses to make."""


def enabled() -> bool:
    return bool(os.environ.get("PATHIA_AUTH_STATELESS_NONCE"))


def _secret() -> bytes:
    """The signing key, which must be identical on every instance.

    No default and no derived fallback. A per-instance secret would verify
    nothing across a cold start — exactly the bug this module exists to fix,
    reintroduced silently — and a hardcoded one would let anyone holding this
    source mint nonces for any deployment running it.
    """
    raw = os.environ.get("PATHIA_AUTH_NONCE_SECRET", "")
    if len(raw) < 32:
        raise NonceError(
            "PATHIA_AUTH_NONCE_SECRET must be set to at least 32 characters when "
            "PATHIA_AUTH_STATELESS_NONCE is on; it is the only thing making a "
            "nonce unforgeable"
        )
    return raw.encode()


def _mac(body: str) -> str:
    return hmac.new(_secret(), body.encode(), sha256).hexdigest()[:32]


def issue(now: Optional[float] = None) -> str:
    """Mint a nonce that any instance with the same secret can verify.

    Shaped to satisfy EIP-4361's alphanumeric nonce rule, so the separator is
    not a character a strict SIWE parser will reject.
    """
    now = time.time() if now is None else now
    body = f"{secrets.token_hex(12)}x{int(now + STATELESS_NONCE_TTL_S)}"
    return f"{body}x{_mac(body)}"


def verify(nonce: str, now: Optional[float] = None) -> bool:
    """True if this instance minted it, it has not expired, and it is intact."""
    now = time.time() if now is None else now
    parts = (nonce or "").split("x")
    if len(parts) != 3:
        return False
    random_part, expiry_raw, mac = parts
    body = f"{random_part}x{expiry_raw}"
    # compare_digest, not ==: a timing-variable comparison on a MAC is how a
    # forgery gets brute-forced one byte at a time.
    try:
        if not hmac.compare_digest(mac, _mac(body)):
            return False
    except NonceError:
        return False
    try:
        return now <= int(expiry_raw)
    except ValueError:
        return False
