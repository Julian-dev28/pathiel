"""Unit tests for the copycat-book exchange contract: `set_leverage`'s
is_cross/strict knobs and `fetch_leader_extras`'s spot+staked valuation.

Pure unit tests — no network. `_make_exchange`, `_http_post`,
`_is_isolated_only`, and `PRIVATE_KEY_HEX` are all monkeypatched.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from pathiel.client import exchange as ex


# ── set_leverage ─────────────────────────────────────────────────────────────

class FakeExchange:
    """Records update_leverage calls; returns a canned response."""

    def __init__(self, response: Any = None, raise_exc: Optional[Exception] = None):
        self.calls = []
        self._response = response if response is not None else {"status": "ok", "response": {"type": "default"}}
        self._raise = raise_exc

    def update_leverage(self, leverage, coin, is_cross=True):
        self.calls.append({"leverage": leverage, "coin": coin, "is_cross": is_cross})
        if self._raise is not None:
            raise self._raise
        return self._response


@pytest.fixture(autouse=True)
def _with_private_key(monkeypatch):
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "deadbeef")


def _install_fake_exchange(monkeypatch, **kwargs) -> FakeExchange:
    fake = FakeExchange(**kwargs)
    monkeypatch.setattr(ex, "_make_exchange", lambda: fake)
    return fake


def test_set_leverage_auto_mode_unchanged_on_isolated_only(monkeypatch):
    """is_cross=None (default): auto picks isolated on an onlyIsolated market —
    today's behavior, must not change."""
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: True)
    fake = _install_fake_exchange(monkeypatch)

    result = ex.set_leverage("xyz:NVDA", 3)

    assert result["ok"] is True
    assert result["is_cross"] is False
    assert fake.calls == [{"leverage": 3, "coin": "xyz:NVDA", "is_cross": False}]


def test_set_leverage_auto_mode_unchanged_on_cross_eligible(monkeypatch):
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: False)
    fake = _install_fake_exchange(monkeypatch)

    result = ex.set_leverage("BTC", 10)

    assert result["ok"] is True
    assert result["is_cross"] is True
    assert fake.calls == [{"leverage": 10, "coin": "BTC", "is_cross": True}]


def test_set_leverage_cross_requested_on_isolated_only_falls_back(monkeypatch, caplog):
    """is_cross=True explicitly requested on an isolated-only market: must
    fall back to isolated (is_cross False in both the call and the return)
    and log it."""
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: True)
    fake = _install_fake_exchange(monkeypatch)

    import logging
    with caplog.at_level(logging.INFO, logger=ex.logger.name):
        result = ex.set_leverage("xyz:NVDA", 3, is_cross=True)

    assert result["ok"] is True
    assert result["is_cross"] is False
    assert fake.calls == [{"leverage": 3, "coin": "xyz:NVDA", "is_cross": False}]
    assert any("falling back to isolated" in r.message for r in caplog.records)


def test_set_leverage_is_cross_false_forces_isolated(monkeypatch):
    """is_cross=False: isolated, unconditionally — even on a cross-eligible
    market."""
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: False)
    fake = _install_fake_exchange(monkeypatch)

    result = ex.set_leverage("BTC", 5, is_cross=False)

    assert result["ok"] is True
    assert result["is_cross"] is False
    assert fake.calls == [{"leverage": 5, "coin": "BTC", "is_cross": False}]


def test_set_leverage_strict_ok_on_status_ok(monkeypatch):
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: False)
    _install_fake_exchange(monkeypatch, response={"status": "ok", "response": {"type": "default"}})

    result = ex.set_leverage("BTC", 5, strict=True)

    assert result["ok"] is True
    assert result["is_cross"] is True


def test_set_leverage_strict_fails_on_status_err(monkeypatch):
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: False)
    _install_fake_exchange(monkeypatch, response={"status": "err", "response": "Insufficient margin"})

    result = ex.set_leverage("BTC", 5, strict=True)

    assert result["ok"] is False
    assert result["error"] == "Insufficient margin"
    assert result["is_cross"] is True


def test_set_leverage_non_strict_still_ok_on_status_err(monkeypatch):
    """Back-compat: non-strict (default) reports ok=True even when HL's body
    says status=err, as long as the SDK call itself didn't raise. This is
    the documented pre-existing bug `strict` exists to fix without touching
    every caller."""
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: False)
    _install_fake_exchange(monkeypatch, response={"status": "err", "response": "Insufficient margin"})

    result = ex.set_leverage("BTC", 5)

    assert result["ok"] is True
    assert result["result"] == {"status": "err", "response": "Insufficient margin"}


def test_set_leverage_strict_fails_on_sdk_exception(monkeypatch):
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: False)
    _install_fake_exchange(monkeypatch, raise_exc=RuntimeError("boom"))

    result = ex.set_leverage("BTC", 5, strict=True)

    assert result["ok"] is False
    assert "boom" in result["error"]


