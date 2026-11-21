"""scripts/trigger_watch.py: wakes the desk tick the minute its entry rule
passes (operator, 2026-10-31, weekend 4-hour loops). No LLM calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("trigger_watch", ROOT / "scripts" / "trigger_watch.py")
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)

BRENT_LV = {"range_24h": {"low": 112.00, "high": 114.23}, "atr14_1h": 0.17}


def test_evaluate_matches_the_tick_rule():
    assert W.evaluate("long", 112.20, 112.165, BRENT_LV, "xyz:BRENTOIL", 20, 20)["ok"] is False   # 1.6R
    hit = W.evaluate("long", 112.20, 112.04, BRENT_LV, "xyz:BRENTOIL", 20, 20)
    assert hit["ok"] is True and hit["stop_px"] == 111.25
    assert W.evaluate("long", 111.90, 112.04, BRENT_LV, "xyz:BRENTOIL", 20, 20)["ok"] is False     # never chase
    assert W.evaluate("flat", None, 112.04, BRENT_LV, "xyz:BRENTOIL", 20, 20)["ok"] is False
    assert W.evaluate("long", 112.20, None, BRENT_LV, "xyz:BRENTOIL", 20, 20)["ok"] is False


# A silver breakdown (services/strategies/tests/test_silver_breakout.py's
# fixture): 24h range 47.85-50, the last 1h bar closed 47.9 under the prior 48.0 low.
SILVER_BO = {"prior_low": 48.0, "prior_high": 50.0, "last_close": 47.9, "recent_high": 48.05,
             "recent_low": 47.85, "lookback_h": 24, "stop_h": 4}
SILVER_LV = {"range_24h": {"low": 47.85, "high": 50.0}, "atr14_1h": 0.2, "breakout_24h": SILVER_BO}


def test_evaluate_never_wakes_a_breakout_now():
    out = W.evaluate("short", 49.0, 47.9, SILVER_LV, "xyz:SILVER", 25, 20)
    assert out["ok"] is False and out.get("source") != "breakout"


def test_evaluate_wakes_the_silver_breakout_and_never_a_brent_one(monkeypatch):
    from services.strategies import next_trade as NT
    monkeypatch.setattr(NT, "BREAKOUT_COINS", frozenset({"xyz:SILVER"}))   # the switch, if it comes back
    hit = W.evaluate("short", 49.0, 47.9, SILVER_LV, "xyz:SILVER", 25, 20)
    assert hit["ok"] is True and hit["source"] == "breakout"
    same_on_brent = W.evaluate("short", 49.0, 47.9, SILVER_LV, "xyz:BRENTOIL", 20, 20)
    assert same_on_brent["ok"] is False and "breakout" not in same_on_brent
    no_break = dict(SILVER_LV, breakout_24h=dict(SILVER_BO, last_close=48.1))
    assert W.evaluate("short", 49.0, 47.9, no_break, "xyz:SILVER", 25, 20)["ok"] is False


def test_cooldown():
    assert W.cooled_down({}, "oil", 1000.0, 1800)
    assert not W.cooled_down({"oil": 900.0}, "oil", 1000.0, 1800)
    assert W.cooled_down({"oil": 900.0}, "metals", 1000.0, 1800)


# ── confirm_fire: the stale-fire fix (operator bug, 2026-11-17) ─────────────
# 2026-11-16 12:59Z the watcher fired oil off cached hour-12 levels (24h low
# 91.80) claiming 2.42R; the desk-tick's own fresh read, moments later at
# 13:00Z, saw the 12:00-13:00 1h bar had just closed, rolling the 24h low to
# 91.582, while the mid had climbed to 91.9565, past the thesis entry
# 91.91 ("never chase"): 1.49R, under the 2R gate, so the tick declined.
# These are the real numbers from /tmp/trigger-watch/watch.log and
# /tmp/oil-loop/progress.log for that incident, replayed with no network.
STALE_OIL_LV = {"range_24h": {"low": 91.8, "high": 94.108}}      # the watcher's cached hour-12 read
FRESH_OIL_LV = {"range_24h": {"low": 91.582, "high": 94.108}}    # a fresh read one minute later (13:00Z)


def test_confirm_fire_replays_the_2026_11_16_1259z_stale_fire():
    """The cached-levels pass's own evaluate() still says yes (this is the
    exact fire the watcher printed: stop 91.05, 2.42R, "buys now"); a fresh
    read a moment later is the exact decline the tick logged ("pays 1.49R at
    91.9565, needs 2R; buy at 91.77 or lower"). confirm_fire asks the SAME
    evaluate() off that fresh read and must agree with the tick, not the
    cache."""
    cached = W.evaluate("long", 91.91, 91.8055, STALE_OIL_LV, "xyz:BRENTOIL", 20.0, 20.0)
    assert cached == {**cached, "ok": True, "stop_px": 91.05, "tp_px": 93.63746, "r_now": 2.42,
                      "verdict": "buys now"}
    confirm = W.confirm_fire("long", 91.91, "xyz:BRENTOIL", 20.0, 20.0, 1_763_297_940_000,
                             fetch_levels=lambda coin, now_ms: dict(FRESH_OIL_LV, mid=91.9565))
    assert confirm["ok"] is False and confirm["r_now"] == 1.49 and confirm["stop_px"] == 90.832
    assert confirm["verdict"] == "waits: buy at 91.77 or lower"
    assert "mid above the thesis entry 91.91: never chase" in confirm["blockers"]


def test_confirm_fire_agrees_when_the_fresh_read_matches_the_cache():
    """The common case: nothing moved between the cached pass and the
    confirm. confirm_fire's answer is byte-identical to evaluate()'s,
    because it is the same function on the same numbers."""
    direct = W.evaluate("long", 91.91, 91.8055, STALE_OIL_LV, "xyz:BRENTOIL", 20.0, 20.0)
    confirm = W.confirm_fire("long", 91.91, "xyz:BRENTOIL", 20.0, 20.0, 1_763_297_940_000,
                             fetch_levels=lambda coin, now_ms: dict(STALE_OIL_LV, mid=91.8055))
    assert confirm == direct


def test_confirm_fire_passes_now_ms_through_to_bypass_both_caches(monkeypatch):
    """Default fetch_levels: services.strategies.levels.summarize_coin(coin,
    now_ms=now_ms). An explicit now_ms bypasses that function's own 75s
    cache (services/strategies/levels.py:122), not just lv_cache here."""
    from services.strategies import levels
    seen = {}

    def fake_summarize_coin(coin, now_ms=None):
        seen["coin"], seen["now_ms"] = coin, now_ms
        return dict(STALE_OIL_LV, mid=91.8055)
    monkeypatch.setattr(levels, "summarize_coin", fake_summarize_coin)
    W.confirm_fire("long", 91.91, "xyz:BRENTOIL", 20.0, 20.0, 1_763_297_940_000)
    assert seen == {"coin": "xyz:BRENTOIL", "now_ms": 1_763_297_940_000}


# ── the trend gate (operator bug, 2026-11-20): evaluate() must feed
# next_trade() the same closes_1h / trend_gate_span scripts/oil_levels.py's
# --next already does, or a desk with a gate (today, only HYPE) fires
# straight through it. ──────────────────────────────────────────────────────

def _hourly_candles(now_ms, n, start, drift):
    """n CLOSED 1h candles ending the hour before now_ms, oldest first,
    each closing `drift` further from `start` than the one before (real HL
    candle shape: t/T/o/h/l/c/v, with wick room so atr() sees real range)."""
    from services.strategies.levels import HOUR_MS
    rows = []
    price = start
    for i in range(n):
        t = now_ms - (n - i) * HOUR_MS
        o = price
        price += drift
        c = price
        rows.append({"t": t, "T": t + HOUR_MS - 1, "o": o, "h": max(o, c) + 0.2,
                     "l": min(o, c) - 0.2, "c": c, "v": 1})
    return rows


def _hype_lv(drift):
    """A HYPE levels payload (services.strategies.levels.summarize) off
    131 synthetic 1h closes drifting by `drift` an hour, with the mid
    placed just inside the fresh 24h low so only the trend gate -- never
    the 2R gate or the no-break guard -- can be the reason this refuses."""
    from services.strategies import levels as L
    now_ms = 200 * L.HOUR_MS
    c1h = _hourly_candles(now_ms, n=131, start=100.0, drift=drift)
    probe = L.summarize(c1h, [], 94.0, now_ms)       # a throwaway mid, just to read the fresh 24h low
    mid = probe["range_24h"]["low"] + 0.1
    return L.summarize(c1h, [], mid, now_ms)


def test_evaluate_blocks_a_hype_long_against_the_120h_downtrend_2026_11_20_bug():
    """2026-11-20 12:02Z this module fired {"fire": "hype", "mid": 63.802,
    "r_now": 5.36, "verdict": "buys now", "source": "desk_rules"} while
    HYPE's 120h return sat at about -5.8% -- the trend gate
    (next_trade.HYPE_RULE) exists to veto exactly that. evaluate() never
    passed next_trade() closes_1h / trend_gate_span, so the gate never ran.
    Replayed on synthetic 1h closes with a clean, deterministic negative
    120h return (the fix needs no live candles to prove)."""
    from services.strategies.next_trade import (leverage_fit_for, next_trade, stop_buffer_for,
                                                target_offset_frac_for)
    lv = _hype_lv(drift=-0.05)
    mid = lv["mid"]
    assert lv["n_closed_1h"] >= 121, "fixture must cover the 120h gate's own minimum"

    # Prove the bug: the main rule alone (what evaluate() called before this
    # fix -- no closes_1h, no trend_gate_span) pays well over 2R and says
    # yes, exactly the fire this replays.
    buf = stop_buffer_for("HYPE", lv["atr14_1h"], mid)
    pre_fix = next_trade("long", mid, lv["range_24h"]["low"], lv["range_24h"]["high"], thesis_entry=None,
                         leverage=10.0, coin_max_leverage=10.0, stop_buffer=buf,
                         leverage_fit=leverage_fit_for("HYPE"), offset_frac=target_offset_frac_for("HYPE"))
    assert pre_fix["ok"] is True and pre_fix["verdict"] == "buys now" and pre_fix["r_now"] > 2.0

    # The fix: evaluate() itself must refuse.
    out = W.evaluate("long", None, mid, lv, "HYPE", 10.0, 10.0)
    assert out["ok"] is False and out["verdict"] != "buys now"
    assert out["trend_gate"]["blocked"] is True and out["trend_gate"]["ret"] < 0
    assert any("120h downtrend" in b for b in out["blockers"])

    # confirm_fire reuses the same evaluate(): the stale-fire recheck must
    # also refuse, never just reproduce the unfixed answer off a fresh read.
    confirmed = W.confirm_fire("long", None, "HYPE", 10.0, 10.0, 200 * 3_600_000,
                               fetch_levels=lambda coin, now_ms: lv)
    assert confirmed["ok"] is False and confirmed["trend_gate"]["blocked"] is True


def test_evaluate_still_fires_a_hype_long_with_the_120h_trend_behind_it():
    """The gate is a veto, not a permanent block on this fixture shape: the
    same setup with an uptrend instead still says buys now, so the refusal
    above is the gate, not some other property of the synthetic bars."""
    lv = _hype_lv(drift=0.05)
    out = W.evaluate("long", None, lv["mid"], lv, "HYPE", 10.0, 10.0)
    assert out["ok"] is True and out["verdict"] == "buys now"
    assert out["trend_gate"]["blocked"] is False


def test_oil_and_sp500_watcher_verdicts_are_unchanged_by_closes_1h():
    """Desks with no trend gate (trend_gate_span_for returns None) must
    answer identically whether or not levels["closes_1h"] carries a severe
    fake downtrend -- today every coin's summarize() includes that key, so
    this is the guarantee that wiring the gate into evaluate() changed
    nothing for a desk that has none."""
    hostile = [100.0] + [50.0] * 120
    oil_bare = W.evaluate("long", 112.20, 112.04, BRENT_LV, "xyz:BRENTOIL", 20, 20)
    oil_with_hist = W.evaluate("long", 112.20, 112.04, dict(BRENT_LV, closes_1h=hostile),
                               "xyz:BRENTOIL", 20, 20)
    assert oil_bare == oil_with_hist and oil_bare["ok"] is True

    sp500_lv = {"range_24h": {"low": 7100.0, "high": 7180.0}, "atr14_1h": 12.0}
    sp_bare = W.evaluate("long", None, 7102.0, sp500_lv, "xyz:SP500", 50.0, 50.0)
    sp_with_hist = W.evaluate("long", None, 7102.0, dict(sp500_lv, closes_1h=hostile), "xyz:SP500", 50.0, 50.0)
    assert sp_bare == sp_with_hist and sp_bare["ok"] is True


def test_the_watcher_and_oil_levels_next_agree_on_the_same_synthetic_bars():
    """scripts/oil_levels.py's --next (next_order) and this watcher's
    evaluate() must read the SAME closes_1h / trend_gate_span off the SAME
    levels payload and land on the same verdict -- the entire point of the
    fix. Checked both ways: the gate blocking, and the gate standing aside."""
    import importlib.util
    cli_spec = importlib.util.spec_from_file_location("oil_levels_parity", ROOT / "scripts" / "oil_levels.py")
    cli = importlib.util.module_from_spec(cli_spec)
    cli_spec.loader.exec_module(cli)

    for drift, want_ok in ((-0.05, False), (0.05, True)):
        lv = _hype_lv(drift)
        via_cli = cli.next_order("HYPE", "long", lv, None, 10.0, 10.0)
        via_watcher = W.evaluate("long", None, lv["mid"], lv, "HYPE", 10.0, 10.0)
        common = {k: v for k, v in via_cli.items() if k not in ("coin", "place_bracket")}
        assert common == via_watcher
        assert via_watcher["ok"] is want_ok


def _stub(monkeypatch, tmp_path, mids, held="", news=None):
    import _state_env
    import pathiel.agents.config_store as cs
    import pathiel.client.exchange as ex
    from services.intel import headline_watch as HW
    from services.strategies import journal, levels
    monkeypatch.setattr(W, "STATE_DIR", str(tmp_path))
    # the headline step: its state and alert files in tmp, its fetchers stubbed (no RSS, no Polymarket)
    monkeypatch.setattr(HW, "STATE_PATH", str(tmp_path / "headline_state.json"))
    monkeypatch.setattr(HW, "ALERTS_PATH", str(tmp_path / "headline_alerts.json"))
    monkeypatch.setattr(W, "fetch_headlines", lambda group: list(news or []))
    monkeypatch.setattr(W, "fetch_odds", lambda desk: [])
    monkeypatch.setattr(W, "fetch_posts", lambda: [])          # the Trump archive and the official
    monkeypatch.setattr(W, "fetch_official", lambda: [])       # feeds: services/intel/tests/test_sources_social.py
    monkeypatch.setattr(W, "_start_finlight", lambda: None)    # the Finlight stream: services/intel/tests/test_finlight_watch.py
    monkeypatch.setattr(_state_env, "load_env_local", lambda root: None)
    monkeypatch.setattr(cs, "read_agent_config", lambda: {
        "active_desks": ["oil"], "desk_equity_share": {"oil": 1.0}, "risk_profile": {"max_leverage": 20}})
    call = SimpleNamespace(side="long", entry_px=112.20, instrument="xyz:BRENTOIL")
    monkeypatch.setattr(journal, "latest_thesis", lambda desk: SimpleNamespace(call=call))
    it = iter(mids)
    last_mid = {"v": None}

    def _fetch_mid(coin):
        last_mid["v"] = next(it)
        return last_mid["v"]
    monkeypatch.setattr(levels, "fetch_mid", _fetch_mid)
    # real summarize_coin(coin, now_ms=None) takes an optional now_ms (an
    # explicit value bypasses its own cache, services/strategies/levels.py:122);
    # confirm_fire always passes one. The stub's fresh read agrees with the
    # pass's own mid by default, so confirm_fire never flips an existing
    # test's fire; test_confirm_fire_* below overrides this directly to
    # replay a fresh read that disagrees.
    monkeypatch.setattr(levels, "summarize_coin", lambda coin, now_ms=None: dict(BRENT_LV, mid=last_mid["v"]))
    monkeypatch.setattr(ex, "get_max_leverage", lambda coin: 20)
    monkeypatch.setattr(W, "_slot", lambda mod, desk: held)
    # _append_watch_line's real implementation appends to
    # services.strategies.desk_log.log_path(desk), which defaults to the
    # REAL /tmp/oil-loop/progress.log -- the exact file the live desk-tick
    # loop and Desk page read. Every test using this stub must not write
    # into that file; the dedicated test below restores the real function
    # against a tmp_path-redirected log_path instead.
    monkeypatch.setattr(W, "_append_watch_line", lambda desk, text: None)


def test_watch_waits_then_fires_once_and_exits(monkeypatch, tmp_path, capsys):
    _stub(monkeypatch, tmp_path, [112.30, 112.20, 112.04])
    sleeps = []
    assert W.watch(once=False, cooldown_min=30, sleep=sleeps.append, clock=lambda: 1000.0) == 0
    out = json.loads(capsys.readouterr().out.strip())
    # 2 inter-pass waits (passes 1 and 2 decline) + 1 confirm_fire wait
    # (CONFIRM_DELAY_S, pass 3's candidate fire) before it actually prints.
    assert out["fire"] == "oil" and out["mid"] == 112.04 and sleeps == [60, 60, W.CONFIRM_DELAY_S]
    assert json.loads((tmp_path / "fired.json").read_text())["oil"] == 1000.0
    assert "FIRE" in (tmp_path / "watch.log").read_text()


# ── 2026-11-20: the entry fire also notes a WATCH line on the desk's own
# shared progress log, so the Desk page's Loop Log card for that desk sees
# it too, not only this module's private watch.log. _stub() above no-ops
# _append_watch_line for every other test (it would otherwise write into
# the real /tmp/oil-loop/progress.log); this test restores the real
# function against a log_path redirected into tmp_path. ─────────────────

def test_the_entry_fire_appends_a_watch_line_to_the_desks_own_progress_log(monkeypatch, tmp_path, capsys):
    real_append_watch_line = W._append_watch_line
    _stub(monkeypatch, tmp_path, [112.30, 112.20, 112.04])
    monkeypatch.setattr(W, "_append_watch_line", real_append_watch_line)
    from services.strategies import desk_log
    progress_log = tmp_path / "oil-progress.log"
    monkeypatch.setattr(desk_log, "log_path", lambda desk: str(progress_log))

    sleeps = []
    assert W.watch(once=False, cooldown_min=30, sleep=sleeps.append, clock=lambda: 1000.0) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["fire"] == "oil"

    text = progress_log.read_text().strip()
    assert text.startswith("WATCH oil ")
    assert "coin=xyz:BRENTOIL" in text and "mid=112.04" in text
    assert "action=WAKE entry rule passed" in text and "run /desk-tick oil" in text
    # desk_log.filter_desk_lines (the Desk page's own filter) must actually
    # recognize this line as oil's -- the whole point of the WATCH prefix.
    assert desk_log.filter_desk_lines([text], "oil") == [text]
    assert desk_log.filter_desk_lines([text], "sp500") == []


def test_append_watch_line_never_raises_when_the_log_directory_is_unwritable(monkeypatch, tmp_path):
    from services.strategies import desk_log
    # A path under a file (not a directory) makes os.makedirs/open fail with
    # OSError every time -- the one failure mode _append_watch_line must
    # swallow rather than crash the watcher mid-fire.
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setattr(desk_log, "log_path", lambda desk: str(blocker / "progress.log"))
    W._append_watch_line("oil", "action=WAKE entry rule passed, 2.1R, run /desk-tick oil")  # must not raise


def test_a_desk_that_holds_something_or_just_fired_is_skipped(monkeypatch, tmp_path, capsys):
    _stub(monkeypatch, tmp_path, [112.04], held="holds position on xyz:BRENTOIL")
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    assert "tick handles it" in capsys.readouterr().out
    _stub(monkeypatch, tmp_path, [112.04])
    (tmp_path / "fired.json").write_text(json.dumps({"oil": 900.0}))
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    assert "cooling down" in capsys.readouterr().out


# ── the full pipeline: the watcher must not fire when the fresh gate would
# refuse (operator bug, 2026-11-17; see confirm_fire above) ─────────────────

def test_watch_does_not_fire_when_the_fresh_gate_declines(monkeypatch, tmp_path, capsys):
    """The cached-levels pass likes the entry at mid 112.04 (2.05R, the same
    numbers test_evaluate_matches_the_tick_rule checks); a fresh read taken
    the instant before firing finds the mid already past the thesis entry
    (112.20): never chase. No FIRE line, no fired.json write, no cooldown
    spent on a trade the real gate would have refused."""
    _stub(monkeypatch, tmp_path, [112.04])
    from services.strategies import levels
    monkeypatch.setattr(levels, "summarize_coin",
                        lambda coin, now_ms=None: dict(BRENT_LV, mid=112.04 if now_ms is None else 112.25))
    W.watch(once=True, cooldown_min=30, sleep=lambda s: None, clock=lambda: 1000.0)
    out = capsys.readouterr().out
    assert '"fire"' not in out
    assert "2.05R on cached levels, but the fresh gate declined" in out
    assert "never chase" in out
    assert not (tmp_path / "fired.json").exists()


def test_the_fire_line_reports_the_fresh_confirmed_numbers_not_the_cached_ones(monkeypatch, tmp_path, capsys):
    """Both the cached pass and the fresh confirm say yes, at slightly
    different mids (the market moved between the two reads, same as the
    real incident, just not enough to flip the verdict here). The printed
    fire must carry the FRESH numbers, the ones the tick's own gate will
    also see a moment later, not the cached pass's."""
    _stub(monkeypatch, tmp_path, [112.00])
    from services.strategies import levels
    monkeypatch.setattr(levels, "summarize_coin",
                        lambda coin, now_ms=None: dict(BRENT_LV, mid=112.00 if now_ms is None else 112.04))
    assert W.watch(once=False, cooldown_min=30, sleep=lambda s: None, clock=lambda: 1000.0) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["fire"] == "oil" and line["mid"] == 112.04 and line["r_now"] == 2.05


def test_the_wait_is_load_bearing_a_zero_delay_recheck_would_have_missed_it(monkeypatch, tmp_path, capsys):
    """2026-11-16 12:59Z: an uncached read taken AT fire time agreed with the
    cached pass (the module docstring, "Since 2026-11-17") - only a read
    taken a moment LATER, after the 24h range rolled, disagreed. This test
    makes that the shape of the fixture (fresh data flips only once the
    clock has advanced CONFIRM_DELAY_S past the fire) and checks both
    halves: the real watch(), which waits before its second read, catches
    it; a watcher that didn't wait (clock frozen through the confirm) would
    not have."""
    _stub(monkeypatch, tmp_path, [112.04])
    from services.strategies import levels
    t0 = 1000.0

    def _fresh_after_wait(waited):
        """The market only LOOKS different (mid past the thesis entry) once
        a wait has actually happened - exactly what a real clock would show
        after a real CONFIRM_DELAY_S sleep, without needing a fake clock."""
        def summarize_coin(coin, now_ms=None):
            if now_ms is not None and waited["v"]:
                return dict(BRENT_LV, mid=112.25)
            return dict(BRENT_LV, mid=112.04)
        return summarize_coin

    # Half 1: the real fix. sleep() marks that the wait happened before the
    # second, uncached read, which then sees the disagreement.
    waited = {"v": False}
    monkeypatch.setattr(levels, "summarize_coin", _fresh_after_wait(waited))

    def sleep_marks_waited(seconds):
        waited["v"] = True
    W.watch(once=True, cooldown_min=30, sleep=sleep_marks_waited, clock=lambda: t0)
    out = capsys.readouterr().out
    assert '"fire"' not in out and "never chase" in out

    # Half 2: a watcher that asks again WITHOUT waiting (sleep a no-op) -
    # the same cached-levels pass fires, because the second read still
    # lands before the disagreement would show up. Proves the wait, not
    # merely the second read, is what the fix depends on.
    waited2 = {"v": False}
    _stub(monkeypatch, tmp_path, [112.04])
    monkeypatch.setattr(levels, "summarize_coin", _fresh_after_wait(waited2))
    assert W.watch(once=True, cooldown_min=30, sleep=lambda s: None, clock=lambda: t0) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["fire"] == "oil" and line["mid"] == 112.04
    assert json.loads((tmp_path / "fired.json").read_text())["oil"] == 1000.0


# ── the headline step (operator, 2026-11-05: "FIX THEM") ────────────────────

def _news(title, now_s):
    from datetime import datetime, timezone
    return {"title": title, "url": "u", "source": "Reuters",
            "published_at": datetime.fromtimestamp(now_s - 60, tz=timezone.utc)}


def test_first_run_primes_and_never_fires_on_the_headlines_already_out(monkeypatch, tmp_path, capsys):
    _stub(monkeypatch, tmp_path, [112.30], news=[_news("Iran, US reach deal to reopen Strait of Hormuz", 1000.0)])
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    out = capsys.readouterr().out
    assert "headlines: primed" in out and "FIRE" not in out
    state = json.loads((tmp_path / "headline_state.json").read_text())
    assert len(state["seen"]) == 1 and state["hist"]["xyz:BRENTOIL"] == [[1000000, 112.3]]
    assert not (tmp_path / "headline_alerts.json").exists()


def test_a_new_headline_wakes_the_desk_in_a_position_and_records_the_alert(monkeypatch, tmp_path, capsys):
    from services.intel import headline_watch as HW
    HW.save_state({"seen": [], "last_news_s": 0.0, "odds": {}}, str(tmp_path / "headline_state.json"))
    _stub(monkeypatch, tmp_path, [112.30], held="holds position on xyz:BRENTOIL",
          news=[_news("US strikes Iran's Qeshm Island", 1000.0)])
    assert W.watch(once=False, cooldown_min=30, clock=lambda: 1000.0) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line == {"fire": "oil", "also": [], "reason": "headline", "utc": line["utc"], "coin": "xyz:BRENTOIL",
                    "label": "iran/hormuz", "headline": "US strikes Iran's Qeshm Island"}
    assert json.loads((tmp_path / "fired.json").read_text()) == {"oil:headline": 1000.0}
    alerts = HW.read_alerts(str(tmp_path / "headline_alerts.json"))
    assert len(alerts) == 1 and alerts[0]["at_ms"] == 1_000_000 and alerts[0]["group"] == "oil"
    # the headline blackout now holds new entries on the oil coins for 30 min
    from services.strategies.event_blackout import event_blackout
    assert event_blackout("xyz:BRENTOIL", 1_000_000 + 29 * 60_000)["kind"] == "headline"
    assert event_blackout("xyz:BRENTOIL", 1_000_000 + 30 * 60_000) is None


def test_the_headline_cooldown_key_does_not_rewake_a_desk_that_just_woke(monkeypatch, tmp_path, capsys):
    from services.intel import headline_watch as HW
    HW.save_state({"seen": [], "last_news_s": 0.0, "odds": {}}, str(tmp_path / "headline_state.json"))
    (tmp_path / "fired.json").write_text(json.dumps({"oil:headline": 900.0}))
    _stub(monkeypatch, tmp_path, [112.30], news=[_news("OPEC+ agrees surprise output cut", 1000.0)])
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    out = capsys.readouterr().out
    assert "headlines: 1 hit(s) (capped or cooling down)" in out and '"fire"' not in out
    assert len(HW.read_alerts(str(tmp_path / "headline_alerts.json"))) == 1     # recorded all the same


def test_a_price_shock_wakes_the_desk_with_its_own_mid_history(monkeypatch, tmp_path, capsys):
    from services.intel import headline_watch as HW
    hist = [[1000_000 - 60_000 * (10 - i), 113.5] for i in range(10)]
    HW.save_state({"seen": [], "last_news_s": 1000.0, "odds": {}, "hist": {"xyz:BRENTOIL": hist}},
                  str(tmp_path / "headline_state.json"))
    _stub(monkeypatch, tmp_path, [112.30])        # -1.06% against the saved window
    assert W.watch(once=False, cooldown_min=30, clock=lambda: 1000.0) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["reason"] == "headline" and line["label"] == "price shock" and line["coin"] == "xyz:BRENTOIL"
    assert "-1.06%" in line["headline"]


# ── weekend flat (operator, 2026-11-05) ──────────────────────────────────────

SAT_S = 1794139200.0        # Sat 2026-11-07 12:00Z, inside Fri 21:00Z to Sun 23:00Z
WINDOW_START_MS = 1793998800000


def _quiet_calendar(monkeypatch):
    from services.intel import headline_watch, scheduled_events
    monkeypatch.setattr(scheduled_events, "blackout", lambda coin, t, path=None: None)
    monkeypatch.setattr(headline_watch, "headline_blackout", lambda coin, t, side=None, path=None: None)


def test_weekend_open_wakes_a_desk_that_holds_a_position_once(monkeypatch, tmp_path, capsys):
    _quiet_calendar(monkeypatch)
    _stub(monkeypatch, tmp_path, [112.30], held="holds position on xyz:BRENTOIL")
    assert W.watch(once=False, cooldown_min=30, clock=lambda: SAT_S) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["fire"] == "oil" and line["reason"] == "weekend_flat" and line["coin"] == "xyz:BRENTOIL"
    assert line["held"] == "holds position on xyz:BRENTOIL" and line["until_utc"] == "2026-11-08 23:00Z"
    assert json.loads((tmp_path / "fired.json").read_text()) == {"oil:weekend_flat": WINDOW_START_MS}
    assert "FIRE" in (tmp_path / "watch.log").read_text()
    # the same window again, still holding: no second fire, the desk is a plain blackout skip
    _stub(monkeypatch, tmp_path, [112.30], held="holds position on xyz:BRENTOIL")
    W.watch(once=True, cooldown_min=30, clock=lambda: SAT_S + 600)
    out = capsys.readouterr().out
    assert '"fire"' not in out and "Weekend flat: no new oil entries until 2026-11-08 23:00Z" in out


def test_weekend_open_does_not_wake_a_flat_desk(monkeypatch, tmp_path, capsys):
    _quiet_calendar(monkeypatch)
    _stub(monkeypatch, tmp_path, [112.04])                   # the entry rule would pass on a weekday
    W.watch(once=True, cooldown_min=30, clock=lambda: SAT_S)
    out = capsys.readouterr().out
    assert '"fire"' not in out and "Weekend flat" in out
    assert not (tmp_path / "fired.json").exists()


def test_weekend_fire_is_pure_and_keyed_on_the_window():
    bo = {"flatten": True, "start_ms": WINDOW_START_MS, "until_utc": "2026-11-08 23:00Z", "reason": "Weekend flat"}
    fired = {}
    assert W.weekend_fire("oil", "xyz:BRENTOIL", bo, fired, SAT_S, lambda: "") is None and fired == {}
    line = W.weekend_fire("oil", "xyz:BRENTOIL", bo, fired, SAT_S, lambda: "holds resting_entry on xyz:BRENTOIL")
    assert line["reason"] == "weekend_flat" and fired == {"oil:weekend_flat": WINDOW_START_MS}
    assert W.weekend_fire("oil", "xyz:BRENTOIL", bo, fired, SAT_S + 60, lambda: "holds position on xyz:BRENTOIL") is None
    nxt = dict(bo, start_ms=WINDOW_START_MS + 7 * 86_400_000)      # next weekend fires again
    assert W.weekend_fire("oil", "xyz:BRENTOIL", nxt, fired, SAT_S, lambda: "holds position on xyz:BRENTOIL")
    def boom():
        raise RuntimeError("venue")
    assert W.weekend_fire("oil", "xyz:BRENTOIL", bo, {}, SAT_S, boom) is None   # a bad read waits a minute


# ── the one-sided headline hold (operator, 2026-11-05: "we SHOULDNT PAUSE FOR BULLISH HEADLINES") ──

def _record(tmp_path, direction, title):
    from services.intel import headline_watch as HW
    HW.save_state({"seen": [], "last_news_s": 1000.0, "odds": {}}, str(tmp_path / "headline_state.json"))
    (tmp_path / "headline_alerts.json").write_text(json.dumps([
        {"group": "oil", "kind": "news", "label": "iran/hormuz", "title": title, "at_ms": 1_000_000 - 5 * 60_000,
         "direction": direction}]))


def test_a_headline_that_supports_the_long_thesis_does_not_stop_the_watcher_firing(monkeypatch, tmp_path, capsys):
    _record(tmp_path, 1, "US strikes Iran's Qeshm Island")
    _stub(monkeypatch, tmp_path, [112.04])                   # the entry rule passes
    # the entry rule reaches confirm_fire's CONFIRM_DELAY_S wait; a no-op
    # sleep keeps this test deterministic and fast.
    assert W.watch(once=False, cooldown_min=30, sleep=lambda s: None, clock=lambda: 1000.0) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["fire"] == "oil" and line["mid"] == 112.04 and "reason" not in line


def test_a_headline_against_or_unknown_still_holds_the_long_thesis(monkeypatch, tmp_path, capsys):
    _record(tmp_path, -1, "U.S., Iran edge closer to deal")
    _stub(monkeypatch, tmp_path, [112.04])
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    out = capsys.readouterr().out
    assert '"fire"' not in out and "Headline blackout: no new long entries until" in out and "against the long" in out
    _record(tmp_path, 0, "Trump's Iran Strategy Reaches a Critical Crossroads")
    _stub(monkeypatch, tmp_path, [112.04])
    W.watch(once=True, cooldown_min=30, clock=lambda: 1000.0)
    out = capsys.readouterr().out
    assert '"fire"' not in out and "direction unknown" in out
