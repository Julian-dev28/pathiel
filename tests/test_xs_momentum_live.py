"""Cross-sectional momentum LIVE wiring tests — timer gating, shadow=no-orders, live=diff-exec."""
import hermes_trader.agents.xs_momentum_live as xl


def _fetch_factory(rets):
    """fetch(coin, interval, n) -> 2-bar series whose lb=1 trailing return is rets[coin]."""
    def fetch(coin, interval, n):
        r = rets[coin]
        return [{"t": 0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1},
                {"t": 1, "o": 100 * (1 + r), "h": 100 * (1 + r), "l": 100 * (1 + r),
                 "c": 100 * (1 + r), "v": 1}]
    return fetch


def _uni(coins):
    return [{"coin": c, "dayNtlVlm": 1e8, "type": "perp"} for c in coins]


_CFG = {"xs_momentum": {"enabled": True, "shadow_mode": True, "lookback_days": 1,
                        "hold_days": 10, "k_per_leg": 2, "universe_top_n": 50, "min_volume_usd": 1e6}}
_RETS = {"A": 0.50, "B": 0.20, "C": 0.05, "D": -0.05, "E": -0.20, "F": -0.40}


def _spies():
    ex, cl = [], []
    return ex, cl, (lambda a: ex.append(a)), (lambda c: cl.append(c))


def test_timer_gates_rebalance(monkeypatch):
    import time
    monkeypatch.setattr(xl, "_last_ts", lambda: time.time())   # just rebalanced → blocked
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    ex, cl, ef, cf = _spies()
    assert xl.maybe_rebalance(_CFG, _uni(_RETS), [], _fetch_factory(_RETS), ef, cf) is None
    assert not ex and not cl


def test_shadow_builds_book_but_places_no_orders(monkeypatch):
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)           # timer due
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)
    ex, cl, ef, cf = _spies()
    plan = xl.maybe_rebalance(_CFG, _uni(_RETS), [], _fetch_factory(_RETS), ef, cf)
    assert plan["open_long"] == ["A", "B"] and plan["open_short"] == ["E", "F"]
    assert not ex and not cl                                   # SHADOW: nothing executed


def test_live_executes_the_diff(monkeypatch):
    cfg = {"xs_momentum": {**_CFG["xs_momentum"], "shadow_mode": False}}
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)
    ex, cl, ef, cf = _spies()
    # current book holds C (long, should be closed) — not in target longs [A,B]
    positions = [{"position": {"coin": "C", "szi": 1.0}}]
    xl.maybe_rebalance(cfg, _uni(_RETS), positions, _fetch_factory(_RETS), ef, cf)
    opened = {a["coin"]: a["side"] for a in ex}
    assert opened == {"A": "long", "B": "long", "E": "short", "F": "short"}
    assert cl == ["C"]                                         # C dropped → closed
    # every opened analysis is external_alpha (bypasses thought-engine gates, safety gates apply)
    assert all(a["external_alpha"] == "xs_momentum" for a in ex)


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    cfg = {"xs_momentum": {"enabled": False}}
    ex, cl, ef, cf = _spies()
    assert xl.maybe_rebalance(cfg, _uni(_RETS), [], _fetch_factory(_RETS), ef, cf) is None
    assert not ex and not cl
