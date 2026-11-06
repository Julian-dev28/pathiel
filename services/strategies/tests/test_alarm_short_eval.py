"""Gate tests for the alarm-short what-if (services/strategies/evals/alarm_short_test.py)."""
from services.strategies.evals import alarm_short_test as A


def _bars(closes, spread=0.05):
    return [{"t": i * A.Q, "o": c, "h": c + spread, "l": c - spread, "c": c} for i, c in enumerate(closes)]


def test_a_one_percent_15m_drop_is_a_down_shock_and_rearms():
    closes = [100.0] * 20 + [98.9] + [98.9] * 5 + [97.8]
    al = [a for a in A.alarms(_bars(closes)) if a["kind"] == "shock"]
    assert al[0]["dir"] == -1 and al[0]["i"] == 20
    assert all(a["i"] != 26 for a in al)          # inside the re-arm window: no second shock


def test_a_short_hits_its_target_and_pays_after_fees():
    closes = [100.0] * 100 + [98.9] + [98.0 - 0.2 * k for k in range(20)]
    bars = _bars(closes)
    tr = A.trade(bars, 100, -1, min_r=None, target_r=1.0)
    assert tr and tr["reason"] == "target" and 0.9 < tr["r"] < 1.0


def test_a_stop_counts_minus_one_r_and_the_2r_gate_refuses_thin_setups():
    closes = [100.0] * 100 + [98.9] + [101.5] * 5
    bars = _bars(closes)
    tr = A.trade(bars, 100, -1, min_r=None, target_r=1.0)
    assert tr["reason"] == "stop" and tr["r"] < -1.0
    assert A.trade(bars, 100, -1, min_r=50.0, target_r=None) is None


def test_run_reports_thirds_and_the_pass_bar():
    out = A.run(_bars([100.0] * 300), -1, min_r=None, target_r=1.0)
    assert out["trades"] == 0 and out["pass"] is False and len(out["thirds_r"]) == 3
