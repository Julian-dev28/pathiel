import os

from hermes_trader.agents import rally_exhaustion_live as rel
from hermes_trader.agents import rebalancer_owned as ro


DAY = 86_400_000
NOW_MS = 50 * DAY + 3_600_000


def _bars_from_closes(closes, start_t=0, vol=250_000, forming=True):
    bars = [
        {"t": start_t + i * DAY, "o": c, "h": c * 1.02, "l": c * 0.98, "c": c, "v": vol}
        for i, c in enumerate(closes)
    ]
    if forming:
        last = bars[-1]
        bars.append({
            "t": last["t"] + DAY,
            "o": last["c"],
            "h": last["c"] * 1.01,
            "l": last["c"] * 0.99,
            "c": last["c"],
            "v": vol,
        })
    return bars


def _fresh_alt_bars():
    signal_start = NOW_MS - DAY - 3_600_000
    start = signal_start - 39 * DAY
    closes = [100.0] * 37 + [100.0, 106.0, 113.0]
    return _bars_from_closes(closes, start_t=start)


def _btc_down_bars():
    signal_start = NOW_MS - DAY - 3_600_000
    start = signal_start - 39 * DAY
    closes = [100.0] * 20 + [100.0 - i * 0.5 for i in range(20)]
    return _bars_from_closes(closes, start_t=start)


def _cfg(**overrides):
    cfg = {
        "enabled": True,
        "scan_interval_hours": 0,
        "entry_window_hours": 8,
        "lookback_days": 2,
        "threshold_pct": 12.0,
        "btc_window": 20,
        "min_volume_usd": 20_000_000,
        "volume_window": 30,
        "history_bars": 40,
        "hold_days": 5,
        "stop_pct": 25.0,
        "notional_usd": 20.0,
        "leverage": 1,
        "executor_short_volume_floor_usd": 20_000_000,
        "max_new_per_cycle": 1,
    }
    cfg.update(overrides)
    return {"rally_exhaustion": cfg}


def _setup(monkeypatch):
    ro._claims_registry = None
    for path in (rel._SEEN_FILE, rel._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    monkeypatch.setattr(rel, "log_event", lambda e: None)
    monkeypatch.setattr(rel, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(rel, "_save_ts", lambda t: None)
    monkeypatch.setattr(rel.time, "time", lambda: NOW_MS / 1000.0)
    monkeypatch.setattr(rel, "active_position_coins", lambda: {})


def _fetch(coin, interval, n):
    assert interval == "1d"
    return _btc_down_bars() if coin == "BTC" else _fresh_alt_bars()


def test_live_opens_with_strategy_specific_executor_overrides(monkeypatch):
    _setup(monkeypatch)
    calls = []

    rec = rel.maybe_run(
        _cfg(),
        [{"coin": "ALT", "type": "perp", "dayNtlVlm": 30_000_000}],
        [],
        _fetch,
        lambda a: calls.append(a) or {"executed": True},
    )

    assert rec["opened"] == 1
    assert len(calls) == 1
    analysis = calls[0]
    assert analysis["coin"] == "ALT"
    assert analysis["side"] == "short"
    assert analysis["strategy_book"] == "rally_exhaustion"
    assert analysis["strategy_book_notional"] == 20.0
    assert analysis["leverage_override"] == 1
    assert analysis["backup_sl_pct_override"] == 25.0
    assert analysis["tp_scale_fraction_override"] == 0.0
    assert analysis["min_short_volume_usd_override"] == 20_000_000
    assert analysis["dsl_exit_override"]["atr_stop"]["enabled"] is False
    assert analysis["dsl_exit_override"]["hard_timeout_minutes"] == 5 * 1440


def test_blocked_executor_releases_claim(monkeypatch):
    _setup(monkeypatch)

    rec = rel.maybe_run(
        _cfg(),
        [{"coin": "ALT", "type": "perp"}],
        [],
        _fetch,
        lambda a: {"executed": False, "reason": "blocked_in_test"},
    )

    assert rec["opened"] == 0
    assert rec["skipped"]["blocked"] == 1
    assert ro.get_claims_registry().owner_of("ALT") is None


def test_skips_held_coin_without_order(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = rel.maybe_run(
        _cfg(),
        [{"coin": "ALT", "type": "perp"}],
        [{"position": {"coin": "ALT", "szi": "-1.0"}}],
        _fetch,
        lambda a: calls.append(a) or {"executed": True},
    )
    assert rec["opened"] == 0
    assert rec["skipped"]["held"] == 1
    assert calls == []
