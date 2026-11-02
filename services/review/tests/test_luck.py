"""Gate tests for the luck check (services/review/luck.py)."""
from __future__ import annotations

from pathiel.models.types import Candle
from services.review import luck as L

F = L.FIVE_MS
DAY0 = 1_793_577_600_000      # 2026-11-02 00:00Z


def b(i, o, h, lo, c):
    return Candle(t=DAY0 + i * F, o=o, h=h, l=lo, c=c, v=0.0)


def test_walk_stop_first_when_one_bar_touches_both():
    bars = [b(0, 100, 103, 98, 101)]
    assert L.walk(bars, 0, True, 1.0, 2.0, DAY0 + F) == -1.0


def test_walk_target_and_mark_to_close():
    bars = [b(0, 100, 100.5, 99.5, 100.2), b(1, 100.2, 102.1, 100.1, 102.0)]
    assert L.walk(bars, 0, True, 1.0, 2.0, DAY0 + 2 * F) == 2.0
    assert abs(L.walk(bars, 0, True, 1.0, None, DAY0 + F) - 0.2) < 1e-9
    assert abs(L.walk(bars, 0, False, 1.0, None, DAY0 + F) + 0.2) < 1e-9


def test_walk_from_a_given_price():
    bars = [b(0, 100, 100.5, 99.5, 100.2)]
    assert abs(L.walk(bars, 0, True, 1.0, None, DAY0 + F, entry_px=99.7) - 0.5) < 1e-9


def _v_day():
    """Falls 0.05 a bar for 12h, then rises 0.05 a bar: a long entered at the
    bottom is good timing, one entered early in the fall is bad timing."""
    out = []
    for i in range(288 + 60):
        px = 110 - 0.05 * i if i < 144 else 110 - 0.05 * 144 + 0.05 * (i - 144)
        nxt = px - 0.05 if i < 144 else px + 0.05
        out.append(b(i, px, max(px, nxt) + 0.01, min(px, nxt) - 0.01, nxt))
    return out


def test_good_timing_scores_high_and_bad_timing_low():
    bars = _v_day()
    def trade(bar_i):
        t = DAY0 + bar_i * F + 10_000
        px = bars[bar_i].c
        return {"id": f"t{bar_i}", "side": "long", "entry_time": t, "exit_time": t + 48 * F,
                "entry_px": px, "stop_px": px - 0.5, "target_px": px + 1.0}
    good, bad = L.luck_check(trade(143), bars), L.luck_check(trade(10), bars)
    assert good["luck_actual_r"] == 2.0 and bad["luck_actual_r"] == -1.0
    assert good["luck_pct"] >= 60 and bad["luck_pct"] <= 40
    assert good["luck_n"] == L.N_DRAWS


def test_luck_is_seeded_by_trade_id_and_empty_without_bars_or_stop():
    tr = {"id": "t", "side": "long", "entry_time": DAY0 + 2 * F, "exit_time": DAY0 + 50 * F,
          "entry_px": 100.1, "stop_px": 99.0, "target_px": 101.5}
    bars = _v_day()
    assert L.luck_check(tr, bars) == L.luck_check(tr, bars)
    assert L.luck_check({**tr, "stop_px": None}, bars)["luck_pct"] is None
    assert L.luck_check(tr, [])["luck_pct"] is None
    assert L.luck_check({**tr, "exit_time": None}, bars)["luck_pct"] is None
