"""Every test here names the attack the check exists to stop.

A green auth suite that only proves the happy path works is the most dangerous
kind of green: login working is not evidence that login cannot be bypassed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from services.auth import siwe

DOMAIN = "pathia.example"
ACCT = Account.from_key("0x" + "11" * 32)
OTHER = Account.from_key("0x" + "22" * 32)


def build(address=None, *, domain=DOMAIN, nonce="abc123", issued=None,
          expires=None, statement="Sign in to Pathia.", version="1", extra=""):
    now = issued or datetime.now(timezone.utc).replace(microsecond=0)
    exp = expires if expires is not None else now + timedelta(minutes=10)
    msg = (f"{domain} wants you to sign in with your Ethereum account:\n"
           f"{address or ACCT.address}\n"
           f"\n{statement}\n\n"
           f"URI: https://{domain}\n"
           f"Version: {version}\n"
           f"Chain ID: 42161\n"
           f"Nonce: {nonce}\n"
           f"Issued At: {now.isoformat().replace('+00:00', 'Z')}")
    if exp is not None:
        msg += f"\nExpiration Time: {exp.isoformat().replace('+00:00', 'Z')}"
    return msg + extra


def sign(message, acct=ACCT):
    return acct.sign_message(encode_defunct(text=message)).signature.hex()


def test_a_wallet_that_signs_our_message_gets_in():
    msg = build()
    parsed = siwe.verify(msg, sign(msg), expected_domain=DOMAIN)
    assert parsed.address.lower() == ACCT.address.lower()
    assert parsed.nonce == "abc123"


def test_a_signature_from_a_different_wallet_is_refused():
    """The address line is text the caller wrote. Only the recovered signer is
    evidence, so claiming to be ACCT while signing with OTHER must fail."""
    msg = build(address=ACCT.address)
    with pytest.raises(siwe.SiweError):
        siwe.verify(msg, sign(msg, OTHER), expected_domain=DOMAIN)


def test_a_signature_harvested_by_another_site_does_not_work_here():
    """The phishing case. evil.example gets a user to sign in there; that
    signature is valid, and must still not open a session on our domain."""
    msg = build(domain="evil.example")
    with pytest.raises(siwe.SiweError, match="domain"):
        siwe.verify(msg, sign(msg), expected_domain=DOMAIN)


def test_an_expired_message_is_refused():
    past = datetime.now(timezone.utc) - timedelta(minutes=30)
    msg = build(issued=past, expires=past + timedelta(minutes=1))
    with pytest.raises(siwe.SiweError):
        siwe.verify(msg, sign(msg), expected_domain=DOMAIN)


def test_an_old_message_is_refused_even_without_an_expiry_field():
    """Expiration Time is optional in EIP-4361. Without the max_age floor, a
    message with no expiry would be a bearer token good forever."""
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    msg = build(issued=old, expires=None)
    with pytest.raises(siwe.SiweError, match="too old"):
        siwe.verify(msg, sign(msg), expected_domain=DOMAIN)


def test_a_message_minted_in_the_future_is_refused():
    """Otherwise a signature can be pre-dated to stay valid past its window."""
    ahead = datetime.now(timezone.utc) + timedelta(hours=6)
    msg = build(issued=ahead, expires=ahead + timedelta(minutes=10))
    with pytest.raises(siwe.SiweError, match="future"):
        siwe.verify(msg, sign(msg), expected_domain=DOMAIN)


def test_small_clock_skew_is_tolerated():
    """A login that fails on a three-second clock difference is a support
    ticket, not a security win."""
    skewed = datetime.now(timezone.utc) + timedelta(seconds=20)
    msg = build(issued=skewed)
    assert siwe.verify(msg, sign(msg), expected_domain=DOMAIN)


def test_tampering_with_the_signed_text_invalidates_it():
    """The whole message is signed, so changing any byte after the fact — here
    the nonce — must break recovery."""
    msg = build(nonce="original")
    sig = sign(msg)
    with pytest.raises(siwe.SiweError):
        siwe.verify(msg.replace("original", "swapped"), sig, expected_domain=DOMAIN)


def test_an_unknown_field_is_rejected_not_ignored():
    """A lenient parser is how a message means one thing to us and another to
    the wallet that displayed it to the user."""
    with pytest.raises(siwe.SiweError, match="unknown field"):
        siwe.parse(build(extra="\nWithdraw All: yes"))


def test_a_duplicated_field_is_rejected():
    """Two Nonce lines: one shown to the user, one read by the parser."""
    with pytest.raises(siwe.SiweError, match="duplicate"):
        siwe.parse(build(extra="\nNonce: second-one"))


def test_a_naive_timestamp_is_rejected_rather_than_crashing_the_check():
    """Comparing a naive datetime against an aware one raises TypeError. Inside
    a security check that exception is the only thing between the caller and an
    unchecked expiry, so the parser refuses it up front."""
    msg = build().replace("Issued At: ", "Issued At: ").split("\n")
    msg = "\n".join(l.split("+")[0].rstrip("Z") if l.startswith("Issued At:") else l
                    for l in msg)
    with pytest.raises(siwe.SiweError, match="UTC offset"):
        siwe.parse(msg)


def test_an_unsupported_version_is_rejected():
    with pytest.raises(siwe.SiweError, match="version"):
        siwe.parse(build(version="2"))


def test_garbage_is_not_an_eip4361_message():
    for junk in ("", "hello", "0x" + "0" * 40, "{}"):
        with pytest.raises(siwe.SiweError):
            siwe.parse(junk)


def test_a_malformed_signature_raises_rather_than_returning_a_wrong_address():
    msg = build()
    for bad in ("", "0x", "0xdeadbeef", "not-a-signature"):
        with pytest.raises(siwe.SiweError):
            siwe.verify(msg, bad, expected_domain=DOMAIN)
