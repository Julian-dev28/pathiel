"""extreme_fade_live.maybe_run — the execution path that was MISSING (the loop computed + logged
fade signals and never traded), plus the entry-timing fixes (completed-bar signal, freshness window,
per-crash-bar dedup) that align live entry with the backtest. Pure mocks — no network, no live state
(conftest redirects HERMES_STATE_DIR to a temp dir, so the dedup state file is disposable)."""
import time

from hermes_trader.agents import extreme_fade_live as efl

DAY = 86_400_000
NOW = int(time.time() * 1000)


def _mk_bars(crashed=True, fresh=True, forming=True):
    """Bars whose last COMPLETED daily bar crashed (or not), closing `fresh`=1h ago / stale=20h ago.
    Optionally append a still-forming current bar (start == the completed bar's close)."""
    close_ms = NOW - (1 * 3_600_000 if fresh else 20 * 3_600_000)
    crash_start = close_ms - DAY
    prior_start = crash_start - DAY
    prior_c = 100.0
    crash_c = prior_c * (1 + (-0.15 if crashed else 0.01))
    bars = [
        {"t": prior_start, "o": prior_c, "h": prior_c, "l": prior_c, "c": prior_c, "v": 1e7},
        {"t": crash_start, "o": crash_c, "h": crash_c, "l": crash_c, "c": crash_c, "v": 1e7},
    ]
    if forming:
        fc = crash_c * 1.05   # already bounced +5% — the forming bar must NOT drive the signal
        bars.append({"t": close_ms, "o": fc, "h": fc, "l": fc, "c": fc, "v": 1e7})
    return bars


def _universe(coins):
    return [{"coin": c, "dayNtlVlm": 1e8} for c in coins]


def _cfg(**over):
    ef = {"enabled": True, "crash_pct": -0.12}
    ef.update(over)
    return {"extreme_fade": ef}


def _fetch(fresh=frozenset(), stale=frozenset()):
    def f(coin, interval, n):
        if coin in fresh:
            return _mk_bars(crashed=True, fresh=True)
        if coin in stale:
            return _mk_bars(crashed=True, fresh=False)
        return _mk_bars(crashed=False, fresh=True)
    return f


def test_disabled_is_noop():
    calls = []
    out = efl.maybe_run({"extreme_fade": {"enabled": False}}, _universe(["D1"]), [],
                        _fetch(fresh={"D1"}), lambda a: calls.append(a))
    assert out is None and calls == []


def test_live_opens_fresh_crash_with_strategy_book_tag():
    calls = []
    efl.maybe_run(_cfg(), _universe(["FRESH", "CALM"]), [],
                  _fetch(fresh={"FRESH"}), lambda a: calls.append(a))
    assert len(calls) == 1
    a = calls[0]
    assert a["coin"] == "FRESH" and a["side"] == "long" and a["verdict"] == "LONG"
    assert a["strategy_book"] == "extreme_fade"


def test_skips_stale_crash_no_chase(caplog):
    """A crash whose bar closed 20h ago is past the entry window → must NOT open (don't chase the
    already-bounced move; this is the mid-day-restart case that surfaced the bug)."""
    calls = []
    caplog.set_level("INFO", logger="hermes_trader.agents.extreme_fade_live")
    efl.maybe_run(_cfg(), _universe(["STALE"]), [],
                  _fetch(stale={"STALE"}), lambda a: calls.append(a))
    assert calls == []
    assert "skip STALE: stale entry window" in caplog.text


def test_forming_bar_does_not_drive_signal():
    """The forming bar bounced +5%; the signal must come from the completed crash bar, so a fade
    still fires (proving we read completed bars, not today-so-far)."""
    calls = []
    efl.maybe_run(_cfg(), _universe(["FORM"]), [],
                  _fetch(fresh={"FORM"}), lambda a: calls.append(a))
    assert len(calls) == 1 and calls[0]["coin"] == "FORM"


def test_skips_held_coin_no_stacking():
    calls = []
    positions = [{"position": {"coin": "HELD", "szi": "1.0"}}]
    efl.maybe_run(_cfg(), _universe(["HELD", "OPEN"]), positions,
                  _fetch(fresh={"HELD", "OPEN"}), lambda a: calls.append(a))
    opened = {c["coin"] for c in calls}
    assert "HELD" not in opened and "OPEN" in opened


