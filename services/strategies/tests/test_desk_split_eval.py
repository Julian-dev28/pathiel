"""Gate tests for the pure pieces of the desk split eval
(services/strategies/evals/desk_split_test.py): the verdict rules, the
plateau on the split grid, leave-one-out, the real fills rescaled, weeks
and weekly returns, and Brent's regime numbers. No network, no files.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pathiel.models.types import Candle
from services.strategies.evals import desk_split_test as T

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def ms(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def _row(final: float, dd: float = 50.0) -> dict:
    return {"final_equity": final, "max_dd_pct": dd}


def _rows(finals_by_ds: dict, dd: dict = None) -> dict:
    """{ds: {split: stats}} from {ds: [final per split in SPLIT_ORDER]}."""
    dd = dd or {}
    return {ds: {n: _row(f, dd.get(n, 50.0)) for n, f in zip(T.SPLIT_ORDER, fs)}
            for ds, fs in finals_by_ds.items()}


def test_split_grid():
    assert T.SPLIT_ORDER == ["100/0", "90/10", "70/30", "50/50", "30/70", "0/100"]
    assert T.LIVE == "90/10"
    for _, oil, silver in T.SPLITS:
        assert oil + silver == pytest.approx(1.0)


def test_verdict_adopts_a_plateau_that_beats_live_with_less_drawdown():
    #                 100/0  90/10  70/30  50/50  30/70  0/100
    rows = _rows({"a": [90, 100, 110, 112, 105, 80],
                  "b": [95, 100, 104, 106, 101, 70],
                  "c": [99, 100, 98, 97, 96, 60]},
                 dd={"90/10": 50.0, "70/30": 40.0, "50/50": 35.0, "30/70": 45.0, "0/100": 70.0, "100/0": 55.0})
    v = T.verdicts(rows)
    assert v["70/30"]["adopt"] and v["70/30"]["beats"] == 2
    assert v["50/50"]["adopt"]              # neighbours 70/30 and 30/70 both beat 2/3
    assert not v["0/100"]["adopt"] and "beats on most" in v["0/100"]["fails"]
    assert not v["100/0"]["adopt"] and "plateau" in v["100/0"]["fails"]   # its only neighbour is live


def test_verdict_refuses_a_spike_and_a_deeper_drawdown():
    rows = _rows({"a": [90, 100, 120, 99, 95, 80], "b": [90, 100, 120, 99, 95, 80], "c": [90, 100, 90, 99, 95, 80]},
                 dd={"70/30": 40.0})
    v = T.verdicts(rows)
    assert v["70/30"]["most"] and not v["70/30"]["plateau"]       # 50/50 does not beat live
    assert not v["70/30"]["adopt"]
    rows = _rows({"a": [90, 100, 120, 120, 95, 80], "b": [90, 100, 120, 120, 95, 80], "c": [90, 100, 90, 99, 95, 80]},
                 dd={"90/10": 30.0, "70/30": 40.0, "50/50": 40.0})
    v = T.verdicts(rows)
    assert v["70/30"]["plateau"] and not v["70/30"]["dd_ok"] and not v["70/30"]["adopt"]


def test_leave_one_out_counts_the_drops_a_verdict_survives():
    rows = _rows({"a": [90, 100, 110, 112, 105, 80], "b": [95, 100, 104, 106, 101, 70],
                  "c": [99, 100, 98, 97, 96, 60], "d": [99, 100, 101, 101, 101, 60]},
                 dd={"90/10": 50.0, "70/30": 40.0, "50/50": 35.0, "30/70": 45.0})
    loo = T.leave_one_out(rows)
    assert set(loo) == {"100/0", "70/30", "50/50", "30/70", "0/100"}
    assert loo["70/30"] == {"held": 4, "of": 4}
    assert loo["100/0"]["held"] == 0


def test_non_mirror_scores_only_the_four_datasets():
    rows = _rows({ds: [90, 100, 110, 110, 110, 110] for ds in T.NON_MIRROR})
    rows["always_long_30d"] = {n: _row(f) for n, f in zip(T.SPLIT_ORDER, [200, 100, 50, 40, 30, 20])}
    nm = T.non_mirror(rows)
    assert nm["70/30"]["of"] == 4 and nm["70/30"]["beats"] == 4


def test_real_fills_rescaled():
    """Since 10-31 at the live split the real nets add up: +7.83 - 33.87 - 1.82 - 2.56."""
    got = T.real_by_split()
    assert got["90/10"] == pytest.approx(-30.42)
    # 50/50: oil x 0.5/0.9, silver x 0.5/0.1
    assert got["50/50"] == pytest.approx((7.83 - 33.87) * 0.5 / 0.9 + (-1.82 - 2.56) * 5.0, abs=0.01)
    assert got["0/100"] == pytest.approx((-1.82 - 2.56) * 10.0, abs=0.01)
    assert T.real_by_split(since="2026-10-01 00:00")["90/10"] == pytest.approx(
        0.9 * (1.05 + 3.81 - 6.04 - 5.81 + 19.86 + 49.02) + 7.83 - 33.87 - 1.82 - 2.56, abs=0.01)


def test_real_weekly_buckets_by_exit_week():
    w = T.real_weekly()
    assert w["oil"][ms(2026, 10, 19)] == pytest.approx(1.05 + 3.81 - 6.04)
    assert w["oil"][ms(2026, 10, 26)] == pytest.approx(-5.81 + 19.86 + 49.02 + 7.83)   # 11-01 is a Sunday
    assert w["oil"][ms(2026, 11, 2)] == pytest.approx(-33.87)
    assert w["silver"] == {ms(2026, 11, 2): pytest.approx(-4.38)}


def test_week_start_is_monday_utc():
    assert T.week_start(ms(2026, 11, 2, 11)) == ms(2026, 11, 2)
    assert T.week_start(ms(2026, 11, 1, 23, 59)) == ms(2026, 10, 26)
    assert T.week_start(ms(2026, 10, 26)) == ms(2026, 10, 26)


def test_weekly_returns():
    w1, w2, w3 = ms(2026, 10, 12), ms(2026, 10, 19), ms(2026, 10, 26)
    curve = [(w1 + DAY_MS, 230.0), (w1 + 5 * DAY_MS, 250.0), (w2 + DAY_MS, 200.0), (w2 + 6 * DAY_MS, 300.0)]
    got = T.weekly_returns(curve, 225.0, [w1, w2, w3])
    assert got[w1] == pytest.approx(round(100 * (250 / 225 - 1), 1))
    assert got[w2] == pytest.approx(20.0)
    assert got[w3] is None


def test_regime_by_week_trend_vs_chop():
    """A straight climb: efficiency 1, every hour breaks the 24h high. A
    zigzag: efficiency near 0, no breaks after the first day."""
    w = ms(2026, 10, 19)
    start = w - 2 * DAY_MS
    up = [Candle(t=start + i * HOUR_MS, o=100 + i * 0.1, h=100 + i * 0.1 + 0.1, l=100 + i * 0.1 - 0.02,
                 c=100 + (i + 1) * 0.1, v=1.0) for i in range(9 * 24)]
    days = [Candle(t=w - (30 - i) * DAY_MS, o=90 + i, h=91 + i, l=89 + i, c=90 + i, v=1.0) for i in range(30)]
    r = T.regime_by_week(up, days, [w])[0]
    assert r["efficiency"] == pytest.approx(1.0)
    assert r["breaks_up"] == r["hours"] == 168 and r["breaks_down"] == 0
    assert r["close_chg_pct"] > 0 and r["trend20_pct"] > 0 and r["vs_sma20_pct"] > 0
    zig = [Candle(t=start + i * HOUR_MS, o=100.0, h=101.0, l=99.0, c=100.5 if i % 2 else 99.5, v=1.0)
           for i in range(9 * 24)]
    z = T.regime_by_week(zig, days, [w])[0]
    assert z["efficiency"] < 0.05
    assert z["breaks_up"] == 0 and z["breaks_down"] == 0
    assert z["atr_pct"] > 0
