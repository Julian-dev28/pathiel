"""Trade-outcome store: record_close → win-rate / payoff → risk-of-ruin.

Uses a fresh AgentMemory instance with flush() stubbed so the live
.agent-memory.json is never touched.
"""

from hermes_trader.agents.memory import AgentMemory
from hermes_trader.agents.sizing import risk_of_ruin


def _mem():
    m = AgentMemory()
    m.flush = lambda: None  # never write to disk in tests
    return m


def _close(pnl_pct, spot=None):
    return {
        "coin": "X", "side": "long", "entry_px": 100.0, "exit_px": 101.0,
        "realized_pnl_pct": pnl_pct, "spot_pct": spot if spot is not None else pnl_pct,
        "realized_pnl_usd": pnl_pct, "leverage": 1, "closed_at": 0,
    }


def test_record_close_feeds_win_rate():
    m = _mem()
    for p in (5.0, -2.0, 8.0, -3.0):  # 2 wins, 2 losses
        m.record_close(_close(p))
    wr = m.get_win_rate()
    assert wr["total"] == 4
    assert wr["wins"] == 2
    assert wr["rate"] == 0.5


def test_payoff_stats_computed():
    m = _mem()
    for p in (10.0, 10.0, -5.0):  # avg win 10, avg loss 5 -> payoff 2.0, win_rate 2/3
        m.record_close(_close(p))
    s = m.get_payoff_stats()
    assert s["n"] == 3
    assert abs(s["win_rate"] - 2 / 3) < 1e-9
    assert abs(s["avg_win_pct"] - 10.0) < 1e-9
    assert abs(s["avg_loss_pct"] - 5.0) < 1e-9
    assert abs(s["payoff_ratio"] - 2.0) < 1e-9


def test_payoff_stats_empty_is_zero_not_crash():
    s = _mem().get_payoff_stats()
    assert s == {"n": 0, "win_rate": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0, "payoff_ratio": 0.0}


def test_outcome_store_drives_risk_of_ruin():
    m = _mem()
    # A solid edge: 60% win, payoff 2.0
    for _ in range(6):
        m.record_close(_close(10.0))
    for _ in range(4):
        m.record_close(_close(-5.0))
    s = m.get_payoff_stats()
    ror = risk_of_ruin(win_rate=s["win_rate"], payoff_ratio=s["payoff_ratio"], risk_per_trade_pct=0.0075)
    assert s["win_rate"] == 0.6 and abs(s["payoff_ratio"] - 2.0) < 1e-9
    assert 0.0 <= ror < 0.05  # strong edge + small risk -> low ruin


def test_cap_bounds_closes_list():
    m = _mem()
    from hermes_trader.agents import memory as _memmod
    for i in range(_memmod.MAX_CLOSES + 50):
        m.record_close(_close(1.0))
    assert len(m._closes) == _memmod.MAX_CLOSES


def test_get_win_rate_falls_back_when_no_closes():
    m = _mem()  # no closes recorded
    wr = m.get_win_rate()
    assert wr["total"] == 0 and wr["rate"] == 0
