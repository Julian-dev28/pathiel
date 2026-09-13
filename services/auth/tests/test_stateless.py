"""Gate tests for stateless login nonces.

This module trades single-use for portability across instances, which is a
security downgrade taken deliberately on one deployment. The tests therefore
cover two things with equal weight: that it works, and that it stays off unless
a deployment asks for it by name.
"""

from __future__ import annotations

import time

import pytest

from services.auth import stateless as N

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


class TestSessions:
    """The loop this fixes: verify returns 200, sets the cookie, and the next
    /auth/me answers 401 because the row was written to another instance's
    disk. The page prompts again, the user signs again, forever. An outright
    failure would have been kinder."""

    ADDR = "0x" + "ab" * 20

    @pytest.fixture(autouse=True)
    def on(self, monkeypatch):
        monkeypatch.setenv("PATHIA_AUTH_STATELESS_SESSION", "1")

    def test_it_is_off_unless_asked_for(self, monkeypatch):
        monkeypatch.delenv("PATHIA_AUTH_STATELESS_SESSION", raising=False)
        assert N.sessions_enabled() is False

    def test_nonces_and_sessions_switch_independently(self, monkeypatch):
        """Different trades — a nonce gives up single-use, a session gives up
        revocation — so a deployment may want one and not the other."""
        monkeypatch.delenv("PATHIA_AUTH_STATELESS_NONCE", raising=False)
        assert N.sessions_enabled() is True
        assert N.enabled() is False

    def test_a_token_round_trips_to_its_address(self):
        assert N.read_session(N.issue_session(self.ADDR)) == self.ADDR

    def test_it_reads_on_an_instance_that_never_minted_it(self):
        """The entire point. No store, no memory, just the secret."""
        minted = N.issue_session(self.ADDR)
        assert N.read_session(minted) == self.ADDR

    def test_the_address_is_lowercased(self):
        """EIP-55 and all-lowercase are the same account; storing both would
        let one wallet hold two identities."""
        assert N.read_session(N.issue_session(self.ADDR.upper().replace("0X", "0x"))) == self.ADDR

    def test_an_expired_session_is_refused(self):
        minted = N.issue_session(self.ADDR)
        assert N.read_session(minted, now=time.time() + N.STATELESS_SESSION_TTL_S + 1) is None

    def test_the_ttl_is_far_shorter_than_a_stored_session(self):
        """A stored session can be revoked; this one cannot, so it must not
        live for two weeks."""
        from services.auth.store import SESSION_TTL_S
        assert N.STATELESS_SESSION_TTL_S < SESSION_TTL_S / 10

    def test_a_tampered_address_is_refused(self):
        """The forgery that matters: swap the address, keep the MAC, become
        somebody else."""
        _, _, expiry, mac = N.issue_session(self.ADDR).split(".")
        forged = f"v1.{'0x' + 'cd' * 20}.{expiry}.{mac}"
        assert N.read_session(forged) is None

    def test_a_tampered_expiry_is_refused(self):
        _, addr, expiry, mac = N.issue_session(self.ADDR).split(".")
        assert N.read_session(f"v1.{addr}.{int(expiry) + 86_400}.{mac}") is None

    def test_a_token_from_another_deployment_is_refused(self, monkeypatch):
        minted = N.issue_session(self.ADDR)
        monkeypatch.setenv("PATHIA_AUTH_NONCE_SECRET", "elsewhere" * 8)
        assert N.read_session(minted) is None

    @pytest.mark.parametrize("junk", ["", "nope", "v1.a.b", "v2." + "x" * 40 + ".1.2", None])
    def test_malformed_tokens_are_refused_not_raised(self, junk):
        assert N.read_session(junk) is None

    def test_a_stored_nonce_is_not_a_valid_session(self):
        """Different shapes under the same secret. Confusing one for the other
        would let a nonce open a session."""
        assert N.read_session(N.issue()) is None

    def test_it_refuses_to_mint_for_a_non_address(self):
        for bad in ("", "nope", "0x123", "ab" * 20):
            with pytest.raises(N.NonceError):
                N.issue_session(bad)

    def test_verification_fails_closed_without_the_secret(self, monkeypatch):
        minted = N.issue_session(self.ADDR)
        monkeypatch.delenv("PATHIA_AUTH_NONCE_SECRET", raising=False)
        assert N.read_session(minted) is None
