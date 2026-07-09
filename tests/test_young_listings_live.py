"""young_listings lane: trades ONLY the under-the-floor xyz population, bounded
hard, with actions config-off until W-Y1 rules on continuation vs fade."""
import os

from hermes_trader.agents import rebalancer_owned as ro
from hermes_trader.agents import young_listings_live as yl

DAY = 86_400_000
NOW_MS = 1_760_000_000_000
NOW_S = NOW_MS / 1000.0
LAST_T = NOW_MS - (NOW_MS % DAY) - DAY   # last completed daily bar (closed 5.9h ago at NOW_MS%DAY≈...)


def _bars(n_days, last_move_pct=12.0, forming=True):
    """n_days completed bars ending at LAST_T; the last bar moves last_move_pct."""
    start = LAST_T - (n_days - 1) * DAY
    px, bars = 100.0, []
    for i in range(n_days - 1):
        bars.append({"t": start + i * DAY, "o": px, "h": px + 1, "l": px - 1, "c": px, "v": 1e6})
    last_c = px * (1 + last_move_pct / 100.0)
    bars.append({"t": LAST_T, "o": px, "h": max(px, last_c) + 1, "l": min(px, last_c) - 1,
                 "c": last_c, "v": 1e6})
    if forming:
        bars.append({"t": LAST_T + DAY, "o": last_c, "h": last_c, "l": last_c, "c": last_c, "v": 1e5})
    return bars


def _uni(coin="xyz:NEWCO", dvol=5_000_000):
    return [{"coin": coin, "dayNtlVlm": dvol, "midPx": 100.0}]


def _cfg(**ov):
    cfg = {"enabled": True, "shadow_only": False, "scan_interval_minutes": 0,
           "dex_allowlist": ["xyz"], "min_age_bars": 2, "max_age_bars": 60,
           "trigger_pct": 8.0, "min_volume_usd": 3_000_000,
           "executor_short_volume_floor_usd": 3_000_000,
           "entry_window_hours": 30.0, "hold_days": 2.0, "stop_pct": 15.0,
           "notional_usd": 15.0, "leverage": 1,
           "up_action": "long", "down_action": "short",
           "max_new_per_cycle": 1, "max_book_positions": 1}
    cfg.update(ov)
    return {"young_listings": cfg}


def _setup(monkeypatch):
    ro._claims_registry = None
    captured, events = [], []
    monkeypatch.setattr(yl.shadow_ledger, "record_many",
                        lambda book, rows: captured.append((book, list(rows))) or len(rows))
    for path in (yl._SEEN_FILE, yl._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    monkeypatch.setattr(yl, "log_event", lambda e: events.append(e))
    monkeypatch.setattr(yl, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(yl, "_save_ts", lambda t: None)
    monkeypatch.setattr(yl.time, "time", lambda: NOW_S)
    monkeypatch.setattr(yl, "active_position_coins", lambda: {})
    return captured, events


def _fetch(n_days=18, move=12.0):
    def f(coin, interval, n):
        assert interval == "1d"
        return _bars(n_days, move)
    return f


# ---------------------------------------------------------------- population bounds
def test_only_young_window_signals():
    cfg = _cfg()["young_listings"]
    assert yl._mover_signal(yl._completed_bars(_bars(18), NOW_MS), cfg) is not None
    # too old: at/over the floor belongs to the main engine
    assert yl._mover_signal(yl._completed_bars(_bars(60), NOW_MS), cfg) is None
    assert yl._mover_signal(yl._completed_bars(_bars(75), NOW_MS), cfg) is None
    # too young: day-1 chaos excluded
    assert yl._mover_signal(yl._completed_bars(_bars(1), NOW_MS), cfg) is None


def test_trigger_threshold_and_direction():
    cfg = _cfg()["young_listings"]
    small = yl._mover_signal(yl._completed_bars(_bars(18, 4.0), NOW_MS), cfg)
    assert small is None
    down = yl._mover_signal(yl._completed_bars(_bars(18, -11.0), NOW_MS), cfg)
    assert down is not None and down["direction"] == "down" and down["move_pct"] < -8


def test_non_xyz_and_thin_coins_excluded(monkeypatch):
    _setup(monkeypatch)
    rec = yl.maybe_run(_cfg(), [{"coin": "VINE", "dayNtlVlm": 9e9, "midPx": 1}],
                       [], _fetch(), lambda a: {"executed": True})
    assert rec["signals"] == 0                       # crypto mover is NOT this lane's job
    rec = yl.maybe_run(_cfg(), _uni(dvol=1_000_000), [], _fetch(),
                       lambda a: {"executed": True})
    assert rec["signals"] == 0                       # thin young listing excluded


# ---------------------------------------------------------------- actions + recording
def test_shadow_records_continuation_frame(monkeypatch):
    captured, _ = _setup(monkeypatch)
    calls = []
    rec = yl.maybe_run(_cfg(shadow_only=True), _uni(), [], _fetch(move=-11.0),
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is True and rec["signals"] == 1 and calls == []
    book, rows = captured[0]
    assert book == "young_listings"
    r = rows[0]
    assert r["side"] == "short" and r["meta"]["direction"] == "down"
    assert r["meta"]["age_bars"] == 18 and r["stop_pct"] == 15.0


def test_actions_off_records_but_never_trades(monkeypatch):
    captured, _ = _setup(monkeypatch)
    calls = []
    rec = yl.maybe_run(_cfg(up_action="off", down_action="off"), _uni(), [],
                       _fetch(), lambda a: calls.append(a) or {"executed": True})
    assert calls == [] and rec["opened"] == 0
    assert rec["skipped"]["action_off"] == 1
    assert captured and captured[0][1]              # trigger still recorded


def test_live_long_carries_bounded_structure(monkeypatch):
    _setup(monkeypatch)
    calls = []
    rec = yl.maybe_run(_cfg(), _uni(), [], _fetch(move=12.0),
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 1
    a = calls[0]
    assert a["side"] == "long" and a["strategy_book"] == "young_listings"
    assert a["strategy_book_notional"] == 15.0 and a["leverage_override"] == 1
    assert a["dsl_exit_override"]["hard_timeout_minutes"] == 2.0 * 1440
    assert a["dsl_exit_override"]["protect_pct"] == 9999.0


def test_down_action_short_carries_volume_override(monkeypatch):
    _setup(monkeypatch)
    calls = []
    yl.maybe_run(_cfg(), _uni(), [], _fetch(move=-11.0),
                 lambda a: calls.append(a) or {"executed": True})
    assert calls[0]["side"] == "short"
    assert calls[0]["min_short_volume_usd_override"] == 3_000_000.0


def test_book_cap_one_position(monkeypatch):
    _setup(monkeypatch)
    claims = ro.get_claims_registry()
    claims.claim("xyz:OTHER", "young_listings")
    claims.save()
    monkeypatch.setattr(yl, "_held_coins", lambda p: {"xyz:OTHER"})
    calls = []
    rec = yl.maybe_run(_cfg(), _uni(), [], _fetch(), lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 0 and calls == []


def test_disabled_is_noop():
    assert yl.maybe_run(_cfg(enabled=False), _uni(), [], _fetch(), lambda a: None) is None
