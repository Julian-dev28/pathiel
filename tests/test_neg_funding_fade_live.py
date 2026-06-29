import os

from hermes_trader.agents import neg_funding_fade_live as nf
from hermes_trader.agents import rebalancer_owned as ro


BAR = 300_000  # 5m
NOW_MS = 60_000 * BAR + 7_777
INFLUX_T = NOW_MS - 360_000  # last completed bar closed ~1m ago (fresh + completed)


def _bars(seq, forming=True):
    """seq=(o,h,l,c,v); last real bar = the influx at INFLUX_T; a forming bar trails."""
    start = INFLUX_T - (len(seq) - 1) * BAR
    bars = [{"t": start + i * BAR, "o": o, "h": h, "l": l, "c": c, "v": v}
            for i, (o, h, l, c, v) in enumerate(seq)]
    if forming:
        last = bars[-1]
        bars.append({"t": last["t"] + BAR, "o": last["c"], "h": last["c"] * 1.01,
                     "l": last["c"] * 0.99, "c": last["c"], "v": 100})
    return bars


def _influx_seq(vol=200):
    seq = [(100, 101, 99, 100, 100)] * 6     # flat trailing, vol 100
    seq += [(100, 103, 99.5, 102, vol)]      # GREEN influx, vol 2x
    return _bars(seq)


def _uni(coin="ALT", prev=100.0, mid=110.0, dvol=30_000_000):
    return [{"coin": coin, "type": "perp", "prevDayPx": prev, "midPx": mid, "dayNtlVlm": dvol}]


def _cfg(**ov):
    cfg = {"enabled": True, "shadow_only": False, "scan_interval_minutes": 0,
           "entry_window_minutes": 7, "vol_window": 6, "influx_vol_x": 1.5,
           "funding_max_pct": -0.10, "min_mover_pct": 0.0, "min_volume_usd": 5_000_000,
           "executor_short_volume_floor_usd": 5_000_000, "max_scan_coins": 40,
           "history_bars": 20, "hold_hours": 8.0, "stop_pct": 25.0, "notional_usd": 20.0,
           "leverage": 1, "max_new_per_cycle": 1, "max_book_positions": 3}
    cfg.update(ov)
    return {"neg_funding_fade": cfg}


def _setup(monkeypatch, funding=-0.50):
    ro._claims_registry = None
    captured: list = []
    monkeypatch.setattr(nf.shadow_ledger, "record_many",
                        lambda book, rows: captured.append((book, list(rows))) or len(rows))
    for path in (nf._SEEN_FILE, nf._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    monkeypatch.setattr(nf, "log_event", lambda e: None)
    monkeypatch.setattr(nf, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(nf, "_save_ts", lambda t: None)
    monkeypatch.setattr(nf.time, "time", lambda: NOW_MS / 1000.0)
    def _simple_held(positions):
        held = set()
        for p in positions or []:
            pos = p.get("position", p) if isinstance(p, dict) else {}
            coin = pos.get("coin")
            try:
                szi = float(pos.get("szi", 0) or 0)
            except (TypeError, ValueError):
                szi = 0.0
            if coin and szi != 0:
                held.add(coin)
        return held
    monkeypatch.setattr(nf, "_held_coins", _simple_held)
    monkeypatch.setattr(nf, "_funding_8h_pct", lambda coin, now_ms: funding)
    return captured


def _fetch(coin, interval, n):
    assert interval == "5m"
    return _influx_seq()


def test_influx_signal_detects_green_volume_pop():
    cb = nf._completed_bars(_influx_seq(), NOW_MS)
    sig = nf._influx_signal(cb, 6, 1.5)
    assert sig is not None and sig["influx_vol_x"] == 2.0


def test_live_opens_short_with_overrides(monkeypatch):
    _setup(monkeypatch, funding=-0.50)
    calls = []
    rec = nf.maybe_run(_cfg(), _uni(), [], _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is False and rec["opened"] == 1
    a = calls[0]
    assert a["side"] == "short" and a["strategy_book"] == "neg_funding_fade"
    assert a["backup_sl_pct_override"] == 25.0
    assert a["strategy_book_notional"] == 20.0
    assert a["min_short_volume_usd_override"] == 5_000_000
    assert a["dsl_exit_override"]["hard_timeout_minutes"] == 8.0 * 60


def test_positive_funding_blocks_the_fade(monkeypatch):
    """Funding above the threshold (not crowded-short) must NOT fire."""
    _setup(monkeypatch, funding=0.05)
    calls = []
    rec = nf.maybe_run(_cfg(), _uni(), [], _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["signals"] == 0 and calls == []


def test_funding_unavailable_no_signal(monkeypatch):
    _setup(monkeypatch, funding=None)
    rec = nf.maybe_run(_cfg(), _uni(), [], _fetch, lambda a: {"executed": True})
    assert rec["signals"] == 0


def test_no_influx_no_signal(monkeypatch):
    _setup(monkeypatch, funding=-0.50)
    rec = nf.maybe_run(_cfg(), _uni(), [],
                       lambda c, i, n: _bars([(100, 101, 99, 100, 100)] * 7),  # flat, no influx
                       lambda a: {"executed": True})
    assert rec["signals"] == 0


def test_shadow_records_zero_capital(monkeypatch):
    captured = _setup(monkeypatch, funding=-0.50)
    calls = []
    rec = nf.maybe_run(_cfg(shadow_only=True), _uni(), [], _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is True and rec["signals"] == 1 and calls == []
    book, rows = captured[0]
    assert book == "neg_funding_fade" and rows[0]["side"] == "short"
    assert rows[0]["meta"]["funding_8h"] == -0.5


def test_book_position_cap(monkeypatch):
    _setup(monkeypatch, funding=-0.50)
    reg = ro.get_claims_registry()
    for c in ("AAA", "BBB", "CCC"):
        reg.claim(c, nf._BOOK_NAME)
    held = [{"position": {"coin": c, "szi": "-1.0"}} for c in ("AAA", "BBB", "CCC")]
    calls = []
    rec = nf.maybe_run(_cfg(max_book_positions=3), _uni(), held, _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 0 and rec["skipped"].get("book_cap") == 3 and calls == []


def test_blocked_executor_releases_claim(monkeypatch):
    _setup(monkeypatch, funding=-0.50)
    rec = nf.maybe_run(_cfg(), _uni(), [], _fetch,
                       lambda a: {"executed": False, "reason": "short floor"})
    assert rec["opened"] == 0 and rec["skipped"]["blocked"] == 1
    assert ro.get_claims_registry().owner_of("ALT") is None
