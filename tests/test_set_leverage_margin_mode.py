"""Unit tests for `set_leverage`'s is_cross/strict knobs.

Pure unit tests — no network. `_make_exchange` and `PRIVATE_KEY_HEX` are
monkeypatched.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from pathiel.client import exchange as ex


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