def test_dedup_same_crash_bar_opens_once():
    calls = []
    fetch = _fetch(fresh={"DEDUP"})
    efl.maybe_run(_cfg(), _universe(["DEDUP"]), [], fetch, lambda a: calls.append(a))
    efl.maybe_run(_cfg(), _universe(["DEDUP"]), [], fetch, lambda a: calls.append(a))
    assert len(calls) == 1            # second cycle deduped on the same crash bar


def test_blocked_execute_does_not_record_open_or_dedup():
    calls = []
    fetch = _fetch(fresh={"BLOCKED"})

    def blocked(a):
        calls.append(a)
        return {"executed": False, "reason": "gate"}

    first = efl.maybe_run(_cfg(), _universe(["BLOCKED"]), [], fetch, blocked)
    second = efl.maybe_run(_cfg(), _universe(["BLOCKED"]), [], fetch, blocked)

    assert first["opened"] == 0
    assert second["opened"] == 0
    assert len(calls) == 2            # not deduped because no exchange risk opened


def test_per_cycle_cap():
    calls = []
    efl.maybe_run(_cfg(max_new_per_cycle=1), _universe(["C1", "C2", "C3"]), [],
                  _fetch(fresh={"C1", "C2", "C3"}), lambda a: calls.append(a))
    assert len(calls) == 1


def test_non_crash_does_not_fire():
    calls = []
    efl.maybe_run(_cfg(), _universe(["CALM"]), [],
                  _fetch(), lambda a: calls.append(a))
    assert calls == []


# ── validated structure + W-B2 skew-arm (2026-07-09) ─────────────────────────────

def test_carries_validated_exit_structure():
    """The fade must trade the structure it was validated with (W-B2 base: 20% stop,
    1x, 3d stop-or-horizon, NO trail) — inheriting the main engine's 15x + tight ATR
    stop clipped the post-crash wobble and turned +4.5%/trade into a live bleed."""
    calls = []
    efl.maybe_run(_cfg(), _universe(["VSTRUCT"]), [],
                  _fetch(fresh={"VSTRUCT"}), lambda a: calls.append(a))
    a = calls[0]
    assert a["leverage_override"] == 1
    assert a["backup_sl_pct_override"] == 20.0
    dsl = a["dsl_exit_override"]
    assert dsl["max_loss_pct"] == 20.0 and dsl["max_loss_roe_pct"] == 20.0
    assert dsl["protect_pct"] == 9999.0            # trail never arms: stop-or-horizon
    assert dsl["hard_timeout_minutes"] == 3.0 * 1440
    assert dsl["atr_stop"] == {"enabled": False}
    assert "strategy_book_equity_frac_override" not in a   # off unless configured


def test_equity_fraction_flows_through():
    calls = []
    efl.maybe_run(_cfg(equity_fraction=0.4), _universe(["VFRAC"]), [],
                  _fetch(fresh={"VFRAC"}), lambda a: calls.append(a))
    assert calls[0]["strategy_book_equity_frac_override"] == 0.4


def _skew_cbc(daily_rets, n_coins=12):
    """cbc where EVERY coin follows the same daily return path -> the equal-weight
    market return equals that path exactly."""
    start = NOW - (len(daily_rets) + 2) * DAY
    cbc = {}
    for k in range(n_coins):
        px, bars = 100.0, []
        bars.append({"t": start, "o": px, "h": px, "l": px, "c": px, "v": 1e7})
        for i, r in enumerate(daily_rets):
            px *= (1 + r)
            t = start + (i + 1) * DAY
            bars.append({"t": t, "o": px, "h": px, "l": px, "c": px, "v": 1e7})
        cbc[f"C{k}"] = bars
    return cbc


def test_market_skew_sign():
    # many small up days + a few large crashes -> negative skew (armed regime)
    neg = _skew_cbc([0.01] * 17 + [-0.06, 0.01, -0.07])
    s = efl._market_skew(neg, window=20)
    assert s is not None and s < 0
    # many small down days + a few large rips -> positive skew (disarmed regime)
    pos = _skew_cbc([-0.01] * 17 + [0.06, -0.01, 0.07])
    s = efl._market_skew(pos, window=20)
    assert s is not None and s > 0


