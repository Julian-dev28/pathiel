"""Majors-swing book (trend + pullback-resume longs on the fixed majors allowlist).

The geometry under test: entries only when trend + pullback-band + resume ALL
hold on completed daily bars; sizing rides the executor's per-analysis
equity-fraction override; ledger records in BOTH modes; exact book_open
footprint on every confirmed open.
"""
import os

from hermes_trader.agents import majors_swing_live as ms
from hermes_trader.agents import rebalancer_owned as ro

DAY = 86_400_000
NOW_MS = 500 * DAY + 7_200_000          # 2h into the forming day
LAST_T = NOW_MS - (NOW_MS % DAY) - DAY  # last COMPLETED daily bar open


def _bars(seq, forming=True):
    """seq = list of (o,h,l,c). Last real bar lands at LAST_T."""
    start = LAST_T - (len(seq) - 1) * DAY
    bars = [{"t": start + i * DAY, "o": o, "h": h, "l": l, "c": c, "v": 1_000}
            for i, (o, h, l, c) in enumerate(seq)]
    if forming:
        last = bars[-1]
        bars.append({"t": last["t"] + DAY, "o": last["c"], "h": last["c"] * 1.01,
                     "l": last["c"] * 0.99, "c": last["c"], "v": 1_000})
    return bars


def _swing_seq(pullback_low=95.0, resume_close=100.5, base=100.0, n_base=70):
    """Uptrend above MA (flat base then grind up), 20d high 102, pullback to
    `pullback_low`, resume bar closing above the prior day's high. The prior
    day derives from pullback_low so its low can't leak a deeper pullback into
    the window than the test intends."""
    prior_hi = pullback_low + 2.5
    seq = [(base * 0.9, base * 0.9 + 1, base * 0.9 - 1, base * 0.9)] * n_base
    seq += [(base, base + 1, base - 0.2, base + 0.5)] * 10           # holds above the MA
    seq += [(100, 102, 99.8, 101)]                                   # 20d high = 102
    seq += [(101, 101.5, pullback_low, pullback_low + 1)]            # pullback low
    seq += [(pullback_low + 1, prior_hi, pullback_low + 0.5,
             pullback_low + 2)]                                      # prior day
    seq += [(pullback_low + 2, resume_close + 0.5,
             pullback_low + 1.5, resume_close)]                      # resume bar
    return _bars(seq)


def _cfg(**ov):
    cfg = {
        "enabled": True, "shadow_only": False, "scan_interval_minutes": 0,
        "coins": ["BTC"], "trend_ma_period": 200, "min_trend_bars": 60,
        "high_lookback": 20, "pullback_lookback": 5,
        "pullback_min_pct": 3.0, "pullback_max_pct": 8.0,
        "entry_window_hours": 8.0, "hold_days": 7.0, "stop_pct": 2.2,
        "equity_fraction": 0.25, "leverage": 25, "protect_pct": 6.0,
        "retrace_threshold": 0.35, "max_new_per_cycle": 1, "max_book_positions": 2,
        "history_bars": 230,
    }
    cfg.update(ov)
    return {"majors_swing": cfg}


