import os

from hermes_trader.agents import premium_fade_short_live as pfs
from hermes_trader.agents import rebalancer_owned as ro


DAY = 86_400_000
NOW_MS = 50 * DAY + 3_600_000
SIGNAL_START = NOW_MS - DAY - 3_600_000


def _daily_bars(n=40, vol=250_000, close=100.0):
    """n completed daily bars ending at SIGNAL_START, + a forming bar (dropped).
    close=100 * vol=250k => $25M trailing dvol, clears the $20M floor."""
    start = SIGNAL_START - (n - 1) * DAY
    bars = [{"t": start + i * DAY, "o": close, "h": close * 1.02, "l": close * 0.98,
             "c": close, "v": vol} for i in range(n)]
    last = bars[-1]
    bars.append({"t": last["t"] + DAY, "o": close, "h": close * 1.01,
                 "l": close * 0.99, "c": close, "v": vol})
    return bars


def _stale_daily_bars(n=40, vol=250_000, close=100.0, age_days=12):
    """Daily bars whose LAST completed bar is `age_days` old (stale price feed, e.g. TON)."""
    last_t = NOW_MS - age_days * DAY
    start = last_t - (n - 1) * DAY
    return [{"t": start + i * DAY, "o": close, "h": close * 1.02, "l": close * 0.98,
             "c": close, "v": vol} for i in range(n)]


def _spike_funding(last_premium):
    """31 daily premium rows ending at NOW_MS; flat-ish history + a final spike."""
    rows = []
    for i in range(31):
        t = NOW_MS - (30 - i) * DAY
        prem = 0.0001 * (1 + 0.1 * (i % 2))      # small noise so stdev > 0
        if i == 30:
            prem = last_premium
        rows.append({"time": t, "fundingRate": 0.0, "premium": prem})
    return rows


def _cfg(**ov):
    cfg = {
        "enabled": True, "shadow_only": False, "scan_interval_hours": 0,
        "z_threshold": 2.0, "premium_lookback_days": 30, "btc_window": 20,
        "min_volume_usd": 20_000_000, "executor_short_volume_floor_usd": 20_000_000,
        "volume_window": 30, "max_eval_coins": 60, "hold_days": 5, "stop_pct": 20.0,
        "notional_usd": 20.0, "leverage": 1, "max_new_per_cycle": 1, "history_bars": 40,
    }
    cfg.update(ov)
    return {"premium_fade_short": cfg}


def _setup(monkeypatch, spike_premium=0.02):
    ro._claims_registry = None
    captured: list = []
    monkeypatch.setattr(pfs.shadow_ledger, "record_many",
                        lambda book, rows: captured.append((book, list(rows))) or len(rows))
    # ALT spikes; everyone else (incl BTC) flat
    monkeypatch.setattr(pfs, "_fetch_funding",
                        lambda coin, start: _spike_funding(spike_premium if coin == "ALT" else 0.0001))
    for path in (pfs._SEEN_FILE, pfs._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    monkeypatch.setattr(pfs, "log_event", lambda e: None)
    monkeypatch.setattr(pfs, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(pfs, "_save_ts", lambda t: None)
    monkeypatch.setattr(pfs.time, "time", lambda: NOW_MS / 1000.0)
    monkeypatch.setattr(pfs, "active_position_coins", lambda: {})
    return captured


def _fetch(coin, interval, n):
    assert interval == "1d"
    return _daily_bars()


def test_premium_z_detects_spike(monkeypatch):
    monkeypatch.setattr(pfs, "_fetch_funding", lambda coin, start: _spike_funding(0.02))
    z = pfs._premium_z("ALT", NOW_MS, 30)
    assert z is not None and z >= 2.0


def test_premium_z_none_when_flat(monkeypatch):
    monkeypatch.setattr(pfs, "_fetch_funding", lambda coin, start: _spike_funding(0.0001))
    z = pfs._premium_z("ALT", NOW_MS, 30)
    assert z is None or z < 2.0


def test_live_opens_short_with_overrides(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = pfs.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp", "dayNtlVlm": 30_000_000}],
                        [], _fetch, lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is False
    assert rec["opened"] == 1
    a = calls[0]
    assert a["side"] == "short" and a["strategy_book"] == "premium_fade_short"
    assert a["backup_sl_pct_override"] == 20.0
    assert a["dsl_exit_override"]["hard_timeout_minutes"] == 5 * 1440
    assert a["leverage_override"] == 1


def test_shadow_records_no_regime_gate(monkeypatch):
    captured = _setup(monkeypatch)
    calls = []
    rec = pfs.maybe_run(_cfg(shadow_only=True), [{"coin": "ALT", "type": "perp", "dayNtlVlm": 30_000_000}],
                        [], _fetch, lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is True
    assert rec["signals"] == 1
    assert calls == []
    book, rows = captured[0]
    assert book == "premium_fade_short"
    assert rows[0]["side"] == "short" and rows[0]["meta"]["premium_z"] >= 2.0


def test_below_threshold_no_signal(monkeypatch):
    _setup(monkeypatch, spike_premium=0.0001)   # no spike -> z under threshold
    calls = []
    rec = pfs.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp", "dayNtlVlm": 30_000_000}],
                        [], _fetch, lambda a: calls.append(a) or {"executed": True})
    assert rec["signals"] == 0
    assert calls == []


def test_volume_floor_filters(monkeypatch):
    _setup(monkeypatch)
    calls = []

    def _fetch_thin(coin, interval, n):
        return _daily_bars(vol=10.0)

    rec = pfs.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp"}],
                        [], _fetch_thin, lambda a: calls.append(a) or {"executed": True})
    assert rec["signals"] == 0
    assert calls == []


def test_btc_excluded_from_candidates(monkeypatch):
    _setup(monkeypatch)
    # BTC also "spikes" but must be excluded
    monkeypatch.setattr(pfs, "_fetch_funding", lambda coin, start: _spike_funding(0.02))
    rec = pfs.maybe_run(_cfg(), [{"coin": "BTC", "type": "perp"}, {"coin": "ALT", "type": "perp"}],
                        [], _fetch, lambda a: {"executed": True})
    assert all(s["coin"] != "BTC" for s in rec["candidates"])


def test_stale_price_candle_skipped(monkeypatch):
    """A live premium spike on a coin with a stale daily candle (TON-like) must NOT record —
    no valid current entry reference."""
    _setup(monkeypatch)   # ALT premium spikes
    calls = []

    def _fetch_stale(coin, interval, n):
        return _stale_daily_bars()   # last bar 12 days old

    rec = pfs.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp", "dayNtlVlm": 30_000_000}],
                        [], _fetch_stale, lambda a: calls.append(a) or {"executed": True})
    assert rec["signals"] == 0
    assert calls == []


def test_blocked_executor_releases_claim(monkeypatch):
    _setup(monkeypatch)
    rec = pfs.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp"}],
                        [], _fetch, lambda a: {"executed": False, "reason": "blocked"})
    assert rec["opened"] == 0
    assert rec["skipped"]["blocked"] == 1
    assert ro.get_claims_registry().owner_of("ALT") is None


def test_skips_held_coin(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = pfs.maybe_run(_cfg(), [{"coin": "ALT", "type": "perp"}],
                        [{"position": {"coin": "ALT", "szi": "-1.0"}}],
                        _fetch, lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 0
    assert rec["skipped"]["held"] == 1
    assert calls == []
