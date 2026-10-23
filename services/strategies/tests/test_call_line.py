"""services/strategies/call_line.py: the 20x-line computation from a
thesis's own call, against pathiel/agents/bracket.py's leverage rule. Uses
the real operator numbers from pathiel/agents/bracket.py's own docstring
(20x Brent long from 107.16, stop 106.70, liq ~104.41)."""
from __future__ import annotations

import pytest
from contracts.strategies.models import Call

from services.strategies.call_line import call_line


def _call(**overrides):
    base = dict(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=107.16,
               stop_px=106.70, targets=[108.45, 109.90], leverage=20, confidence=0.6,
               invalidation="closes below 106")
    base.update(overrides)
    return Call(**base)


def test_20x_ok_on_the_real_operator_numbers():
    out = call_line(_call(), coin_max_leverage=20, leverage=20)
    assert out["ok"] is True
    assert out["verdict"] == "20x OK"
    assert out["liq_px"] == pytest.approx(104.41, abs=0.03)
    assert out["r_to_targets"] == [
        {"px": 108.45, "r_multiple": 2.8}, {"px": 109.90, "r_multiple": 5.96}]


def test_a_wider_stop_fails_20x_and_reports_the_max():
    # Same entry/stop/tp as tests/test_oil_levels.py's own 20x-line check,
    # which pins max_leverage_ok at 8 for this exact stop.
    out = call_line(_call(entry_px=107.0, stop_px=101.65, targets=[115.0]),
                    coin_max_leverage=20, leverage=20)
    assert out["ok"] is False
    assert out["verdict"] == "no 20x: max 8x"
    assert out["max_leverage_ok"] == 8


def test_the_selector_recomputes_at_a_lower_leverage():
    wide = _call(stop_px=101.65, targets=[115.0])
    assert call_line(wide, coin_max_leverage=20, leverage=20)["ok"] is False
    at5 = call_line(wide, coin_max_leverage=20, leverage=5)
    assert at5["ok"] is True and at5["verdict"] == "5x OK"


def test_a_flat_or_pair_call_has_no_line():
    out = call_line(_call(side="flat", targets=[], stop_px=None, entry_px=None), coin_max_leverage=20, leverage=20)
    assert out["ok"] is False and "flat" in out["reason"]


def test_a_call_with_no_entry_or_stop_yet_is_reported_plainly():
    out = call_line(_call(entry_px=None, stop_px=None), coin_max_leverage=20, leverage=20)
    assert out["ok"] is False and "no entry/stop" in out["reason"]


def test_missing_venue_max_leverage_is_reported_plainly():
    out = call_line(_call(), coin_max_leverage=None, leverage=20)
    assert out["ok"] is False and "venue max leverage" in out["reason"]


def test_entry_zone_midpoint_is_used_when_entry_px_is_absent():
    out = call_line(_call(entry_px=None, entry_zone=[107.0, 107.32]), coin_max_leverage=20, leverage=20)
    assert out["entry_px"] == pytest.approx(107.16)


def test_no_targets_yields_an_empty_r_list_and_no_r_gate():
    out = call_line(_call(targets=[]), coin_max_leverage=20, leverage=20)
    assert out["ok"] is True and out["r_to_targets"] == [] and out["r_gate"] is None