def _setup(monkeypatch):
    ro._claims_registry = None
    captured: list = []
    monkeypatch.setattr(ms.shadow_ledger, "record_many",
                        lambda book, rows: captured.append((book, list(rows))) or len(rows))
    for path in (ms._SEEN_FILE, ms._TS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    events: list = []
    monkeypatch.setattr(ms, "log_event", lambda e: events.append(e))
    monkeypatch.setattr(ms, "_last_ts", lambda: 0.0)
    monkeypatch.setattr(ms, "_save_ts", lambda t: None)
    monkeypatch.setattr(ms.time, "time", lambda: NOW_MS / 1000.0)
    monkeypatch.setattr(ms, "active_position_coins", lambda: {})
    return captured, events


def _fetch(coin, interval, n):
    assert interval == "1d"
    return _swing_seq()


# ------------------------------------------------------------------ signal shape
def test_detects_trend_pullback_resume():
    cb = ms._completed_bars(_swing_seq(), NOW_MS)
    sig = ms._pullback_resume_signal(cb, _cfg()["majors_swing"])
    assert sig is not None
    assert 3.0 <= sig["pullback_pct"] <= 8.0
    assert sig["signal_bar_t"] == LAST_T


def test_no_resume_no_signal():
    # close does NOT reclaim the prior day's high (97.5)
    cb = ms._completed_bars(_swing_seq(resume_close=97.0), NOW_MS)
    assert ms._pullback_resume_signal(cb, _cfg()["majors_swing"]) is None


def test_pullback_too_deep_no_signal():
    # 102 -> 90 is ~11.8%, outside the 3-8% band (that's a crash, not a pullback)
    cb = ms._completed_bars(_swing_seq(pullback_low=90.0, resume_close=100.5), NOW_MS)
    assert ms._pullback_resume_signal(cb, _cfg()["majors_swing"]) is None


def test_pullback_too_shallow_no_signal():
    # 102 -> 101 is ~1%, below the 3% floor (no pullback happened);
    # resume close must clear the prior-day high (103.5) so ONLY the band gates.
    cb = ms._completed_bars(_swing_seq(pullback_low=101.0, resume_close=104.0), NOW_MS)
    assert ms._pullback_resume_signal(cb, _cfg()["majors_swing"]) is None


def test_below_trend_ma_no_signal():
    # same pullback/resume shape but price sits far below a long-run higher base
    seq_cfg = _cfg(trend_ma_period=80)["majors_swing"]
    seq = [(200, 201, 199, 200)] * 76           # MA anchored way above
    seq += [(100, 102, 99.5, 101)]
    seq += [(101, 101.5, 95.0, 96.0)]
    seq += [(96, 97.5, 95.5, 97)]
    seq += [(97, 101, 96.5, 100.5)]
    cb = ms._completed_bars(_bars(seq), NOW_MS)
    assert ms._pullback_resume_signal(cb, seq_cfg) is None


def test_insufficient_history_skipped():
    def short_fetch(coin, interval, n):
        return _bars([(100, 101, 99, 100)] * 30)   # 30 bars < min_trend_bars 60
    sigs = ms._candidate_signals(_cfg()["majors_swing"], short_fetch, NOW_MS)
    assert sigs == []


def test_allowlist_is_the_universe():
    seen_coins = []

    def fetch(coin, interval, n):
        seen_coins.append(coin)
        return _swing_seq()

    cfg = _cfg(coins=["BTC", "xyz:SP500"])["majors_swing"]
    sigs = ms._candidate_signals(cfg, fetch, NOW_MS)
    assert seen_coins == ["BTC", "xyz:SP500"]      # scans ONLY the allowlist
    assert {s["coin"] for s in sigs} == {"BTC", "xyz:SP500"}


def test_stale_signal_outside_entry_window_skipped():
    cfg = _cfg(entry_window_hours=1.0)["majors_swing"]   # signal closed 2h ago
    assert ms._candidate_signals(cfg, _fetch, NOW_MS) == []


# ------------------------------------------------------------------ book plumbing
def test_shadow_records_zero_capital(monkeypatch):
    captured, events = _setup(monkeypatch)
    calls = []
    rec = ms.maybe_run(_cfg(shadow_only=True), [], [], _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is True and rec["signals"] == 1 and calls == []
    book, rows = captured[0]
    assert book == "majors_swing"
    assert rows[0]["side"] == "long" and rows[0]["meta"]["shadow"] is True
    assert rows[0]["horizon_days"] == 7.0 and rows[0]["stop_pct"] == 2.2


def test_live_opens_long_with_swing_overrides(monkeypatch):
    captured, events = _setup(monkeypatch)
    calls = []
    rec = ms.maybe_run(_cfg(), [], [], _fetch,
                       lambda a: calls.append(a) or {"executed": True})
    assert rec["shadow"] is False and rec["opened"] == 1
    a = calls[0]
    assert a["side"] == "long" and a["strategy_book"] == "majors_swing"
    assert a["strategy_book_equity_frac_override"] == 0.25
    assert "strategy_book_notional" not in a          # dynamic sizing, no fixed cap
    assert a["leverage_override"] == 25
    assert a["backup_sl_pct_override"] == 2.2
    import pytest
    dsl = a["dsl_exit_override"]
    assert dsl["max_loss_pct"] == 2.2 and dsl["max_loss_roe_pct"] == pytest.approx(55.0)
    assert dsl["protect_pct"] == 6.0                  # arm late: let it swing
    assert dsl["hard_timeout_minutes"] == 7.0 * 1440
    assert dsl["phase2_tiers"][0]["pct_above_entry"] == 8.0
    # ledger recorded in LIVE mode too + exact attribution footprint
    assert captured and captured[0][1][0]["meta"]["shadow"] is False
    opens = [e for e in events if e.get("event") == "book_open"]
    assert opens == [{"event": "book_open", "book": "majors_swing",
                      "coin": "BTC", "side": "long", "sig_t": LAST_T}]


def test_dedup_same_signal_bar(monkeypatch):
    _setup(monkeypatch)
    seen = {}
    monkeypatch.setattr(ms, "_load_seen", lambda: seen)
    monkeypatch.setattr(ms, "_save_seen", lambda s: seen.update(s))
    opened = []
    ms.maybe_run(_cfg(), [], [], _fetch, lambda a: opened.append(a) or {"executed": True})
    ms.maybe_run(_cfg(), [], [], _fetch, lambda a: opened.append(a) or {"executed": True})
    assert len(opened) == 1                            # second cycle deduped


def test_book_cap_blocks_new_opens(monkeypatch):
    _setup(monkeypatch)
    claims = ro.get_claims_registry()
    claims.claim("ETH", "majors_swing")
    claims.claim("SOL", "majors_swing")
    claims.save()
    monkeypatch.setattr(ms, "_held_coins", lambda p: {"ETH", "SOL"})
    calls = []
    rec = ms.maybe_run(_cfg(), [], [], _fetch, lambda a: calls.append(a) or {"executed": True})
    assert rec["opened"] == 0 and calls == []
    assert rec["skipped"].get("book_cap") == 2


def test_blocked_executor_releases_claim(monkeypatch):
    _setup(monkeypatch)
    rec = ms.maybe_run(_cfg(), [], [], _fetch,
                       lambda a: {"executed": False, "reason": "blocked"})
    assert rec["opened"] == 0 and rec["skipped"]["blocked"] == 1
    assert ro.get_claims_registry().owner_of("BTC") is None


def test_disabled_is_noop():
    assert ms.maybe_run(_cfg(enabled=False), [], [], _fetch, lambda a: None) is None
# (executor sizing seam is covered behaviorally in
#  tests/test_claims_registry_and_sizing.py::test_per_analysis_frac_override_beats_global)
