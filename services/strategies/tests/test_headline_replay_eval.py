"""services/strategies/evals/headline_replay.py: the pure replay of a price
shock on 1h candles (synthetic bars; the eval itself fetches the real
2026-11-04 Brent day)."""
from __future__ import annotations

from datetime import datetime, timezone

from services.strategies.evals import headline_replay as R

H = 3_600_000
DAY0 = int(datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp() * 1000)


def _bar(hour, o, h, l, c):  # noqa: E741 - OHLC
    return {"t": str(DAY0 + hour * H), "o": str(o), "h": str(h), "l": str(l), "c": str(c)}


CANDLES = [
    _bar(-1, 100.0, 100.3, 99.8, 100.0),
    _bar(0, 100.0, 100.2, 99.5, 99.9),      # -0.1% close, -0.5% low: quiet
    _bar(1, 99.9, 100.0, 98.7, 99.3),       # low -1.2% vs 99.9, close -0.6%: wick only
    _bar(2, 99.3, 99.4, 97.9, 98.0),        # close -1.31% vs 99.3: fires by 03:00Z
    _bar(3, 98.0, 98.3, 97.8, 98.1),        # quiet
    _bar(4, 98.1, 99.4, 98.0, 99.3),        # +1.22% close: fires by 05:00Z (upward leg)
]


def test_shock_bars_report_close_to_close_and_wick_only_crossings():
    shocks = R.shock_bars(reversed(CANDLES), 0.01)      # any order in
    assert [R._utc(s["bar_open_ms"]) for s in shocks] == ["01:00Z", "02:00Z", "04:00Z"]
    wick, down, up = shocks
    assert wick["fired_by_ms"] is None and wick["touched_in_bar"] and wick["extreme_move"] == -0.012
    assert down["fired_by_ms"] == DAY0 + 3 * H and down["close_move"] == -0.0131 and down["ref_close"] == 99.3
    assert up["fired_by_ms"] == DAY0 + 5 * H and up["close_move"] == 0.0122 and up["extreme"] == 99.4
    assert R.shock_bars(CANDLES, 0.02) == []


def test_price_at_reads_the_last_closed_bar_else_the_open():
    assert R.price_at(CANDLES, DAY0 + 2 * H + 37 * 60_000) == 99.3     # 02:37Z: the 01:00Z bar closed 99.3
    assert R.price_at(CANDLES, DAY0 - 30 * 60_000) == 100.0            # inside the first bar: its open
    assert R.price_at(CANDLES, DAY0 - 2 * H) is None


def test_replay_lines_up_the_shock_against_the_desk_reactions():
    res = R.replay(CANDLES, "xyz:BRENTOIL", (("stop filled", "2026-11-04T03:37Z"),), DAY0)
    assert res["threshold"] == 0.01 and res["open"] == 100.0 and res["low"] == 97.8
    assert res["move_open_to_low"] == -0.022
    assert res["first_shock_fired_by_ms"] == DAY0 + 3 * H and res["first_shock_move"] == -0.0131
    assert res["reactions"][0]["price"] == 98.0 and res["reactions"][0]["move_from_open"] == -0.02
    assert res["lead_minutes"] == 37
    text = R.render(res, "2026-11-04")
    assert "02:00Z" in text and "fired by" in text and "stop filled" in text and "37 min later" in text
    # silver's threshold is 1.2%: the 1.22% up leg still counts, the 1.31% down leg too
    assert len(R.replay(CANDLES, "xyz:SILVER", (), DAY0)["shocks"]) == 3
