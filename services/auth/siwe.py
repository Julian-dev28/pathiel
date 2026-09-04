"""Sign-In With Ethereum (EIP-4361) message parsing and verification.

WHY THIS AND NOT A VENDOR
-------------------------
The product signs Hyperliquid orders, so `eth-account` is already a hard
dependency. Recovering a signer from a personal_sign signature is the same
primitive, which makes EIP-4361 a zero-new-dependency auth scheme here. Privy,
Dynamic, Magic and friends all add a vendor, a bill, an outage surface and a
third party who learns every user's wallet. None of them buy anything this file
does not already do.

It also gets the identity model right for a non-custodial trading product: a
user IS a wallet address. There is no password to leak, no reset flow to
phish, and the thing a user proves is exactly the thing the system needs to
know about them.

WHAT A SIGNATURE PROVES, AND WHAT IT DOES NOT
---------------------------------------------
It proves the holder of a private key signed *this* message. That is all. Every
other guarantee has to be built on top, and each of the checks below exists
because leaving it out is a known, named attack:

  domain     A signature harvested by evil.com must not open a session here.
             The domain is inside the signed bytes, so it cannot be swapped
             after the fact. Checked against our own expected domain.

  nonce      Without server-issued single-use nonces, one captured signature
             is a permanent password. Nonces are minted here, stored, and
             burned on use.

  expiry     A signature valid forever is a bearer token with no revocation.
             Both `Issued At` and `Expiration Time` are enforced.

  address    Recovered from the signature, never trusted from the message
             body. The address line is attacker-controlled text; only the
             recovered signer is evidence.

Parsing is strict and allowlist-shaped: an unknown field is a rejected message,
not an ignored line. A lenient parser on a security boundary is how a message
that means one thing to us means another to the wallet that displayed it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_defunct

# EIP-4361 §Message Format. The header and address lines are positional; the
# rest are `Key: value` fields. Written as one anchored pattern so a message
# with extra or reordered structural lines fails to parse rather than being
# partially understood.
_HEADER = re.compile(
    r"^(?P<domain>[^\n]+) wants you to sign in with your Ethereum account:\n"
    r"(?P<address>0x[0-9a-fA-F]{40})\n"
)
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Fields we understand. Anything else in the field block is a parse failure.
_KNOWN_FIELDS = {
    "URI", "Version", "Chain ID", "Nonce", "Issued At",
    "Expiration Time", "Not Before", "Request ID", "Resources",
}


class SiweError(ValueError):
    """A message that is malformed, or valid text that fails a security check.

    One exception type on purpose: callers must not branch on *why* a login
    failed, and an API that distinguishes "bad nonce" from "bad signature"
    hands an attacker an oracle.
    """


@dataclass(frozen=True)
class SiweMessage:
    domain: str
    address: str
    statement: Optional[str]
    uri: str
    version: str
    chain_id: str
    nonce: str
    issued_at: datetime
    expiration_time: Optional[datetime]
    not_before: Optional[datetime]


def _parse_ts(raw: str, field: str) -> datetime:
    """ISO-8601 with an offset, normalised to UTC.

    `fromisoformat` accepts a naive string, and a naive timestamp compared
    against an aware `now` raises at runtime — inside a security check, where
    the exception would be the only thing standing between a caller and an
    unchecked expiry. A missing offset is rejected instead.
    """
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SiweError(f"{field} is not ISO-8601") from exc
    if ts.tzinfo is None:
        raise SiweError(f"{field} needs a UTC offset")
    return ts.astimezone(timezone.utc)


def parse(message: str) -> SiweMessage:
    """Parse an EIP-4361 message. Raises SiweError on anything unexpected."""
    m = _HEADER.match(message)
    if not m:
        raise SiweError("not an EIP-4361 message")
    domain, address = m.group("domain"), m.group("address")
    rest = message[m.end():]

    # An optional statement sits between two blank lines, before the fields.
    statement: Optional[str] = None
    if rest.startswith("\n"):
        rest = rest[1:]
        head, sep, tail = rest.partition("\n\n")
        if not sep:
            raise SiweError("statement block is not terminated")
        statement = head or None
        rest = tail

    fields: dict[str, str] = {}
    for line in rest.strip("\n").split("\n"):
        key, sep, value = line.partition(": ")
        if not sep:
            # Resources are a `- uri` list under the Resources key. We accept
            # the key but carry no resource semantics, so a bare list item is
            # only tolerated when Resources was actually declared.
            if line.startswith("- ") and "Resources" in fields:
                continue
            raise SiweError(f"unparseable line: {line[:40]!r}")
        if key not in _KNOWN_FIELDS:
            raise SiweError(f"unknown field: {key!r}")
        if key in fields:
            raise SiweError(f"duplicate field: {key!r}")
        fields[key] = value

    for required in ("URI", "Version", "Chain ID", "Nonce", "Issued At"):
        if required not in fields:
            raise SiweError(f"missing field: {required!r}")
    if fields["Version"] != "1":
        raise SiweError("unsupported EIP-4361 version")

    exp = fields.get("Expiration Time")
    nbf = fields.get("Not Before")
    return SiweMessage(
        domain=domain,
        address=address,
        statement=statement,
        uri=fields["URI"],
        version=fields["Version"],
        chain_id=fields["Chain ID"],
        nonce=fields["Nonce"],
        issued_at=_parse_ts(fields["Issued At"], "Issued At"),
        expiration_time=_parse_ts(exp, "Expiration Time") if exp else None,
        not_before=_parse_ts(nbf, "Not Before") if nbf else None,
    )


def recover_address(message: str, signature: str) -> str:
    """The address that actually signed `message`, lowercased.

    Never compare against the address line in the message body: that text is
    supplied by the caller. Only the recovered signer is evidence.
    """
    try:
        signer = Account.recover_message(encode_defunct(text=message),
                                         signature=signature)
    except Exception as exc:                      # malformed / wrong-length sig
        raise SiweError("signature does not recover") from exc
    return str(signer).lower()


def verify(message: str, signature: str, *, expected_domain: str,
           now: Optional[datetime] = None,
           max_age_s: int = 600) -> SiweMessage:
    """Full check. Returns the parsed message, or raises SiweError.

    The caller still has to burn the nonce — that is state, and this module is
    deliberately stateless so it can be tested without a database. See
    `services.auth.store.consume_nonce`.
    """
    now = now or datetime.now(timezone.utc)
    parsed = parse(message)

    if parsed.domain != expected_domain:
        raise SiweError("domain mismatch")

    if not _ADDRESS_RE.match(parsed.address):
        raise SiweError("malformed address")

    # A message minted far in the future would otherwise stay valid for as long
    # as the clock skew allows. 60s of tolerance, not zero: a user's clock is
    # not our clock, and a login that fails on a 3-second skew is a support
    # ticket, not a security win.
    if parsed.issued_at > now.replace(microsecond=0) and \
            (parsed.issued_at - now).total_seconds() > 60:
        raise SiweError("issued in the future")
    if (now - parsed.issued_at).total_seconds() > max_age_s:
        raise SiweError("message too old")
    if parsed.expiration_time is not None and now >= parsed.expiration_time:
        raise SiweError("message expired")
    if parsed.not_before is not None and now < parsed.not_before:
        raise SiweError("message not yet valid")

    if recover_address(message, signature) != parsed.address.lower():
        raise SiweError("signature does not match address")

    return parsed
