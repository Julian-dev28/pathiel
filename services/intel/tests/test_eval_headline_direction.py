"""The pure parts of services/intel/evals/eval_headline_direction.py: the
hold-minute arithmetic and the real-alert check. No network, no live file."""
from __future__ import annotations

from services.intel import headline_watch as HW
from services.intel.evals import eval_headline_direction as EV

M = 60_000
T0 = 1_793_900_000_000


def _a(at_ms, direction):
    return {"group": "oil", "kind": "price", "label": "price shock", "title": f"xyz:BRENTOIL {direction:+.2%} in 15 min",
            "at_ms": at_ms, "move": direction, "direction": 1 if direction > 0 else -1}


def test_hold_minutes_unions_windows_and_drops_the_aligned_ones():
    alerts = [_a(T0, 0.011), _a(T0 + 10 * M, -0.012), _a(T0 + 120 * M, 0.013)]
    assert EV.hold_minutes(alerts, None) == 40 + 30            # :00-:40 merged, then 30
    assert EV.hold_minutes(alerts, "long") == 30               # only the down move holds a long
    assert EV.hold_minutes(alerts, "short") == 60              # the two up moves hold a short
    assert EV.hold_minutes([], None) == 0


def test_candle_study_counts_from_synthetic_bars():
    # flat at 100 for 6h, a +1.5% bar, flat, a -1.5% bar, flat
    closes = [100.0] * 6 + [101.5] * 3 + [100.0] * 3
    bars = [{"t": T0 + i * 3_600_000, "c": c} for i, c in enumerate(closes)]
    st = EV.candle_study("xyz:BRENTOIL", bars)
    assert st["alerts"] == 2 and st["up"] == 1 and st["down"] == 1
    assert st["hold_min_old_rule"] == 60 and st["hold_min_long_thesis"] == 30 and st["hold_min_short_thesis"] == 30
    assert st["removed_for_long"] == 30 and st["removed_for_long_pct"] == 50.0 and st["down_holds_untouched"]


def test_check_real_reads_the_live_rows_shape():
    rows = [{"group": "oil", "kind": "news", "label": "iran/hormuz", "at_ms": T0,
             "title": "Iran Opens Strait of Hormuz to China and India as U.S. Blockade Continues"},
            {"group": "oil", "kind": "odds", "label": "odds jump", "at_ms": T0,
             "title": "Will Iran target an Arab country by November 15, 2026?: 68% -> 60%"}]
    out = EV.check_real(rows)
    assert out["checked"] == 2 and out["ok"]
    assert EV.classify_rows(rows)[0][0] == -1
    assert HW.alert_direction(rows[1]) == -1


def test_fixture_titles_load_and_classify_offline():
    fx = EV.fixture_titles()
    assert len(fx) > 50
    by_text = {t: HW.text_direction(g, t) for g, _k, t in fx}
    assert by_text["U.S. crude oil near $98 as Iran steps up attacks in Strait of Hormuz"] == 1
    assert by_text["OPEC+ Agrees to Boost Oil Output When Strait of Hormuz Reopens"] == -1
    assert by_text["U.S. Offers Another 17 Million SPR Barrels as Fuel Crisis Deepens"] == -1
    assert any(t.startswith("Oil Prices are DROPPING LIKE A ROCK") and d == -1 for t, d in by_text.items())