def test_set_leverage_rejects_leverage_below_one(monkeypatch):
    isolated_check_calls = []
    monkeypatch.setattr(ex, "_is_isolated_only", lambda coin: isolated_check_calls.append(coin) or False)
    fake = _install_fake_exchange(monkeypatch)

    result = ex.set_leverage("BTC", 0)

    assert result["ok"] is False
    assert "error" in result
    assert fake.calls == []
    # No-call contract also means we never needed the market-type lookup.
    assert isolated_check_calls == []


def test_set_leverage_rejects_negative_leverage(monkeypatch):
    fake = _install_fake_exchange(monkeypatch)

    result = ex.set_leverage("BTC", -5)

    assert result["ok"] is False
    assert fake.calls == []


def test_set_leverage_no_private_key(monkeypatch):
    # autouse fixture sets a key; override it back off for this one case.
    monkeypatch.setattr(ex, "PRIVATE_KEY_HEX", "")
    fake = _install_fake_exchange(monkeypatch)

    result = ex.set_leverage("BTC", 5)

    assert result["ok"] is False
    assert fake.calls == []


# ── fetch_leader_extras ──────────────────────────────────────────────────────

USER = "0xe2823659be02e0f48a4660e4da008b5e1abfdf29"

SPOT_RESPONSE = {
    "balances": [
        {"coin": "USDC", "token": 0, "total": "1000.5", "hold": "0.0", "entryNtl": "0.0"},
        {"coin": "HYPE", "token": 150, "total": "2.0", "hold": "0.0", "entryNtl": "0.0"},
        {"coin": "PURR", "token": 1, "total": "10.0", "hold": "0.0", "entryNtl": "0.0"},
        {"coin": "+71790", "token": 999, "total": "500.0", "hold": "0.0", "entryNtl": "0.0"},
        {"coin": "USDE", "token": 235, "total": "0.0", "hold": "0.0", "entryNtl": "0.0"},
    ],
}

DELEGATOR_RESPONSE = {
    "delegated": "1026.29724485",
    "undelegated": "5.0",
    "totalPendingWithdrawal": "1.5",
    "nPendingWithdrawals": 0,
}

SPOT_META_AND_CTXS = [
    {
        "tokens": [
            {"name": "USDC", "index": 0},
            {"name": "PURR", "index": 1},
            {"name": "HYPE", "index": 150},
            # token 999 ("+71790") intentionally has NO matching universe pair below.
        ],
        "universe": [
            {"tokens": [1, 0], "name": "PURR/USDC", "index": 0},
            {"tokens": [150, 0], "name": "@107", "index": 107},
        ],
    },
    # ctxs must be indexable up to index 107 — pad with None/dummy entries like
    # the real feed (ctxs is longer than universe and sparsely used by index).
    None,  # placeholder, replaced in the fixture below
]


def _make_ctxs():
    ctxs = [{"midPx": "0.0"} for _ in range(120)]
    ctxs[0] = {"midPx": "0.165", "markPx": "0.166"}       # PURR/USDC
    ctxs[107] = {"midPx": "87.70", "markPx": "87.71"}      # HYPE/USDC
    return ctxs


def _spot_meta_and_ctxs_payload():
    meta = SPOT_META_AND_CTXS[0]
    return [meta, _make_ctxs()]


def _http_post_router(responses: Dict[str, Any]):
    """Builds a fake `_http_post(path, payload, timeout=...)` that dispatches
    on payload["type"], mirroring how the real callers key their reads."""

    def _fake(path, payload, timeout=5):
        t = payload.get("type")
        if t not in responses:
            raise AssertionError(f"unexpected info type in test: {t}")
        value = responses[t]
        if callable(value):
            return value()
        return value

    return _fake


@pytest.fixture(autouse=True)
def _reset_spot_price_cache(monkeypatch):
    """Every test starts with a cold price cache so cache behavior is
    explicit per test rather than leaking across tests."""
    monkeypatch.setattr(ex, "_SPOT_PRICE_CACHE", None)
    monkeypatch.setattr(ex, "_SPOT_PRICE_CACHE_TS", 0.0)


def test_fetch_leader_extras_happy_path(monkeypatch):
    monkeypatch.setattr(ex, "_http_post", _http_post_router({
        "spotClearinghouseState": SPOT_RESPONSE,
        "delegatorSummary": DELEGATOR_RESPONSE,
        "spotMetaAndAssetCtxs": _spot_meta_and_ctxs_payload,
    }))

    result = ex.fetch_leader_extras(USER)

    assert result is not None
    # USDC: 1000.5 flat.
    assert result["spot_usdc"] == pytest.approx(1000.5)
    # spot_other_usd: HYPE 2.0 * 87.70 + PURR 10.0 * 0.165 (token +71790 skipped, no price)
    assert result["spot_other_usd"] == pytest.approx(2.0 * 87.70 + 10.0 * 0.165)
    # staked HYPE: (1026.29724485 + 5.0 + 1.5) * 87.70
    expected_staked = (1026.29724485 + 5.0 + 1.5) * 87.70
    assert result["staked_hype_usd"] == pytest.approx(expected_staked)


