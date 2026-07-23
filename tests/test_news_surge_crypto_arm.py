"""Gate tests for the news_surge_short CRYPTO arm (W-SOC1 go-live, 2026-07-23).

The money-critical invariant: crypto breaking surges size $20/1x (conservative, one tape),
xyz-equity surges keep the original geometry, and the two are gated by independent flags.
"""
import types

from hermes_trader.agents import news_surge_short_live as m


def _rep():
    return types.SimpleNamespace(headlines=[], surge_x=4.0, n_recent=2, breaking=True)


CFG = {"crypto_leverage": 1, "crypto_notional_usd": 20.0, "crypto_stop_pct": 15.0,
       "leverage": 6, "notional_usd": 20.0, "stop_pct": 6.0, "hold_days": 1.0}


def test_is_xyz_equity():
    assert m._is_xyz_equity("xyz:AAPL") is True
    assert m._is_xyz_equity("PEPE") is False
    assert m._is_xyz_equity("BTC") is False


def test_crypto_arm_sizes_20x1():
    a = m._analysis("PEPE", _rep(), CFG)
    assert a["strategy_book"] == "news_surge_short" and a["side"] == "short"
    assert a["strategy_book_notional"] == 20.0
    assert a["leverage_override"] == 1                      # 1x, not the equity 6/10x
    assert a["backup_sl_pct_override"] == 15.0
    assert a["dsl_exit_override"]["max_loss_pct"] == 15.0


def test_equity_arm_keeps_its_own_geometry():
    a = m._analysis("xyz:AAPL", _rep(), CFG)
    assert a["strategy_book_notional"] == 20.0
    assert a["leverage_override"] == 6                      # cfg equity leverage
    assert a["backup_sl_pct_override"] == 6.0


def test_trade_filter_gates_each_class_independently():
    """Replicate the maybe_run filter predicate: crypto_live gates crypto, equity_live gates
    equity — with equity OFF and crypto ON, only crypto breaking rows trade."""
    rows = [
        {"coin": "PEPE", "meta": {"breaking": True, "equity": False}},
        {"coin": "xyz:AAPL", "meta": {"breaking": True, "equity": True}},
        {"coin": "SOL", "meta": {"breaking": False, "equity": False}},  # not breaking
    ]
    equity_live, crypto_live = False, True
    sel = [r["coin"] for r in rows if r["meta"]["breaking"] and (
        (r["meta"]["equity"] and equity_live) or (not r["meta"]["equity"] and crypto_live))]
    assert sel == ["PEPE"]                                  # crypto breaking only
