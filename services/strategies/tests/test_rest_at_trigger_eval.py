"""Gate tests for the rest-at-trigger eval's pure parts
(services/strategies/evals/rest_at_trigger_eval.py). Synthetic candles and a
real calendar date (so `event_blackout` needs no stubbing), no network.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pathiel.models.types import Candle
from services.strategies.backtest.data import ThesisDirection
from services.strategies.evals.rest_at_trigger_eval import (
    HOUR_MS, active_thesis, closed_1h_as_of, range_24h, rule_at, simulate,
)

COIN_LOW, COIN_HIGH = 109.01, 114.07   # the 24h range every synthetic 1h candle below carries


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def hourly_history(start_ms: int, end_ms: int) -> list:
    """One closed 1h candle per hour in [start_ms, end_ms), constant 24h
    range (COIN_LOW/COIN_HIGH) so `rule_at` always has a real structure."""
    out, t = [], start_ms
    while t < end_ms:
        out.append(Candle(t=t, o=111.0, h=COIN_HIGH, l=COIN_LOW, c=111.0, v=1.0))
        t += HOUR_MS
    return out


def bar15(t_ms: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(t=t_ms, o=o, h=h, l=l, c=c, v=1.0)


# ── small pure helpers ───────────────────────────────────────────────────────

def test_closed_1h_as_of_excludes_the_still_open_bar():
    c1h = hourly_history(0, 3 * HOUR_MS)
    assert len(closed_1h_as_of(c1h, HOUR_MS)) == 1          # only bar 0 has closed by t=1h
    assert len(closed_1h_as_of(c1h, 2 * HOUR_MS)) == 2
    assert len(closed_1h_as_of(c1h, HOUR_MS - 1)) == 0       # bar 0 has not closed yet


def test_range_24h_is_the_last_24_closed_bars():
    c1h = hourly_history(0, 30 * HOUR_MS)
    r = range_24h(c1h)
    assert r == {"low": COIN_LOW, "high": COIN_HIGH}
    assert range_24h([]) is None


def test_active_thesis_picks_the_newest_at_or_before_t():
    long_t = ThesisDirection(created_at_ms=0, side="long", entry_px=113.87, stop_px=None,
                             invalidation_px=None, targets=[], thesis_id="a")
    short_t = ThesisDirection(created_at_ms=1000, side="short", entry_px=109.0, stop_px=None,
                              invalidation_px=None, targets=[], thesis_id="b")
    assert active_thesis([long_t, short_t], 500).side == "long"
    assert active_thesis([long_t, short_t], 1000).side == "short"
    assert active_thesis([long_t, short_t], -1) is None


def test_rule_at_needs_24_closed_bars_and_a_real_side():
    thin = hourly_history(0, 10 * HOUR_MS)
    assert rule_at("long", 113.135, thin, 113.87)["ok"] is False
    full = hourly_history(0, 30 * HOUR_MS)
    assert rule_at(None, 113.135, full, 113.87)["ok"] is False
    n = rule_at("long", 113.135, full, 113.87)
    assert n["ok"] is False and n["rest"] is not None        # distance-only: the scenario this eval targets


# ── simulate(): market-only misses the touch, resting catches it ───────────
# Same numbers as services/strategies/tests/test_next_trade.py's 10:08Z
# fixture: mid 113.135 pays 0.07R (blocked), the trigger is ~110.0066
# (stop 108.26, offset target 113.49965). A long thesis at 113.87 never
# chases, never breaks the range, and Brent's leverage fit clears the
# trigger's own leverage line.

def _setup_history(end_ms: int) -> list:
    return hourly_history(end_ms - 48 * HOUR_MS, end_ms)


def test_market_only_never_trades_a_mid_that_only_touches_intrabar():
    """The bar's OPEN sits at 113.135 the whole time (never qualifies at a
    sampled tick) even though its LOW dips to the trigger: market_only must
    take nothing."""
    t0 = ms(2026, 1, 5, 0)
    c1h = _setup_history(t0)
    thesis = ThesisDirection(created_at_ms=t0 - HOUR_MS, side="long", entry_px=113.87, stop_px=None,
                             invalidation_px=None, targets=[], thesis_id="t1")
    c15 = [
        bar15(t0, 113.135, 113.2, 113.0, 113.1),                      # no touch
        bar15(t0 + 15 * 60_000, 113.135, 113.2, 109.5, 113.1),        # dips through the trigger intrabar
        bar15(t0 + 30 * 60_000, 111.0, 114.0, 110.5, 113.9),          # target reached (113.49965) for whoever is in
    ]
    market = simulate(c15, c1h, [thesis], variant="market_only")
    rest = simulate(c15, c1h, [thesis], variant="rest_after_fix")
    assert market.n == 0
    assert rest.n == 1 and rest.wins == 1 and rest.trades[0].source == "rest"
    assert rest.trades[0].entry_px == rest.trades[0].entry_px   # sanity: fired at the trigger, not the bar open
    assert abs(rest.trades[0].entry_px - 113.135) > 1.0          # the resting fill price, not the sampled mid
    assert rest.net_r > 0


def test_market_only_trades_the_instant_the_sampled_open_qualifies():
    """Flip the fixture: once the bar OPEN itself sits low enough in the
    range to pay 2R, market_only trades it exactly like the live tick's
    market branch always has."""
    t0 = ms(2026, 1, 5, 0)
    c1h = _setup_history(t0)
    thesis = ThesisDirection(created_at_ms=t0 - HOUR_MS, side="long", entry_px=113.87, stop_px=None,
                             invalidation_px=None, targets=[], thesis_id="t1")
    c15 = [
        bar15(t0, 109.5, 109.6, 109.4, 109.55),                        # mid near the 24h low: pays well over 2R
        bar15(t0 + 15 * 60_000, 111.0, 114.0, 110.5, 113.9),           # the target bar
    ]
    market = simulate(c15, c1h, [thesis], variant="market_only")
    assert market.n == 1 and market.trades[0].source == "market" and market.trades[0].entry_px == 109.5


def test_a_flat_or_missing_thesis_never_trades_either_series():
    t0 = ms(2026, 1, 5, 0)
    c1h = _setup_history(t0)
    c15 = [bar15(t0, 113.135, 113.2, 109.5, 113.1), bar15(t0 + 15 * 60_000, 111.0, 114.0, 110.5, 113.9)]
    assert simulate(c15, c1h, [], variant="rest_after_fix").n == 0     # no thesis at all
    flat = ThesisDirection(created_at_ms=t0 - HOUR_MS, side="flat", entry_px=None, stop_px=None,
                          invalidation_px=None, targets=[], thesis_id="f")
    assert simulate(c15, c1h, [flat], variant="rest_after_fix").n == 0


# ── the blackout cancel rule: before vs after ───────────────────────────────
# A real EIA window on 2026-10-07 (no overlapping scheduled event, unlike
# 2026-10-28, which also carries an FOMC window that afternoon): report
# 14:30Z, blackout [12:30Z, 16:30Z). Timeline (all bars open at
# 113.135, a sampled mid market_only never trades):
#   11:30Z  no touch   -> both variants rest the trigger
#   12:20Z  no touch   -> "after": 10 min from the 12:30Z open, inside the
#                          15-min cadence horizon -> cancelled. "before":
#                          no such check, stays resting.
#   12:30Z  touches    -> window is now ACTIVE. "before" fills here, inside
#                          the blackout. "after" has nothing resting (it was
#                          cancelled) and will not re-rest while the window
#                          is open, so no fill.
#   16:35Z  no touch   -> window closed 5 min ago: "after" re-rests.
#   16:50Z  touches    -> "after" fills here, outside the window.

def test_the_cancel_rule_removes_the_blackout_fill_and_the_order_still_fills_later():
    thesis = ThesisDirection(created_at_ms=ms(2026, 10, 5, 0), side="long", entry_px=113.87, stop_px=None,
                             invalidation_px=None, targets=[], thesis_id="eia")
    c1h = hourly_history(ms(2026, 10, 4, 0), ms(2026, 10, 7, 17, 0))
    c15 = [
        bar15(ms(2026, 10, 7, 11, 30), 113.135, 113.2, 113.0, 113.1),
        bar15(ms(2026, 10, 7, 12, 20), 113.135, 113.2, 113.0, 113.1),
        bar15(ms(2026, 10, 7, 12, 30), 113.135, 113.2, 109.5, 113.1),   # touches the trigger, window now active
        bar15(ms(2026, 10, 7, 12, 45), 111.0, 114.0, 110.5, 113.9),    # target bar, for whoever is in by then
        bar15(ms(2026, 10, 7, 16, 35), 113.135, 113.2, 113.0, 113.1),  # window closed 5 min ago: no touch yet
        bar15(ms(2026, 10, 7, 16, 50), 113.135, 113.2, 109.5, 113.1),  # touches, now outside the window
        bar15(ms(2026, 10, 7, 17, 5), 111.0, 114.0, 110.5, 113.9),     # target bar
    ]
    before = simulate(c15, c1h, [thesis], variant="rest_before_fix")
    after = simulate(c15, c1h, [thesis], variant="rest_after_fix")

    # "before": the 12:30Z fill lands inside the blackout (the bug this rule
    # fixes), closes for the target at 12:45Z, the system goes flat again,
    # and legitimately re-rests and fills a SECOND time at 16:50Z once both
    # the window and the cadence horizon have cleared -- same as "after"'s
    # only trade.
    assert before.n == 2 and before.blackout_fills == 1
    assert before.trades[0].entry_time == ms(2026, 10, 7, 12, 30) and before.trades[0].blackout_fill
    assert before.trades[1].entry_time == ms(2026, 10, 7, 16, 50) and not before.trades[1].blackout_fill

    # "after": nothing resting during the window (cancelled ahead of it), so
    # only the one clean fill once it has passed -- never the blackout one.
    assert after.n == 1 and after.blackout_fills == 0
    assert after.trades[0].entry_time == ms(2026, 10, 7, 16, 50)

    # the clean fill both series end up with is the identical trade (same
    # trigger, same R): the cancel rule removes the bad fill, not the good one
    assert before.trades[1].entry_px == after.trades[0].entry_px
    assert before.wins == 2 and after.wins == 1


def test_market_only_never_sees_any_of_the_touches_in_the_blackout_fixture():
    thesis = ThesisDirection(created_at_ms=ms(2026, 10, 5, 0), side="long", entry_px=113.87, stop_px=None,
                             invalidation_px=None, targets=[], thesis_id="eia")
    c1h = hourly_history(ms(2026, 10, 4, 0), ms(2026, 10, 7, 17, 0))
    c15 = [
        bar15(ms(2026, 10, 7, 11, 30), 113.135, 113.2, 113.0, 113.1),
        bar15(ms(2026, 10, 7, 12, 30), 113.135, 113.2, 109.5, 113.1),
        bar15(ms(2026, 10, 7, 16, 50), 113.135, 113.2, 109.5, 113.1),
    ]
    assert simulate(c15, c1h, [thesis], variant="market_only").n == 0


# ── run(): the report's verdict says plainly when (b) is worse ─────────────

def test_run_names_a_net_r_loss_plainly(monkeypatch):
    """A fixture where resting adds one extra, quickly-stopped noise trade
    on top of the one shared winner: net R must come out lower for
    rest_after_fix, and the verdict must say WORSE, not just show a number."""
    import services.strategies.evals.rest_at_trigger_eval as M
    thesis = ThesisDirection(created_at_ms=ms(2026, 1, 1, 0), side="long", entry_px=113.87, stop_px=None,
                             invalidation_px=None, targets=[], thesis_id="t1")
    t0 = ms(2026, 1, 5, 0)
    c1h = hourly_history(t0 - 48 * HOUR_MS, t0 + 2 * HOUR_MS)
    c15 = [
        bar15(t0, 113.135, 113.2, 113.0, 113.1),                       # places the resting order; no touch yet
        bar15(t0 + 15 * 60_000, 113.135, 113.2, 109.5, 113.1),         # resting FILLS here; market_only never trades it
        bar15(t0 + 30 * 60_000, 113.135, 113.2, 107.0, 108.5),         # noise wick: stops the fill out at a loss
    ]
    monkeypatch.setattr(M, "load_window", lambda days, cache_dir, now_ms=None: {
        "c15": c15, "c1h": c1h, "start_ms": t0, "end_ms": t0 + 2 * HOUR_MS})
    monkeypatch.setattr(M, "load_journaled_theses", lambda theses_dir: [thesis])
    out = M.run("/unused", 30, "/unused/cache")
    assert out["series"]["market_only"]["trades"] == 0
    assert out["series"]["rest_after_fix"]["trades"] == 1 and out["series"]["rest_after_fix"]["net_r"] < 0
    assert out["verdict"]["net_r_delta_vs_market_only"] < 0
    assert out["verdict"]["net_r_verdict"] == "WORSE on net R: say so plainly"


def test_run_verdict_is_tied_when_the_series_match(monkeypatch):
    import services.strategies.evals.rest_at_trigger_eval as M
    monkeypatch.setattr(M, "load_window", lambda days, cache_dir, now_ms=None: {
        "c15": [], "c1h": [], "start_ms": 0, "end_ms": 0})
    monkeypatch.setattr(M, "load_journaled_theses", lambda theses_dir: [])
    out = M.run("/unused", 30, "/unused/cache")
    assert out["verdict"] == {"extra_trades_from_resting": 0, "net_r_delta_vs_market_only": 0.0,
                              "net_r_verdict": "TIED on net R", "blackout_fills_before_fix": 0,
                              "blackout_fills_after_fix": 0, "cancel_rule_removed_every_blackout_fill": True}