def test_fetch_leader_extras_none_when_spot_read_fails(monkeypatch):
    monkeypatch.setattr(ex, "_http_post", _http_post_router({
        "spotClearinghouseState": None,  # simulates a failed/timed-out read
        "delegatorSummary": DELEGATOR_RESPONSE,
        "spotMetaAndAssetCtxs": _spot_meta_and_ctxs_payload,
    }))

    assert ex.fetch_leader_extras(USER) is None


def test_fetch_leader_extras_none_when_prices_read_fails(monkeypatch):
    monkeypatch.setattr(ex, "_http_post", _http_post_router({
        "spotClearinghouseState": SPOT_RESPONSE,
        "delegatorSummary": DELEGATOR_RESPONSE,
        "spotMetaAndAssetCtxs": None,
    }))

    assert ex.fetch_leader_extras(USER) is None


def test_fetch_leader_extras_none_when_delegator_read_fails(monkeypatch):
    monkeypatch.setattr(ex, "_http_post", _http_post_router({
        "spotClearinghouseState": SPOT_RESPONSE,
        "delegatorSummary": None,
        "spotMetaAndAssetCtxs": _spot_meta_and_ctxs_payload,
    }))

    assert ex.fetch_leader_extras(USER) is None


def test_fetch_leader_extras_never_returns_zero_as_a_disguised_failure(monkeypatch):
    """A failed read must never look like a flat leader: assert the failure
    path is None, not a dict of zeros."""
    monkeypatch.setattr(ex, "_http_post", _http_post_router({
        "spotClearinghouseState": {},  # malformed: no "balances" key
        "delegatorSummary": DELEGATOR_RESPONSE,
        "spotMetaAndAssetCtxs": _spot_meta_and_ctxs_payload,
    }))

    result = ex.fetch_leader_extras(USER)
    assert result is None
    assert result != {"spot_usdc": 0.0, "spot_other_usd": 0.0, "staked_hype_usd": 0.0}


def test_fetch_leader_extras_token_without_price_is_skipped_not_failed(monkeypatch):
    """A spot balance with no USDC pair (e.g. a synthetic/delisted token)
    must be skipped (valued at 0) rather than failing the whole read."""
    spot_with_unpriced_only = {
        "balances": [
            {"coin": "USDC", "token": 0, "total": "100.0"},
            {"coin": "+71790", "token": 999, "total": "500.0"},
        ],
    }
    monkeypatch.setattr(ex, "_http_post", _http_post_router({
        "spotClearinghouseState": spot_with_unpriced_only,
        "delegatorSummary": {"delegated": "0.0", "undelegated": "0.0", "totalPendingWithdrawal": "0.0"},
        "spotMetaAndAssetCtxs": _spot_meta_and_ctxs_payload,
    }))

    result = ex.fetch_leader_extras(USER)

    assert result is not None
    assert result["spot_usdc"] == pytest.approx(100.0)
    assert result["spot_other_usd"] == pytest.approx(0.0)  # +71790 skipped, no USDC pair
    assert result["staked_hype_usd"] == pytest.approx(0.0)


def test_fetch_leader_extras_caches_spot_meta_and_ctxs(monkeypatch):
    """The 907-row spotMetaAndAssetCtxs payload must be fetched once and
    reused across calls within the TTL, not once per leader per cycle."""
    call_log = []

    def _fake_http_post(path, payload, timeout=5):
        t = payload.get("type")
        call_log.append(t)
        if t == "spotClearinghouseState":
            return SPOT_RESPONSE
        if t == "delegatorSummary":
            return DELEGATOR_RESPONSE
        if t == "spotMetaAndAssetCtxs":
            return _spot_meta_and_ctxs_payload()
        raise AssertionError(f"unexpected type {t}")

    monkeypatch.setattr(ex, "_http_post", _fake_http_post)

    r1 = ex.fetch_leader_extras(USER)
    r2 = ex.fetch_leader_extras(USER)

    assert r1 is not None and r2 is not None
    assert call_log.count("spotMetaAndAssetCtxs") == 1
    assert call_log.count("spotClearinghouseState") == 2
    assert call_log.count("delegatorSummary") == 2


def test_fetch_leader_extras_refetches_prices_after_ttl(monkeypatch):
    call_log = []

    def _fake_http_post(path, payload, timeout=5):
        t = payload.get("type")
        call_log.append(t)
        if t == "spotClearinghouseState":
            return SPOT_RESPONSE
        if t == "delegatorSummary":
            return DELEGATOR_RESPONSE
        if t == "spotMetaAndAssetCtxs":
            return _spot_meta_and_ctxs_payload()
        raise AssertionError(f"unexpected type {t}")

    monkeypatch.setattr(ex, "_http_post", _fake_http_post)

    assert ex.fetch_leader_extras(USER) is not None
    assert call_log.count("spotMetaAndAssetCtxs") == 1

    # Simulate TTL expiry by rewinding the cache timestamp.
    monkeypatch.setattr(ex, "_SPOT_PRICE_CACHE_TS", 0.0)

    assert ex.fetch_leader_extras(USER) is not None
    assert call_log.count("spotMetaAndAssetCtxs") == 2
