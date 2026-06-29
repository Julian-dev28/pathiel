import os

from hermes_trader.agents import vol_breakout_long_live as vb
from hermes_trader.agents import rebalancer_owned as ro


BAR = 300_000  # 5m
NOW_MS = 50_000 * BAR + 123_456
# Place the last COMPLETED (confirm) bar so it just closed: opened 6m ago, closed 1m ago
# (completed: now-t=360s>=300; fresh: now-close=60s<=entry_window).
CONFIRM_T = NOW_MS - 360_000


def _bars(seq, forming=True):
    """seq = list of (o,h,l,c,v). Last real bar lands at CONFIRM_T; a forming bar is
    appended after it (inside the drop window) so _completed_bars strips it."""
    start = CONFIRM_T - (len(seq) - 1) * BAR
    bars = [{"t": start + i * BAR, "o": o, "h": h, "l": l, "c": c, "v": v}
            for i, (o, h, l, c, v) in enumerate(seq)]
    if forming:
        last = bars[-1]
        bars.append({"t": last["t"] + BAR, "o": last["c"], "h": last["c"] * 1.01,
                     "l": last["c"] * 0.99, "c": last["c"], "v": 100})
    return bars


def _confirmed_seq(confirm_vol=160, breakout_vol=400, breakout_close=105):
    """48 flat low-vol bars, then a breakout (new high, green, high vol), then a
    confirm bar (follow-through volume). 50 completed bars total."""
    seq = [(100, 101, 99, 100, 100)] * 48
    seq += [(100, 106, 99.5, breakout_close, breakout_vol)]   # breakout
    seq += [(breakout_close, 107, 104, 106, confirm_vol)]     # confirm / follow-through
    return _bars(seq)


def _uni(coin="ALT", prev=100.0, mid=110.0, dvol=30_000_000):
    return [{"coin": coin, "type": "perp", "prevDayPx": prev, "midPx": mid, "dayNtlVlm": dvol}]


def _cfg(**ov):
    cfg = {
        "enabled": True, "shadow_only": False, "scan_interval_minutes": 0,
        "entry_window_minutes": 7, "vol_window": 48, "breakout_vol_x": 3.0,
        "confirm_vol_x": 1.5, "min_mover_pct": 8.0, "min_volume_usd": 5_000_000,
        "max_scan_coins": 25, "history_bars": 70, "hold_hours": 4.0,
        "retrace_threshold": 0.10, "protect_pct": 1.0, "stop_pct": 20.0,
        "notional_usd": 8.0, "leverage": 1, "max_new_per_cycle": 1,
    }
    cfg.update(ov)
    return {"vol_breakout_long": cfg}


