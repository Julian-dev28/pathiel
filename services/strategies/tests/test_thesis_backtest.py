"""services/strategies/evals/thesis_backtest.py: AI theses for any coin,
scored under the live rules next to the plain direction controls."""
from __future__ import annotations

import math

from pathiel.models.types import Candle
from services.strategies.backtest.data import ThesisDirection
from services.strategies.evals import thesis_backtest as TB

FIFTEEN_MS = 900_000


def _series(days=8):
    n15 = days * 24 * 4
    px = [100 + 2.0 * math.sin(j / 9) + 0.5 * math.sin(j / 2.7) - j * 0.002 for j in range(n15 + 1)]
    c15 = [Candle(t=j * FIFTEEN_MS, o=px[j], h=max(px[j], px[j + 1]) + 0.1,
                  l=min(px[j], px[j + 1]) - 0.1, c=px[j + 1], v=1.0) for j in range(n15)]
    c1h = []
    for i in range(0, n15, 4):
        g = c15[i:i + 4]
        c1h.append(Candle(t=g[0].t, o=g[0].o, h=max(b.h for b in g), l=min(b.l for b in g), c=g[-1].c, v=1.0))
    return c15, c1h


def _thesis(t_ms, side, entry):
    return ThesisDirection(created_at_ms=t_ms, side=side, entry_px=entry, stop_px=None,
                           invalidation_px=None, targets=[], thesis_id=f"t{t_ms}")


def test_score_runs_every_series_on_the_same_window():
    c15, c1h = _series()
    start = 2 * 24 * 4 * FIFTEEN_MS
    out = TB.score(c15, c1h, [_thesis(start, "short", 0.0)], 20.0, start, c15[-1].t + FIFTEEN_MS)
    assert set(out["series"]) == {"ai_theses", "always_short", "always_long", "daily_sign"}
    assert out["leverage"] == 20.0 and out["thesis_sides"] == {"short": 1}
    assert all(t["side"] == "short" for t in out["ai_trades"])


def test_a_short_never_sells_below_its_thesis_entry():
    """Entry 1000 for a short means price never gets high enough: no trade (never chase)."""
    c15, c1h = _series()
    start = 2 * 24 * 4 * FIFTEEN_MS
    out = TB.score(c15, c1h, [_thesis(start, "short", 1000.0)], 20.0, start, c15[-1].t + FIFTEEN_MS)
    assert out["ai_trades"] == [] and out["series"]["ai_theses"]["trades"] == 0


def test_a_flat_thesis_trades_nothing():
    c15, c1h = _series()
    start = 2 * 24 * 4 * FIFTEEN_MS
    out = TB.score(c15, c1h, [_thesis(start, "flat", None)], 20.0, start, c15[-1].t + FIFTEEN_MS)
    assert out["series"]["ai_theses"]["trades"] == 0


def test_cites_all_time_level_reads_the_call_text():
    assert TB.cites_all_time_level({"call": {"one_liner": "the all-time low 51.856 caps any short"}})
    assert TB.cites_all_time_level({"call": {"invalidation": "a close above the ATH"}})
    assert not TB.cites_all_time_level({"call": {"one_liner": "short the rally to 54.6", "invalidation": None}})
