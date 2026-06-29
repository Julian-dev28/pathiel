"""The wide-stop runner sandbox reuses vol_breakout_long's volume-influx ENTRY but applies a
WIDE 30% stop + arm-late (+10%) trail, at $3/1x. Tests the exit overrides + book plumbing."""
import os

from hermes_trader.agents import vol_breakout_wide_live as vw
from hermes_trader.agents import rebalancer_owned as ro

BAR = 300_000
NOW_MS = 70_000 * BAR + 4_242
CONFIRM_T = NOW_MS - 360_000


def _bars(seq, forming=True):
    start = CONFIRM_T - (len(seq) - 1) * BAR
    bars = [{"t": start + i * BAR, "o": o, "h": h, "l": l, "c": c, "v": v}
            for i, (o, h, l, c, v) in enumerate(seq)]
    if forming:
        last = bars[-1]
        bars.append({"t": last["t"] + BAR, "o": last["c"], "h": last["c"] * 1.01,
                     "l": last["c"] * 0.99, "c": last["c"], "v": 100})
    return bars


def _influx_seq():
    # 6 flat trailing bars, green influx (vol 1.6x), green confirm (vol 1.2x)
    seq = [(100, 101, 99, 100, 100)] * 6
    seq += [(100, 104, 99.5, 103, 160)]
    seq += [(103, 105, 102, 104, 120)]
    return _bars(seq)


def _uni(coin="ALT", prev=100.0, mid=110.0, dvol=30_000_000):
    return [{"coin": coin, "type": "perp", "prevDayPx": prev, "midPx": mid, "dayNtlVlm": dvol}]


def _cfg(**ov):
    cfg = {"enabled": True, "shadow_only": False, "scan_interval_minutes": 0,
           "entry_window_minutes": 7, "vol_window": 6, "breakout_vol_x": 1.5, "confirm_vol_x": 1.0,
           "confirm_require_green": True, "require_new_high": False, "min_mover_pct": 0.0,
           "min_volume_usd": 5_000_000, "max_scan_coins": 40, "history_bars": 25, "hold_hours": 8.0,
           "protect_pct": 10.0, "retrace_threshold": 0.35, "stop_pct": 30.0, "notional_usd": 3.0,
           "leverage": 1, "max_new_per_cycle": 1, "max_book_positions": 3}
    cfg.update(ov)
    return {"vol_breakout_wide": cfg}


def _setup(monkeypatch):
    ro._claims_registry = None
    monkeypatch.setattr(vw.shadow_ledger, "record_many", lambda book, rows: len(rows))
    for path in (vw._SEEN_FILE, vw._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    monkeypatch.setattr(vw, "log_event", lambda e: None)
    monkeypatch.setattr(vw, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(vw, "_save_ts", lambda t: None)
    monkeypatch.setattr(vw.time, "time", lambda: NOW_MS / 1000.0)
    # _candidate_signals (imported from the long book) calls active_position_coins via _held_coins,
    # but here we patch vw._held_coins directly for the live branch.
    monkeypatch.setattr(vw, "_held_coins", lambda positions: {
        p.get("position", p).get("coin") for p in (positions or [])
        if float((p.get("position", p)).get("szi", 0) or 0) != 0})


def _fetch(coin, interval, n):
    return _influx_seq()


def test_live_opens_long_with_wide_exit(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = vw.maybe_run(_cfg(), _uni(), [], _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 1
    a = calls[0]
    assert a["side"] == "long" and a["strategy_book"] == "vol_breakout_wide"
    assert a["strategy_book_notional"] == 3.0
    assert a["backup_sl_pct_override"] == 30.0                      # WIDE stop
    assert a["dsl_exit_override"]["protect_pct"] == 10.0            # arm LATE
    assert a["dsl_exit_override"]["retrace_threshold"] == 0.35      # loose give-back
    assert a["dsl_exit_override"]["phase2_tiers"][0]["pct_above_entry"] == 30.0


def test_book_position_cap(monkeypatch):
    _setup(monkeypatch)
    reg = ro.get_claims_registry()
    for c in ("AAA", "BBB", "CCC"):
        reg.claim(c, vw._BOOK_NAME)
    held = [{"position": {"coin": c, "szi": "1.0"}} for c in ("AAA", "BBB", "CCC")]
    calls = []
    rec = vw.maybe_run(_cfg(max_book_positions=3), _uni(), held, _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 0 and rec["skipped"].get("book_cap") == 3 and calls == []


def test_shadow_zero_capital(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = vw.maybe_run(_cfg(shadow_only=True), _uni(), [], _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is True and rec["signals"] == 1 and calls == []
