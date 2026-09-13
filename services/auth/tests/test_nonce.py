"""Gate tests for stateless login nonces.

This module trades single-use for portability across instances, which is a
security downgrade taken deliberately on one deployment. The tests therefore
cover two things with equal weight: that it works, and that it stays off unless
a deployment asks for it by name.
"""

from __future__ import annotations

import time

import pytest

from services.auth import nonce as N

SECRET = "k" * 48


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("PATHIA_AUTH_NONCE_SECRET", SECRET)


class TestOffByDefault:
    def test_it_is_disabled_unless_asked_for(self, monkeypatch):
        """A deployment with a durable volume keeps burned nonces and must not
        silently lose replay protection to an import."""
        monkeypatch.delenv("PATHIA_AUTH_STATELESS_NONCE", raising=False)
        assert N.enabled() is False

    def test_the_flag_turns_it_on(self, monkeypatch):
        monkeypatch.setenv("PATHIA_AUTH_STATELESS_NONCE", "1")
        assert N.enabled() is True


class TestRoundTrip:
    def test_a_freshly_minted_nonce_verifies(self):
        assert N.verify(N.issue()) is True

    def test_it_verifies_without_any_stored_state(self):
        """The entire point: another instance, holding only the secret, with no
        database and no memory of minting it."""
        minted = N.issue()
        N.issue()  # unrelated traffic in between
        assert N.verify(minted) is True

    def test_the_nonce_is_alphanumeric(self):
        """EIP-4361 requires it, and `siwe.py` parses strictly — a separator
        the spec rejects would fail at the wallet, not here."""
        assert N.issue().isalnum()

    def test_two_nonces_are_never_the_same(self):
        assert len({N.issue() for _ in range(200)}) == 200


class TestRejection:
    def test_an_expired_nonce_is_refused(self):
        minted = N.issue()
        assert N.verify(minted, now=time.time() + N.STATELESS_NONCE_TTL_S + 1) is False

    def test_the_window_is_tighter_than_the_stored_one(self):
        """Single-use is what let the stored nonce live 600s. Without it the
        window is the only thing doing the work."""
        from services.auth.store import NONCE_TTL_S
        assert N.STATELESS_NONCE_TTL_S < NONCE_TTL_S

    def test_a_tampered_mac_is_refused(self):
        minted = N.issue()
        flipped = minted[:-1] + ("0" if minted[-1] != "0" else "1")
        assert N.verify(flipped) is False

    def test_a_tampered_expiry_is_refused(self):
        """Pushing the expiry out is the obvious forgery; the MAC covers it."""
        random_part, expiry, mac = N.issue().split("x")
        forged = f"{random_part}x{int(expiry) + 86_400}x{mac}"
        assert N.verify(forged) is False

    @pytest.mark.parametrize("junk", ["", "nope", "a.b.c", "axb", "axbxcxd", None])
    def test_malformed_input_is_refused_not_raised(self, junk):
        """A 500 on a malformed nonce is a denial-of-service with extra steps."""
        assert N.verify(junk) is False

    def test_a_nonce_from_another_deployment_is_refused(self, monkeypatch):
        """The secret is what separates deployments. Without this, anyone
        running this source could mint nonces for any other install."""
        minted = N.issue()
        monkeypatch.setenv("PATHIA_AUTH_NONCE_SECRET", "different" * 8)
        assert N.verify(minted) is False


class TestSecretHandling:
    def test_a_missing_secret_is_a_hard_error_on_mint(self, monkeypatch):
        """Never a default, never derived. A per-instance fallback would verify
        nothing across a cold start — the exact bug this module fixes, back
        again and silent."""
        monkeypatch.delenv("PATHIA_AUTH_NONCE_SECRET", raising=False)
        with pytest.raises(N.NonceError):
            N.issue()

    def test_a_short_secret_is_refused(self, monkeypatch):
        monkeypatch.setenv("PATHIA_AUTH_NONCE_SECRET", "tooshort")
        with pytest.raises(N.NonceError):
            N.issue()

    def test_a_missing_secret_refuses_rather_than_accepts(self, monkeypatch):
        """Verification must fail CLOSED when it cannot check. An exception
        escaping here would be a 500; returning True would be an open door."""
        minted = N.issue()
        monkeypatch.delenv("PATHIA_AUTH_NONCE_SECRET", raising=False)
        assert N.verify(minted) is False
