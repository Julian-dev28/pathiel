"""A missing maxLeverage must never quietly become 1x.

2026-09-08: the watchdog re-exec'd, startup logged "meta prewarm exceeded 3s —
metadata will warm lazily", and two positions opened in that cold window went on
the exchange at 1x while the config asked for 3x. Nothing errored.

The damage is not risk-per-trade — a stop loses stop_pct of NOTIONAL whatever the
leverage. It is that margin triples, so the account fits a third of the intended
book, and every sizing number derived from `leverage` is wrong while looking
right. Every xyz market allows at least 3x, so 1 was never a real answer; it was
a dict `.get(..., 1)` default standing in for missing data.
"""
from __future__ import annotations

import pytest

import pathiel.client.exchange as EX


@pytest.fixture(autouse=True)
def _clear():
    EX._META_CACHE.clear()
    yield
    EX._META_CACHE.clear()


def test_a_partial_cache_entry_forces_a_refresh_instead_of_returning_1(monkeypatch):
    """The exact 2026-09-08 shape: the coin IS cached, but without the field."""
    calls = {"n": 0}

    def _fake(dex=None, force_refresh=False):
        calls["n"] += 1
        if not force_refresh:
            return [{"name": "xyz:FOO"}]                 # partial: no maxLeverage
        return [{"name": "xyz:FOO", "maxLeverage": 10}]  # complete after refresh

    monkeypatch.setattr(EX, "_cached_universe", _fake)
    assert EX.get_max_leverage("xyz:FOO") == 10
    assert calls["n"] >= 2, "it must actually re-fetch, not just re-read the cache"


@pytest.mark.parametrize("bad", [None, 0, "", "abc", -1])
def test_unusable_leverage_values_never_pass_through_as_1(monkeypatch, bad):
    monkeypatch.setattr(EX, "_cached_universe",
                        lambda dex=None, force_refresh=False:
                        [{"name": "xyz:FOO", "maxLeverage": bad}])
    with pytest.raises(ValueError):
        EX.get_max_leverage("xyz:FOO")


def test_it_raises_rather_than_returning_a_wrong_number(monkeypatch):
    """Raising is the point: the caller does min(requested, this), so any
    plausible-but-wrong value silently mis-sizes instead of failing."""
    monkeypatch.setattr(EX, "_cached_universe",
                        lambda dex=None, force_refresh=False: [])
    with pytest.raises(ValueError):
        EX.get_max_leverage("xyz:NOPE")


def test_a_good_value_is_returned_without_a_refresh(monkeypatch):
    calls = {"n": 0}

    def _fake(dex=None, force_refresh=False):
        calls["n"] += 1
        return [{"name": "xyz:FOO", "maxLeverage": 20}]

    monkeypatch.setattr(EX, "_cached_universe", _fake)
    assert EX.get_max_leverage("xyz:FOO") == 20
    assert calls["n"] == 1, "a complete cache entry must not trigger a refetch"


def test_force_refresh_bypasses_a_live_cache_entry(monkeypatch):
    import time
    EX._META_CACHE["xyz"] = (time.time(), [{"name": "xyz:FOO"}])
    monkeypatch.setattr(EX, "_get_info", lambda: type(
        "I", (), {"meta": staticmethod(
            lambda dex=None: {"universe": [{"name": "xyz:FOO", "maxLeverage": 5}]})})())
    assert EX._cached_universe(dex="xyz", force_refresh=True)[0]["maxLeverage"] == 5


# The 2026-10-23 (wave 2) regression-guard this file used to carry for the
# retired executor's "leverage_unresolved" refusal does not have a direct
# analog in the place_bracket path: scripts/pathiel-mcp-server.py's
# `_coin_specs` swallows a `get_max_leverage` failure into `coin_max=None`
# rather than refusing, and services.risk.size.size_trade falls back to the
# operator's risk-profile leverage cap when no coin max is known (Hyperliquid
# itself still enforces the real per-coin cap server-side on `set_leverage`).
# That is a materially different risk posture from "refuse outright," not
# audited as part of this cleanup — flagged for Julien, not silently carried
# forward as if it were still tested.
