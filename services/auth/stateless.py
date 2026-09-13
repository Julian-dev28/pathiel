"""Stateless login nonces and sessions, for deployments with no durable disk.

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

SESSIONS HAVE THE SAME DISEASE

`create_session` writes a row too, so a session opened on one instance is
unknown to the next: /auth/verify returns 200, sets the cookie, and the very
next /auth/me answers 401. The page shows the sign-in prompt again, the user
signs again, and it loops — which is what this looked like in the field, and
what makes it worse than an outright failure. Fixing the nonce alone moved the
loop one step later.

A stateless session token carries the address and an expiry under the same MAC.
What it gives up is SERVER-SIDE REVOCATION: `revoke_session` and
`revoke_all_for_user` cannot reach a token nobody stored, so a leaked one is
valid until it expires. That is why the stateless TTL is 12 hours against the
stored 14 days, and why this too is opt-in by name.
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


# Sessions cannot be revoked once minted, so they expire far sooner than stored
# ones. 14 days is defensible when `revoke_all_for_user` can end it early; it is
# not when nothing can.
STATELESS_SESSION_TTL_S = 12 * 3600


def enabled() -> bool:
    """Stateless nonces."""
    return bool(os.environ.get("PATHIA_AUTH_STATELESS_NONCE"))


def sessions_enabled() -> bool:
    """Stateless sessions. Separate switch, because the trades differ: a nonce
    gives up single-use, a session gives up revocation."""
    return bool(os.environ.get("PATHIA_AUTH_STATELESS_SESSION"))


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


# ── sessions ────────────────────────────────────────────────────────────────

def issue_session(address: str, now: Optional[float] = None) -> str:
    """A session token that any instance with the secret can validate.

    Shape is `v1.<address>.<expiry>.<mac>`. The address is in the clear on
    purpose: it is public, the browser already knows it, and an opaque token
    would need a lookup table — which is the thing that does not exist here.
    """
    now = time.time() if now is None else now
    addr = (address or "").lower()
    if not addr.startswith("0x") or len(addr) != 42:
        raise NonceError(f"refusing to mint a session for {address!r}")
    body = f"v1.{addr}.{int(now + STATELESS_SESSION_TTL_S)}"
    return f"{body}.{_mac(body)}"


def read_session(token: str, now: Optional[float] = None) -> Optional[str]:
    """The address this token proves, or None.

    None for every failure — forged, expired, malformed, wrong deployment. A
    caller that distinguishes them tells an attacker which half to keep working
    on, and no caller here needs to.
    """
    now = time.time() if now is None else now
    parts = (token or "").split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return None
    _, addr, expiry_raw, mac = parts
    body = f"v1.{addr}.{expiry_raw}"
    try:
        if not hmac.compare_digest(mac, _mac(body)):
            return None
    except NonceError:
        return None
    try:
        if now > int(expiry_raw):
            return None
    except ValueError:
        return None
    return addr if addr.startswith("0x") and len(addr) == 42 else None
