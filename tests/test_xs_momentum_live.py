"""Cross-sectional momentum LIVE wiring tests — timer gating and diff execution."""
import json
import os
import tempfile
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


_CFG = {"xs_momentum": {"enabled": True, "lookback_days": 1,
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


def test_due_rebalance_executes_book(monkeypatch):
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)           # timer due
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)
    ex, cl, ef, cf = _spies()
    plan = xl.maybe_rebalance(_CFG, _uni(_RETS), [], _fetch_factory(_RETS), ef, cf)
    assert plan["open_long"] == ["A", "B"] and plan["open_short"] == ["E", "F"]
    assert {a["coin"] for a in ex} == {"A", "B", "E", "F"}
    assert not cl


def test_live_executes_the_diff(monkeypatch, tmp_path):
    cfg = _CFG
    # Pre-populate the owned state: xs_momentum previously opened C as long
    owned_path = str(tmp_path / ".xs_momentum_positions.json")
    with open(owned_path, "w") as fh:
        json.dump({"longs": ["C"], "shorts": []}, fh)
    monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(xl, "_owned", None)

    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)
    ex, cl, ef, cf = _spies()
    # current book holds C (long, should be closed) — not in target longs [A,B]
    positions = [{"position": {"coin": "C", "szi": 1.0}}]
    xl.maybe_rebalance(cfg, _uni(_RETS), positions, _fetch_factory(_RETS), ef, cf)
    opened = {a["coin"]: a["side"] for a in ex}
    assert opened == {"A": "long", "B": "long", "E": "short", "F": "short"}
    assert cl == ["C"]                                         # C dropped → closed (we owned it)
    # every opened analysis is strategy_book (bypasses thought-engine gates, safety gates apply)
    assert all(a["strategy_book"] == "xs_momentum" for a in ex)


def test_blocked_execute_does_not_record_owned_or_claim(monkeypatch, tmp_path):
    cfg = _CFG
    owned_path = str(tmp_path / ".xs_momentum_positions.json")
    monkeypatch.setattr(xl, "_OWNED_FILE", owned_path)
    monkeypatch.setattr(xl, "_owned", None)

    # Reset the shared claims singleton so this assertion is isolated from prior tests.
    import hermes_trader.agents.rebalancer_owned as ro
    ro._claims_registry = None

    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(xl, "_save_ts", lambda t: None)
    monkeypatch.setattr(xl, "log_event", lambda e: None)
    called = []

    def blocked(a):
        called.append(a)
        return {"executed": False, "reason": "blocked_in_test"}

    plan = xl.maybe_rebalance(cfg, _uni(_RETS), [], _fetch_factory(_RETS), blocked, lambda c: None)
    assert plan["open_long"] == ["A", "B"]
    assert plan["open_short"] == ["E", "F"]
    assert {a["coin"] for a in called} == {"A", "B", "E", "F"}
    assert xl._get_owned().current_book() == ([], [])
    claims = xl.get_claims_registry()
    assert all(claims.owner_of(c) is None for c in ("A", "B", "E", "F"))


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(xl, "_last_ts", lambda: 0.0)
    cfg = {"xs_momentum": {"enabled": False}}
    ex, cl, ef, cf = _spies()
    assert xl.maybe_rebalance(cfg, _uni(_RETS), [], _fetch_factory(_RETS), ef, cf) is None
    assert not ex and not cl
