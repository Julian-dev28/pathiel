"""The leverage ladder a public install sees (final review, 2026-11-12).

The desks offer 5x/10x/20x rungs (oil, metals, ai, ...). The public risk
profile caps leverage at 3x, so before this every directional thesis on a
public install recommended a leverage the user's own profile refuses.
services.strategies.desks.get_spec now cuts the ladder to the profile cap on a
public install; the operator's install gets the cached spec object itself.

Offline: candles, meta and account are fakes; the risk profile is patched.
"""
from __future__ import annotations

import re

import pytest

from contracts.risk.models import RiskProfile
from contracts.strategies.models import AccountSnapshot
from services.strategies import analytics as A
from services.strategies import desks
from services.strategies.tests.conftest import flat_meta, make_candles
from services.thesis import prompt as prompt_mod
from services.thesis import render, validator

RUNG_LINE = re.compile(r"^- (\d+)x (?:long|short):", re.M)
TABLE_ROW = re.compile(r"^\| [^|]+ \| (\d+)x \|", re.M)


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.delenv("PATHIEL_INSTALL", raising=False)


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setattr(desks, "profile_leverage_cap", lambda: 3.0)


def _analytics(spec):
    coins = [i.coin for i in spec.instruments]
    candles = {c: make_candles([100.0 + 0.1 * i for i in range(60)], wiggle=0.01) for c in coins}
    return A.build_analytics(
        spec, candle_fetcher=lambda coin, interval, count: candles[coin],
        meta_fetcher=lambda: {c: flat_meta(max_leverage=20) for c in coins},
        account_fetcher=lambda: AccountSnapshot(equity_usd=500.0, source="assumed"))


# ── the cut ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ladder,cap,want", [
    ([5, 10, 20], 3.0, [2.0, 3.0]),
    ([5, 10, 20], 5.0, [2.0, 3.0, 5.0]),
    ([5, 10, 20], 10.0, [5.0, 10.0]),
    ([5, 10, 20], 25.0, [5.0, 10.0, 20.0]),
    ([5, 10], 2.0, [2.0]),
    ([5, 10], 1.5, [1.0]),
    ([5, 10, 20], None, [5, 10, 20]),
])
def test_ladder_for_cap(ladder, cap, want):
    assert desks.ladder_for_cap(ladder, cap) == want


def test_operator_gets_the_cached_spec_untouched(operator, monkeypatch):
    monkeypatch.setattr(desks, "profile_leverage_cap", lambda: pytest.fail("operator read a profile"))
    spec = desks.get_spec("oil")
    assert spec is desks._cached_spec("oil")
    assert spec.leverage_ladder == [5, 10, 20]
    assert all(s is desks._cached_spec(s.id) for s in desks.list_specs())


def test_public_3x_profile_cuts_every_desk(public):
    for spec in desks.list_specs():
        assert spec.leverage_ladder == [2.0, 3.0], spec.id
        assert desks.get_spec(spec.id).leverage_ladder == [2.0, 3.0]
    assert desks._cached_spec("oil").leverage_ladder == [5, 10, 20], "the cache is never mutated"


def test_public_profile_raised_to_10x_keeps_the_desk_rungs(monkeypatch):
    monkeypatch.setenv("PATHIEL_INSTALL", "public")
    monkeypatch.setattr(desks, "profile_leverage_cap", lambda: 10.0)
    assert desks.get_spec("oil").leverage_ladder == [5.0, 10.0]


def test_profile_cap_reads_the_saved_profile(monkeypatch):
    from pathiel.agents import config_store
    monkeypatch.setattr(config_store, "read_risk_profile",
                        lambda: RiskProfile(preset="custom", max_leverage=3.0))
    assert desks.profile_leverage_cap() == 3.0

    def broken():
        raise OSError("unreadable")

    monkeypatch.setattr(config_store, "read_risk_profile", broken)
    assert desks.profile_leverage_cap() == 3.0, "a broken read falls back to the public cap"


# ── what the thesis writer and the brief see ─────────────────────────────────

def test_public_prompt_validator_and_brief_never_offer_more_than_3x(public, oil_thesis):
    spec = desks.get_spec("oil")
    analytics = _analytics(spec)
    assert analytics.ladder and {r.leverage for r in analytics.ladder} <= {2.0, 3.0}

    text, _tokens = prompt_mod.build_user_prompt(spec, analytics, None, None, incremental=False)
    assert "Leverage choices available: 2x, 3x." in text
    rungs = [int(x) for x in RUNG_LINE.findall(text)]
    assert rungs and max(rungs) <= 3, rungs

    call10 = oil_thesis.call.model_copy(update={"leverage": 10.0})
    assert validator.check_leverage_not_avoid(call10, analytics), "10x is not a public rung"

    full = render.render_markdown(oil_thesis, analytics, mode="full")
    rows = [int(x) for x in TABLE_ROW.findall(full)]
    assert rows and max(rows) <= 3, rows


def test_operator_prompt_still_offers_the_desk_ladder(operator):
    spec = desks.get_spec("oil")
    analytics = _analytics(spec)
    text, _tokens = prompt_mod.build_user_prompt(spec, analytics, None, None, incremental=False)
    assert "Leverage choices available: 5x, 10x, 20x." in text
    assert {r.leverage for r in analytics.ladder} == {5.0, 10.0, 20.0}


@pytest.fixture
def oil_thesis():
    from contracts.strategies.fixtures import _build
    return _build.thesis
