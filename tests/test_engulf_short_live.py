import os

from hermes_trader.agents import engulf_short_live as es
from hermes_trader.agents import rebalancer_owned as ro


DAY = 86_400_000
NOW_MS = 50 * DAY + 3_600_000
SIGNAL_START = NOW_MS - DAY - 3_600_000  # the last COMPLETED bar lands here


def _bars(seq, vol=250_000, forming=True):
    """seq = list of (o,h,l,c). Places the LAST real bar at SIGNAL_START (so the
    forming bar appended after it is inside the drop window) regardless of length."""
    start = SIGNAL_START - (len(seq) - 1) * DAY
    bars = [{"t": start + i * DAY, "o": o, "h": h, "l": l, "c": c, "v": vol}
            for i, (o, h, l, c) in enumerate(seq)]
    if forming:
        last = bars[-1]
        bars.append({"t": last["t"] + DAY, "o": last["c"], "h": last["c"] * 1.01,
                     "l": last["c"] * 0.99, "c": last["c"], "v": vol})
    return bars


def _engulf_alt_bars():
    """37 flat bars, then green bar (100->104), then a bearish full-body engulf
    (open 105 >= prior close 104, close 99 <= prior open 100, body 6 >= prior body 4)."""
    seq = [(100, 101, 99, 100)] * 37
    seq += [(100, 104.5, 99.5, 104)]      # green
    seq += [(105, 105.5, 98.5, 99)]       # bearish engulf of the green bar
    return _bars(seq)


def _no_engulf_alt_bars():
    seq = [(100, 101, 99, 100)] * 39      # all flat dojis, no engulf
    return _bars(seq)


def _btc_up_bars():
    seq = [(100, 101, 99, 100)] * 20 + [(c, c + 1, c - 1, c) for c in (100 + i * 0.5 for i in range(20))]
    return _bars(seq)


def _btc_down_bars():
    seq = [(100, 101, 99, 100)] * 20 + [(c, c + 1, c - 1, c) for c in (100 - i * 0.5 for i in range(20))]
    return _bars(seq)


def _cfg(**ov):
    cfg = {
        "enabled": True, "shadow_only": False, "scan_interval_hours": 0,
        "entry_window_hours": 8, "min_body_ratio": 1.0, "btc_window": 20,
        "min_volume_usd": 20_000_000, "executor_short_volume_floor_usd": 20_000_000,
        "volume_window": 30, "hold_days": 1, "stop_pct": 20.0, "notional_usd": 20.0,
        "leverage": 1, "max_new_per_cycle": 2, "history_bars": 40,
    }
    cfg.update(ov)
    return {"engulf_short": cfg}


def _setup(monkeypatch):
    ro._claims_registry = None
    captured: list = []
    monkeypatch.setattr(es.shadow_ledger, "record_many",
                        lambda book, rows: captured.append((book, list(rows))) or len(rows))
    for path in (es._SEEN_FILE, es._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    monkeypatch.setattr(es, "log_event", lambda e: None)
    monkeypatch.setattr(es, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(es, "_save_ts", lambda t: None)
    monkeypatch.setattr(es.time, "time", lambda: NOW_MS / 1000.0)
    monkeypatch.setattr(es, "active_position_coins", lambda: {})
    return captured


def _fetch_up(coin, interval, n):
    assert interval == "1d"
    return _btc_up_bars() if coin == "BTC" else _engulf_alt_bars()


def test_detects_bearish_full_engulf():
    bars = _engulf_alt_bars()
    completed = es._completed_bars(bars, NOW_MS)
    assert es._is_bearish_full_engulf(completed[-2], completed[-1], 1.0) is True


def test_live_opens_short_with_overrides(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = es.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp", "dayNtlVlm": 30_000_000}],
                       [], _fetch_up, lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is False
    assert rec["opened"] == 1
    a = calls[0]
    assert a["side"] == "short" and a["strategy_book"] == "engulf_short"
    assert a["backup_sl_pct_override"] == 20.0
    assert a["dsl_exit_override"]["hard_timeout_minutes"] == 1 * 1440
    assert a["leverage_override"] == 1


def test_shadow_records_all_regimes_no_gate(monkeypatch):
    """No BTC-regime gate: it must record even in a DOWN tape (so the forward grader can split)."""
    captured = _setup(monkeypatch)
    calls = []

    def _fetch_down(coin, interval, n):
        return _btc_down_bars() if coin == "BTC" else _engulf_alt_bars()

    rec = es.maybe_run(_cfg(shadow_only=True), [{"coin": "ALT", "type": "perp"}],
                       [], _fetch_down, lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is True
    assert rec["signals"] == 1          # fired despite BTC-down
    assert rec["btc_up"] is False
    assert calls == []                  # zero capital
    book, rows = captured[0]
    assert book == "engulf_short"
    assert rows[0]["side"] == "short" and rows[0]["meta"]["btc_up"] is False


def test_no_engulf_no_signal(monkeypatch):
    _setup(monkeypatch)
    calls = []

    def _fetch_flat(coin, interval, n):
        return _btc_up_bars() if coin == "BTC" else _no_engulf_alt_bars()

    rec = es.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp"}],
                       [], _fetch_flat, lambda a: calls.append(a) or {"executed": True})
    assert rec["signals"] == 0
    assert calls == []


def test_volume_floor_filters(monkeypatch):
    _setup(monkeypatch)
    calls = []

    def _fetch_thin(coin, interval, n):
        if coin == "BTC":
            return _btc_up_bars()
        seq = [(100, 101, 99, 100)] * 37 + [(100, 104.5, 99.5, 104), (105, 105.5, 98.5, 99)]
        return _bars(seq, vol=10.0)

    rec = es.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp"}],
                       [], _fetch_thin, lambda a: calls.append(a) or {"executed": True})
    assert rec["signals"] == 0
    assert calls == []


def test_btc_excluded(monkeypatch):
    _setup(monkeypatch)
    rec = es.maybe_run(_cfg(), [{"coin": "BTC", "type": "perp"}, {"coin": "ALT", "type": "perp"}],
                       [], _fetch_up, lambda a: {"executed": True})
    assert all(s["coin"] != "BTC" for s in rec["candidates"])
    assert rec["opened"] == 1


def test_blocked_executor_releases_claim(monkeypatch):
    _setup(monkeypatch)
    rec = es.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp"}],
                       [], _fetch_up, lambda a: {"executed": False, "reason": "blocked"})
    assert rec["opened"] == 0
    assert rec["skipped"]["blocked"] == 1
    assert ro.get_claims_registry().owner_of("ALT") is None


def test_skips_held_coin(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = es.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp"}],
                       [{"position": {"coin": "ALT", "szi": "-1.0"}}],
                       _fetch_up, lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 0
    assert rec["skipped"]["held"] == 1
    assert calls == []
