"""Integration tests for the uw_flow_xs book vs the REAL ClaimsRegistry.

Locks the money-critical behaviour: correct long/short ranking by UW net-flow, bounded
sizing, shadow records-only, live claims+executes, and no claim leaks.
"""
import types

import pytest

from hermes_trader.agents import uw_flow_xs_live as m
from hermes_trader.agents import rebalancer_owned as ro


UNI = [
    {"coin": "xyz:AAPL", "dayNtlVlm": 5e6},
    {"coin": "xyz:NVDA", "dayNtlVlm": 5e6},
    {"coin": "xyz:INTC", "dayNtlVlm": 5e6},
    {"coin": "xyz:MU", "dayNtlVlm": 5e6},
    {"coin": "xyz:XYZ100", "dayNtlVlm": 9e9},   # index — must be excluded
    {"coin": "BTC", "dayNtlVlm": 9e9},          # crypto — not an xyz equity
]
# flow: AAPL most bullish -> ... -> MU most bearish
FLOW = {"AAPL": 0.8, "NVDA": 0.3, "INTC": -0.2, "MU": -0.7}


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(ro, "_CLAIMS_FILE", str(tmp_path / "claims.json"))
    monkeypatch.setattr(ro, "_claims_registry", None)
    monkeypatch.setattr(m.uw, "has_key", lambda: True)
    monkeypatch.setattr(m.time, "sleep", lambda *_a: None)

    def fake_np(ticker, date=None):
        if ticker not in FLOW:
            return None
        # net_volume carries the sign; volumes make the normaliser sane
        return {"net_volume": FLOW[ticker] * 1000, "call_volume": 700, "put_volume": 300}
    monkeypatch.setattr(m.uw, "net_prem_daily", fake_np)
    return ro.get_claims_registry()


def _cfg(**over):
    base = {"enabled": True, "shadow_only": False, "k_per_leg": 1, "notional_usd": 20.0,
            "leverage": 3, "stop_pct": 20.0, "hold_days": 5.0, "min_volume_usd": 250_000.0}
    base.update(over)
    return {"uw_flow_xs": base}


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, a):
        self.calls.append(a)
        return {"executed": True}


def test_book_registered():
    assert "uw_flow_xs" in ro.active_claim_books()


def test_equity_universe_filter():
    coins = m._equity_coins(UNI, 250_000.0)
    assert "xyz:AAPL" in coins and "xyz:XYZ100" not in coins and "BTC" not in coins


def test_analysis_bounded_sizing():
    a = m._analysis("xyz:AAPL", "long", _cfg()["uw_flow_xs"])
    assert a["strategy_book"] == "uw_flow_xs" and a["side"] == "long"
    assert a["strategy_book_notional"] == 20.0 and a["leverage_override"] == 3
    assert a["backup_sl_pct_override"] == 20.0
    assert a["dsl_exit_override"]["hard_timeout_minutes"] == 5.0 * 1440


def test_shadow_records_but_never_executes(fresh):
    spy = Spy()
    n = m.maybe_run(_cfg(shadow_only=True), UNI, positions=[], execute_fn=spy)
    assert n == 2 and spy.calls == []
    assert fresh.owner_of("xyz:AAPL") is None


def test_live_longs_top_shorts_bottom_and_claims(fresh):
    spy = Spy()
    n = m.maybe_run(_cfg(shadow_only=False, k_per_leg=1), UNI, positions=[], execute_fn=spy)
    assert n == 2 and len(spy.calls) == 2
    sides = {a["coin"]: a["side"] for a in spy.calls}
    assert sides["xyz:AAPL"] == "long"      # most bullish flow -> long
    assert sides["xyz:MU"] == "short"        # most bearish flow -> short
    assert fresh.owner_of("xyz:AAPL") == "uw_flow_xs"
    assert fresh.owner_of("xyz:MU") == "uw_flow_xs"


def test_daily_dedup(fresh):
    spy = Spy()
    assert m.maybe_run(_cfg(), UNI, [], spy) == 2
    assert m.maybe_run(_cfg(), UNI, [], spy) == 0   # same UTC day -> skip