def test_market_skew_thin_data_is_none():
    assert efl._market_skew(_skew_cbc([0.01] * 3), window=20) is None       # too few days
    assert efl._market_skew(_skew_cbc([0.01] * 22, n_coins=3), window=20) is None  # too few coins


def test_skew_arm_shadow_tags_but_does_not_block(monkeypatch):
    """Default (enforce=false): disarmed regime still trades — the arm only TAGS,
    building the forward evidence before any enforcement flip."""
    monkeypatch.setattr(efl, "_market_skew", lambda cbc, w, min_coins=10: 0.8)
    calls = []
    out = efl.maybe_run(_cfg(), _universe(["VSHADOW"]), [],
                        _fetch(fresh={"VSHADOW"}), lambda a: calls.append(a))
    assert len(calls) == 1
    assert out["armed"] is False and out["skew"] == 0.8


def test_skew_arm_enforce_blocks_disarmed(monkeypatch):
    monkeypatch.setattr(efl, "_market_skew", lambda cbc, w, min_coins=10: 0.8)
    calls = []
    out = efl.maybe_run(_cfg(skew_arm={"enabled": True, "enforce": True}),
                        _universe(["VBLOCK"]), [],
                        _fetch(fresh={"VBLOCK"}), lambda a: calls.append(a))
    assert calls == []
    assert out["opened"] == 0 and out["armed"] is False


def test_skew_arm_enforce_allows_armed(monkeypatch):
    monkeypatch.setattr(efl, "_market_skew", lambda cbc, w, min_coins=10: -0.6)
    calls = []
    out = efl.maybe_run(_cfg(skew_arm={"enabled": True, "enforce": True}),
                        _universe(["VALLOW"]), [],
                        _fetch(fresh={"VALLOW"}), lambda a: calls.append(a))
    assert len(calls) == 1 and out["armed"] is True


def test_signals_recorded_to_ledger_with_arm_meta(monkeypatch):
    monkeypatch.setattr(efl, "_market_skew", lambda cbc, w, min_coins=10: -0.42)
    captured = []
    monkeypatch.setattr(efl.shadow_ledger, "record_many",
                        lambda book, rows: captured.append((book, list(rows))) or len(rows))
    efl.maybe_run(_cfg(), _universe(["VLEDGER"]), [],
                  _fetch(fresh={"VLEDGER"}), lambda a: {"executed": True})
    book, rows = captured[0]
    assert book == "extreme_fade" and len(rows) == 1
    r = rows[0]
    assert r["side"] == "long" and r["horizon_days"] == 3.0 and r["stop_pct"] == 20.0
    assert r["entry_ref_px"] > 0
    assert r["meta"]["armed"] is True and r["meta"]["skew"] == -0.42


def test_deep_crash_tier_sizes_up():
    """Alpha rescrape 2026-07-09: crashes <= -20% validated at higher EV on two
    independent datasets -> distinct size tier, same 20%/1x/3d structure."""
    calls = []
    # _mk_bars crashes -15% (base tier)
    efl.maybe_run(_cfg(equity_fraction=0.4,
                       deep_tier={"crash_pct": -0.20, "equity_fraction": 0.6}),
                  _universe(["VBASE"]), [], _fetch(fresh={"VBASE"}),
                  lambda a: calls.append(a))
    assert calls[0]["strategy_book_equity_frac_override"] == 0.4
    assert "[deep-tier]" not in calls[0]["reasoning"]


def test_deep_crash_tier_triggers_below_threshold(monkeypatch):
    from hermes_trader.agents.extreme_fade import FadeSignal
    a = efl._fade_analysis(FadeSignal(coin="X", side="long", prior_daily_ret=-0.25, threshold_pct=12.0),
                           {"equity_fraction": 0.4,
                            "deep_tier": {"crash_pct": -0.20, "equity_fraction": 0.6}})
    assert a["strategy_book_equity_frac_override"] == 0.6
    assert "[deep-tier]" in a["reasoning"]