def _setup(monkeypatch):
    ro._claims_registry = None
    captured: list = []
    monkeypatch.setattr(vb.shadow_ledger, "record_many",
                        lambda book, rows: captured.append((book, list(rows))) or len(rows))
    for path in (vb._SEEN_FILE, vb._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    monkeypatch.setattr(vb, "log_event", lambda e: None)
    monkeypatch.setattr(vb, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(vb, "_save_ts", lambda t: None)
    monkeypatch.setattr(vb.time, "time", lambda: NOW_MS / 1000.0)
    monkeypatch.setattr(vb, "active_position_coins", lambda: {})
    return captured


def _fetch(coin, interval, n):
    assert interval == "5m"
    return _confirmed_seq()


def test_detects_confirmed_breakout():
    cb = vb._completed_bars(_confirmed_seq(), NOW_MS)
    sig = vb._is_confirmed_breakout(cb, 48, 3.0, 1.5)
    assert sig is not None
    assert sig["breakout_vol_x"] == 4.0 and sig["confirm_vol_x"] == 1.6


def test_pure_volume_influx_variant_no_new_high():
    """Operator forward-test variant: require_new_high=false fires on a green vol-influx
    candle that does NOT make a new high, as long as confirm holds green volume."""
    # entry candle green + 1.5x vol but BELOW the prior high (no breakout)
    seq = [(100, 108, 99, 100, 100)] * 6                      # prior high 108
    seq += [(101, 104, 100.5, 103, 160)]                      # green, vol 1.6x, close 103 < 108 (no new high)
    seq += [(103, 105, 102, 104, 120)]                        # confirm: green, vol 1.2x
    cb = vb._completed_bars(_bars(seq), NOW_MS)
    # with the new-high requirement it must NOT fire (close 103 < high 108)
    assert vb._is_confirmed_breakout(cb, 6, 1.5, 1.0, require_new_high=True, confirm_require_green=True) is None
    # the pure-influx variant fires
    sig = vb._is_confirmed_breakout(cb, 6, 1.5, 1.0, require_new_high=False, confirm_require_green=True)
    assert sig is not None and sig["breakout_vol_x"] == 1.6


def test_green_confirm_required_rejects_red_followthrough():
    seq = [(100, 101, 99, 100, 100)] * 6
    seq += [(100, 106, 99.5, 105, 160)]                      # green influx
    seq += [(105, 105.5, 101, 102, 130)]                     # RED confirm (close < open), vol ok
    cb = vb._completed_bars(_bars(seq), NOW_MS)
    # green-confirm required -> reject the red follow-through
    assert vb._is_confirmed_breakout(cb, 6, 1.5, 1.0, require_new_high=False, confirm_require_green=True) is None
    # without the green requirement it fires (volume alone)
    assert vb._is_confirmed_breakout(cb, 6, 1.5, 1.0, require_new_high=False, confirm_require_green=False) is not None


def test_live_opens_long_with_overrides(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = vb.maybe_run(_cfg(), _uni(), [], _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is False and rec["opened"] == 1
    a = calls[0]
    assert a["side"] == "long" and a["strategy_book"] == "vol_breakout_long"
    assert a["strategy_book_notional"] == 8.0
    assert a["leverage_override"] == 1
    assert a["backup_sl_pct_override"] == 20.0
    assert a["dsl_exit_override"]["retrace_threshold"] == 0.10
    assert a["dsl_exit_override"]["hard_timeout_minutes"] == 4.0 * 60
    assert "phase2_tiers" not in a["dsl_exit_override"]  # tight floor, no loosening


def test_unconfirmed_breakout_no_signal(monkeypatch):
    """Volume spike that DIES next candle (the pump-and-dump) must not fire."""
    _setup(monkeypatch)
    calls = []
    monkeypatch.setattr(
        vb, "_candidate_signals", vb._candidate_signals)  # use real
    rec = vb.maybe_run(_cfg(), _uni(),
                       [], lambda c, i, n: _confirmed_seq(confirm_vol=120),  # < 1.5x*100
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["signals"] == 0 and calls == []


def test_no_breakout_no_signal(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = vb.maybe_run(_cfg(), _uni(),
                       [], lambda c, i, n: _confirmed_seq(breakout_vol=200),  # < 3x*100
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["signals"] == 0 and calls == []


def test_mover_prefilter_excludes_quiet(monkeypatch):
    """A coin that isn't moving (small 24h %) is never 5m-fetched."""
    _setup(monkeypatch)
    fetched = []

    def _spy_fetch(coin, interval, n):
        fetched.append(coin)
        return _confirmed_seq()

    rec = vb.maybe_run(_cfg(), _uni(prev=100.0, mid=103.0),  # 3% move < 8%
                       [], _spy_fetch, lambda a: {"executed": True})
    assert rec["signals"] == 0 and fetched == []


def test_thin_volume_excluded(monkeypatch):
    _setup(monkeypatch)
    fetched = []
    rec = vb.maybe_run(_cfg(), _uni(dvol=1_000_000),  # < 5M floor
                       [], lambda c, i, n: fetched.append(c) or _confirmed_seq(),
                       lambda a: {"executed": True})
    assert rec["signals"] == 0 and fetched == []


def test_stale_signal_skipped(monkeypatch):
    """A confirm bar that closed long ago (slow cycle) is dropped — don't chase stale."""
    _setup(monkeypatch)
    calls = []

    def _old_fetch(coin, interval, n):
        # shift every bar back 30 minutes so the confirm closed well outside the window
        bars = _confirmed_seq(forming=False)
        for b in bars:
            b["t"] -= 1_800_000
        return bars

    rec = vb.maybe_run(_cfg(), _uni(), [], _old_fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["signals"] == 0 and calls == []


def test_shadow_records_zero_capital(monkeypatch):
    captured = _setup(monkeypatch)
    calls = []
    rec = vb.maybe_run(_cfg(shadow_only=True), _uni(), [], _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is True and rec["signals"] == 1 and calls == []
    book, rows = captured[0]
    assert book == "vol_breakout_long" and rows[0]["side"] == "long"
    assert rows[0]["entry_ref_px"] == 106.0


def test_skips_held_coin(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = vb.maybe_run(_cfg(), _uni(), [{"position": {"coin": "ALT", "szi": "1.0"}}],
                       _fetch, lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 0 and rec["skipped"]["held"] == 1 and calls == []


def test_blocked_executor_releases_claim(monkeypatch):
    _setup(monkeypatch)
    rec = vb.maybe_run(_cfg(), _uni(), [], _fetch,
                       lambda a: {"executed": False, "reason": "blocked"})
    assert rec["opened"] == 0 and rec["skipped"]["blocked"] == 1
    assert ro.get_claims_registry().owner_of("ALT") is None


def test_book_position_cap_leaves_slots_for_main_engine(monkeypatch):
    """At max_book_positions the book opens nothing — no slot collision with the main engine."""
    _setup(monkeypatch)
    reg = ro.get_claims_registry()
    reg.claim("AAA", vb._BOOK_NAME)
    reg.claim("BBB", vb._BOOK_NAME)
    reg.claim("CCC", vb._BOOK_NAME)  # book already holds 3
    calls = []
    # held must include the 3 claimed coins so prune_to keeps them
    held = [{"position": {"coin": c, "szi": "1.0"}} for c in ("AAA", "BBB", "CCC")]
    rec = vb.maybe_run(_cfg(max_book_positions=3), _uni(), held, _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 0 and rec["skipped"].get("book_cap") == 3 and calls == []


def test_book_cap_allows_up_to_room(monkeypatch):
    _setup(monkeypatch)
    reg = ro.get_claims_registry()
    reg.claim("AAA", vb._BOOK_NAME)  # 1 held, cap 3 -> room for 2 more (max_new caps to 1)
    calls = []
    held = [{"position": {"coin": "AAA", "szi": "1.0"}}]
    rec = vb.maybe_run(_cfg(max_book_positions=3, max_new_per_cycle=1), _uni(), held, _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 1
