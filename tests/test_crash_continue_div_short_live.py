import json
import os

from hermes_trader.agents import crash_continue_div_short_live as ccd
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


def _diverging_alt_bars():
    # 2-day completed-bar return = 91/100 - 1 = -9% (<= -8% threshold).
    signal_start = NOW_MS - DAY - 3_600_000
    start = signal_start - 39 * DAY
    closes = [100.0] * 37 + [100.0, 95.0, 91.0]
    return _bars_from_closes(closes, start_t=start)


def _flat_alt_bars():
    signal_start = NOW_MS - DAY - 3_600_000
    start = signal_start - 39 * DAY
    closes = [100.0] * 40
    return _bars_from_closes(closes, start_t=start)


def _btc_up_bars():
    signal_start = NOW_MS - DAY - 3_600_000
    start = signal_start - 39 * DAY
    closes = [100.0] * 20 + [100.0 + i * 0.5 for i in range(20)]
    return _bars_from_closes(closes, start_t=start)


def _btc_down_bars():
    signal_start = NOW_MS - DAY - 3_600_000
    start = signal_start - 39 * DAY
    closes = [100.0] * 20 + [100.0 - i * 0.5 for i in range(20)]
    return _bars_from_closes(closes, start_t=start)


def _cfg(**overrides):
    cfg = {
        "enabled": True,
        "shadow_only": False,
        "scan_interval_hours": 0,
        "entry_window_hours": 8,
        "lookback_days": 2,
        "threshold_pct": 8.0,
        "btc_window": 20,
        "min_volume_usd": 20_000_000,
        "volume_window": 30,
        "history_bars": 40,
        "hold_days": 10,
        "stop_pct": 8.0,
        "notional_usd": 20.0,
        "leverage": 1,
        "executor_short_volume_floor_usd": 20_000_000,
        "max_new_per_cycle": 1,
    }
    cfg.update(overrides)
    return {"crash_continue_div_short": cfg}


def _setup(monkeypatch, tmp_path):
    ro._claims_registry = None
    # redirect shadow jsonl into the test tmp dir so we never touch live state
    monkeypatch.setattr(ccd, "_SHADOW_FILE", str(tmp_path / "shadow.jsonl"))
    for path in (ccd._SEEN_FILE, ccd._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    monkeypatch.setattr(ccd, "log_event", lambda e: None)
    monkeypatch.setattr(ccd, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(ccd, "_save_ts", lambda t: None)
    monkeypatch.setattr(ccd.time, "time", lambda: NOW_MS / 1000.0)
    monkeypatch.setattr(ccd, "active_position_coins", lambda: {})


def _fetch_up(coin, interval, n):
    assert interval == "1d"
    return _btc_up_bars() if coin == "BTC" else _diverging_alt_bars()


def test_live_opens_short_with_strategy_overrides(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    calls = []
    rec = ccd.maybe_run(
        _cfg(),
        [{"coin": "ALT", "type": "perp", "dayNtlVlm": 30_000_000}],
        [],
        _fetch_up,
        lambda a: calls.append(a) or {"executed": True},
    )
    assert rec["shadow"] is False
    assert rec["opened"] == 1
    assert len(calls) == 1
    a = calls[0]
    assert a["coin"] == "ALT"
    assert a["side"] == "short"
    assert a["strategy_book"] == "crash_continue_div_short"
    assert a["strategy_book_notional"] == 20.0
    assert a["leverage_override"] == 1
    assert a["backup_sl_pct_override"] == 8.0
    assert a["tp_scale_fraction_override"] == 0.0
    assert a["min_short_volume_usd_override"] == 20_000_000
    assert a["dsl_exit_override"]["atr_stop"]["enabled"] is False
    assert a["dsl_exit_override"]["hard_timeout_minutes"] == 10 * 1440


def test_shadow_mode_logs_but_never_executes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    calls = []
    rec = ccd.maybe_run(
        _cfg(shadow_only=True),
        [{"coin": "ALT", "type": "perp", "dayNtlVlm": 30_000_000}],
        [],
        _fetch_up,
        lambda a: calls.append(a) or {"executed": True},
    )
    assert rec["shadow"] is True
    assert rec["opened"] == 0
    assert rec["signals"] == 1
    assert calls == []  # zero capital allocated
    # forward-validation record written for offline grading
    lines = (tmp_path / "shadow.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["coin"] == "ALT"
    assert row["btc_up"] is True
    assert row["move_pct"] <= -8.0
    assert row["stop_pct"] == 8.0


def test_btc_down_regime_blocks_all_signals(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    calls = []

    def _fetch_down(coin, interval, n):
        return _btc_down_bars() if coin == "BTC" else _diverging_alt_bars()

    rec = ccd.maybe_run(
        _cfg(),
        [{"coin": "ALT", "type": "perp"}],
        [],
        _fetch_down,
        lambda a: calls.append(a) or {"executed": True},
    )
    assert rec["btc_up"] is False
    assert rec["signals"] == 0
    assert calls == []


def test_insufficient_divergence_no_signal(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    calls = []

    def _fetch_flat(coin, interval, n):
        return _btc_up_bars() if coin == "BTC" else _flat_alt_bars()

    rec = ccd.maybe_run(
        _cfg(),
        [{"coin": "ALT", "type": "perp"}],
        [],
        _fetch_flat,
        lambda a: calls.append(a) or {"executed": True},
    )
    assert rec["signals"] == 0
    assert calls == []


def test_volume_floor_filters_thin_coin(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    calls = []

    def _fetch_thin(coin, interval, n):
        if coin == "BTC":
            return _btc_up_bars()
        # same shape but tiny volume -> dvol well under the floor
        signal_start = NOW_MS - DAY - 3_600_000
        start = signal_start - 39 * DAY
        closes = [100.0] * 37 + [100.0, 95.0, 91.0]
        return _bars_from_closes(closes, start_t=start, vol=100.0)

    rec = ccd.maybe_run(
        _cfg(),
        [{"coin": "ALT", "type": "perp"}],
        [],
        _fetch_thin,
        lambda a: calls.append(a) or {"executed": True},
    )
    assert rec["signals"] == 0
    assert calls == []


def test_btc_excluded_from_candidates(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    calls = []

    def _fetch_btc_diverging(coin, interval, n):
        # BTC up by regime window but the trailing 2d also -9%; must still be excluded as a candidate
        if coin == "BTC":
            return _btc_up_bars()
        return _diverging_alt_bars()

    rec = ccd.maybe_run(
        _cfg(),
        [{"coin": "BTC", "type": "perp"}, {"coin": "ALT", "type": "perp"}],
        [],
        _fetch_btc_diverging,
        lambda a: calls.append(a) or {"executed": True},
    )
    assert all(s["coin"] != "BTC" for s in rec["candidates"])
    assert rec["opened"] == 1


def test_blocked_executor_releases_claim(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    rec = ccd.maybe_run(
        _cfg(),
        [{"coin": "ALT", "type": "perp"}],
        [],
        _fetch_up,
        lambda a: {"executed": False, "reason": "blocked_in_test"},
    )
    assert rec["opened"] == 0
    assert rec["skipped"]["blocked"] == 1
    assert ro.get_claims_registry().owner_of("ALT") is None


def test_skips_held_coin_without_order(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    calls = []
    rec = ccd.maybe_run(
        _cfg(),
        [{"coin": "ALT", "type": "perp"}],
        [{"position": {"coin": "ALT", "szi": "-1.0"}}],
        _fetch_up,
        lambda a: calls.append(a) or {"executed": True},
    )
    assert rec["opened"] == 0
    assert rec["skipped"]["held"] == 1
    assert calls == []
