"""Offline gate test for the verdict-rule backtest eval's PURE logic
(services/strategies/evals/verdict_backtest.py). The eval's __main__ hits
live HL data and is a periodic/nightly run, not a commit gate — this file
only exercises walk_forward_liq_check/evaluate on synthetic candles plus
run_for_desk/main with fully injected (offline) fetchers, so the eval's own
LOGIC has fast, deterministic regression coverage."""
from __future__ import annotations

import random

from services.strategies.evals import verdict_backtest as VB
from services.strategies.tests.conftest import make_candles


def test_hold_liquidation_rate_hand_computed():
    results = [("hold", False), ("hold", True), ("hold", False), ("dip_only", True)]
    assert VB.hold_liquidation_rate(results) == 1 / 3


def test_hold_liquidation_rate_none_when_no_holds():
    assert VB.hold_liquidation_rate([("dip_only", True), ("avoid", True)]) is None


def test_evaluate_pass_and_fail_thresholds():
    good = VB.evaluate([("hold", False)] * 9 + [("hold", True)], pass_threshold=0.15)
    assert good["hold_liq_rate"] == 0.1
    assert good["passed"] is True

    bad = VB.evaluate([("hold", True)] * 3 + [("hold", False)] * 7, pass_threshold=0.15)
    assert bad["hold_liq_rate"] == 0.3
    assert bad["passed"] is False

    empty = VB.evaluate([("dip_only", True)])
    assert empty["hold_liq_rate"] is None
    assert empty["passed"] is True  # vacuously — no hold days to have been wrong about


def test_walk_forward_flags_a_real_liquidation_and_not_a_flat_stretch():
    random.seed(11)
    closes = [100.0]
    for _ in range(140):
        closes.append(closes[-1] * (1.0 + random.uniform(-0.01, 0.01)))
    # inject one deep crash well after the warmup, so some trailing windows
    # see it (and should flag actually_liquidated=True for their forward
    # window) and the long flat stretch before it should not.
    closes[100] = closes[99] * 0.5

    candles = make_candles(closes, wiggle=0.02)
    results = VB.walk_forward_liq_check("XYZ", candles, "long", 5.0, 20.0, 7, warmup_days=60)

    assert results, "expected at least some eligible days after warmup"
    assert any(liq for _, liq in results), "the crash should register as an actual liquidation somewhere"
    assert any(not liq for _, liq in results), "the calm stretch should NOT all read as liquidations"


def test_walk_forward_leverage_above_max_returns_empty():
    candles = make_candles([100.0] * 100, wiggle=0.01)
    assert VB.walk_forward_liq_check("XYZ", candles, "long", 25.0, 20.0, 7) == []


def test_run_for_desk_fully_offline_with_injected_fetchers():
    """Uses the REAL oil DeskSpec (registry), but both fetchers are fakes —
    this must never touch the network."""
    random.seed(3)
    closes = [100.0]
    for _ in range(150):
        closes.append(closes[-1] * (1.0 + random.uniform(-0.02, 0.02)))
    candles = make_candles(closes, wiggle=0.015)

    def daily_fetcher(coin):
        return candles

    def meta_fetcher():
        return {"xyz:CL": {"maxLeverage": 20}, "xyz:BRENTOIL": {"maxLeverage": 20},
               "xyz:NATGAS": {"maxLeverage": 10}}

    rows = VB.run_for_desk("oil", daily_fetcher=daily_fetcher, meta_fetcher=meta_fetcher)
    assert rows, "expected rows for oil's primary instrument(s)"
    assert {r["coin"] for r in rows} == {"xyz:BRENTOIL"}   # only the primary instrument (Brent)
    assert {r["side"] for r in rows} == {"long", "short"}
    for r in rows:
        assert "passed" in r and "hold_liq_rate" in r


def test_main_runs_fully_offline_and_prints_verdict(capsys, monkeypatch):
    random.seed(5)
    closes = [100.0]
    for _ in range(150):
        closes.append(closes[-1] * (1.0 + random.uniform(-0.02, 0.02)))
    candles = make_candles(closes, wiggle=0.015)

    monkeypatch.setattr(VB, "_default_daily_fetcher", lambda coin, count=400: candles)
    import services.strategies.analytics as analytics_mod
    monkeypatch.setattr(analytics_mod, "default_meta_fetcher",
                        lambda: {"xyz:CL": {"maxLeverage": 20}, "xyz:BRENTOIL": {"maxLeverage": 20},
                                "xyz:NATGAS": {"maxLeverage": 10}})

    rc = VB.main(["--desk", "oil"])
    out = capsys.readouterr().out
    assert "VERDICT:" in out
    assert rc in (0, 1)
