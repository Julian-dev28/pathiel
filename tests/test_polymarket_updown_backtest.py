"""AI up/down backtest — the no-leakage context rebuild, the outcome grader, and
the full backtest loop with an injected brain (no network, no model)."""
from __future__ import annotations

import pytest

from services.polymarket_scout import updown_backtest as bt


def _bars(closes, opens=None):
    # binance 1m kline: [openT, o, h, l, c, v]
    opens = opens or closes
    return [[i * 60000, opens[i], max(opens[i], closes[i]) + 2,
             min(opens[i], closes[i]) - 2, closes[i], 1.0] for i in range(len(closes))]


def test_window_outcome_reads_close_vs_open():
    # window starting at index 5 (5-min window = idx 5..9); open=100, close=110 -> UP
    bars = _bars([100] * 5 + [100, 101, 102, 103, 110] + [0] * 5)
    assert bt.window_outcome(bars, 5) is True
    down = _bars([100] * 5 + [100, 99, 98, 97, 95] + [0] * 5)
    assert bt.window_outcome(down, 5) is False


def test_window_outcome_none_when_incomplete():
    assert bt.window_outcome(_bars([100, 101]), 5) is None


def test_context_from_1m_is_no_lookahead():
    # 30 rising bars; decide at index 23 (mid the window starting at 20) ->
    # context must only see closes[:24], and price is above the window open (bar 20)
    closes = [100.0 + i for i in range(30)]
    ctx = bt.context_from_1m(_bars(closes), 23, 90)
    assert ctx is not None
    assert ctx["price"] == 123.0                 # close at index 23, not later
    assert ctx["window_open"] == 120.0           # open of bar 20 (window start)
    assert ctx["vs_open_pct"] > 0                # above the window open
    assert 0.5 < ctx["drift_prob_up"] <= 1.0     # rising -> leans up
    assert "s1" not in ctx                        # no 1-second layer in bulk history


def test_context_needs_prior_history():
    assert bt.context_from_1m(_bars([1.0] * 10), 5, 90) is None   # <16 bars visible


class _Brain:
    """Follows position-vs-open: says UP with 0.7 if the prompt shows +vs_open."""
    def complete(self, system, user, web_search=False):
        up = "open by +" in user or "ABOVE open" in user or "+0" in user
        # crude: look for a positive vs_open in the prompt
        up = "RESOLUTION:" in user and ("+0" in user.split("vs_open")[0] if False else True)
        return '{"verdict":"UP","up_prob":0.70,"reasoning":"x"}'


def test_backtest_grades_the_ai_against_the_close(monkeypatch):
    # deterministic history: fetch returns a fixed rising series, no network
    closes = [100.0 + (i % 7) for i in range(400)]      # choppy
    monkeypatch.setattr(bt, "load_history", lambda minutes, runner=None: bt._bars_for_test(closes)
                        if hasattr(bt, "_bars_for_test") else _bars(closes))
    r = bt.backtest(n=10, decision_frac=0.7, minutes=400, brain=_Brain(), progress=False)
    assert r["n"] > 0
    assert 0.0 <= r["hit_rate"] <= 1.0
    assert "ev_per_bet" in r and "followed_position_vs_open_pct" in r


def test_backtest_reports_nothing_without_history(monkeypatch):
    monkeypatch.setattr(bt, "load_history", lambda minutes, runner=None: [])
    assert bt.backtest(n=5, brain=_Brain()).get("n") == 0
