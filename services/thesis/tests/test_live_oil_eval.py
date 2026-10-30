"""Offline gate tests for services/thesis/evals/live_oil_eval.py's PURE
helpers (target_implied_moves, scenario_calibration_report, format_report,
RepairWatcher). The eval's main() calls the real local Claude CLI against
LIVE market/news data and is a one-shot, paid, manual run - not a commit
gate (see the module's own docstring). This file never calls write_thesis,
build_analytics, build_packet, or main() itself; every Thesis/DeskAnalytics
here is hand-built so the report's own logic has fast, deterministic, free
coverage, and importing the module under test touches neither the network
nor PATHIEL_STATE_DIR (that's pinned inside main(), not at import time -
see test_pin_temp_state_dir_* below for the one function that does).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import pytest

from contracts.strategies.models import (
    AccountSnapshot, Call, DeskAnalytics, ForwardStats, InstrumentSnapshot, InstrumentStructure,
    PriceExtreme, Quantiles, Scenario, Thesis, Usage,
)
from services.thesis.evals import live_oil_eval as E

T0 = datetime(2026, 10, 30, tzinfo=timezone.utc)


def _q(p10, p25, p50, p75, p90) -> Quantiles:
    return Quantiles(p10=p10, p25=p25, p50=p50, p75=p75, p90=p90)


def _analytics(*, all_time_high=None, all_time_low=None, long_reach=None, final_ret=None) -> DeskAnalytics:
    structure = InstrumentStructure(coin="xyz:BRENTOIL", mid=113.87,
                                    all_time_high=all_time_high, all_time_low=all_time_low)
    forward = ForwardStats(
        coin="xyz:BRENTOIL", horizon_days=7, n_windows=200,
        sample_start=T0, sample_end=T0, final_ret=final_ret or _q(-0.09, -0.04, 0.01, 0.05, 0.1),
        final_ret_mean=0.01, long_mae=_q(-0.1, -0.06, -0.03, -0.01, -0.003),
        short_mae=_q(0.003, 0.01, 0.03, 0.06, 0.1), long_reach=long_reach,
    )
    return DeskAnalytics(
        desk_id="oil", as_of=T0,
        instruments=[InstrumentSnapshot(coin="xyz:BRENTOIL", label="Brent", mid=113.87, ret_1d=0.0,
                                        ret_5d=0.0, ret_20d=0.0, high_20d=115.0, low_20d=100.0,
                                        atr14_pct=0.02, vol_daily_10d=0.02, vol_daily_20d=0.02)],
        structure=[structure], forward=[forward],
        account=AccountSnapshot(equity_usd=1000.0, source="assumed"),
    )


def _thesis(*, call: Call, scenarios=None, cost_usd=0.5) -> Thesis:
    return Thesis(
        id="oil-test", desk_id="oil", created_at=T0, expires_at=T0, horizon_days=7, call=call,
        scenarios=scenarios or [], usage=Usage(model="fable-5-1", input_tokens=5000,
                                               output_tokens=1200, cost_usd=cost_usd),
    )


# -- pin_temp_state_dir --------------------------------------------------------


def test_pin_temp_state_dir_creates_a_fresh_dir_and_sets_the_env_var():
    """The only function in this module that touches PATHIEL_STATE_DIR -
    main() calls it first, before any live read. Restores whatever the env
    var held before this test ran (conftest.py's autouse fixture already
    pointed it at this test's own tmp_path via monkeypatch), so this test
    leaves no cross-test side effect."""
    before = os.environ.get("PATHIEL_STATE_DIR")
    try:
        path = E.pin_temp_state_dir()
        assert os.environ.get("PATHIEL_STATE_DIR") == path
        assert "pathiel_live_oil_eval_" in path
        assert os.path.isdir(path)
    finally:
        if before is None:
            os.environ.pop("PATHIEL_STATE_DIR", None)
        else:
            os.environ["PATHIEL_STATE_DIR"] = before


# -- target_implied_moves ------------------------------------------------------


def test_target_implied_moves_long():
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=100.0,
                targets=[105.0, 110.0], confidence=0.5, invalidation="i")
    assert E.target_implied_moves(call) == [(105.0, pytest.approx(0.05)), (110.0, pytest.approx(0.10))]


def test_target_implied_moves_short():
    call = Call(side="short", instrument="xyz:BRENTOIL", one_liner="x", entry_px=100.0,
                targets=[95.0, 90.0], confidence=0.5, invalidation="i")
    assert E.target_implied_moves(call) == [(95.0, pytest.approx(0.05)), (90.0, pytest.approx(0.10))]


def test_target_implied_moves_empty_for_flat_or_missing_targets():
    flat = Call(side="flat", instrument="xyz:BRENTOIL", one_liner="x", confidence=0.0, invalidation="i")
    no_targets = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=100.0,
                      confidence=0.5, invalidation="i")
    assert E.target_implied_moves(flat) == []
    assert E.target_implied_moves(no_targets) == []


# -- scenario_calibration_report ----------------------------------------------


def test_scenario_calibration_report_hand_computed():
    analytics = _analytics(final_ret=_q(-0.09, -0.04, 0.01, 0.05, 0.1))
    scenarios = [
        Scenario(name="down", probability=0.3, move_lo_pct=-0.08, move_hi_pct=-0.02, description="d"),
        Scenario(name="chop", probability=0.3, move_lo_pct=-0.02, move_hi_pct=0.02, description="d"),
        Scenario(name="up", probability=0.4, move_lo_pct=0.02, move_hi_pct=0.5, description="d"),
    ]
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                confidence=0.5, invalidation="i")
    thesis = _thesis(call=call, scenarios=scenarios)
    report = E.scenario_calibration_report(thesis, analytics)
    assert report is not None
    assert report["p25"] == pytest.approx(-0.04) and report["p75"] == pytest.approx(0.05)
    # cum after down=0.3, after chop=0.6>=0.5 -> bucket chop, frac=(0.5-0.3)/0.3=0.667,
    # implied = -0.02 + 0.667*0.04 = 0.00667
    assert report["implied_median"] == pytest.approx(0.00667, abs=1e-4)


def test_scenario_calibration_report_none_without_forward_stats():
    analytics = _analytics().model_copy(update={"forward": []})
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                confidence=0.5, invalidation="i")
    thesis = _thesis(call=call, scenarios=[
        Scenario(name="s", probability=1.0, move_lo_pct=0.0, move_hi_pct=0.1, description="d")])
    assert E.scenario_calibration_report(thesis, analytics) is None


def test_scenario_calibration_report_none_without_scenarios():
    analytics = _analytics()
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                confidence=0.5, invalidation="i")
    thesis = _thesis(call=call, scenarios=[])
    assert E.scenario_calibration_report(thesis, analytics) is None


# -- RepairWatcher --------------------------------------------------------------


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(name="services.thesis.api", level=logging.WARNING, pathname="",
                             lineno=0, msg=msg, args=(), exc_info=None)


def test_repair_watcher_flags_the_repairing_warning():
    watcher = E.RepairWatcher()
    watcher.emit(_record("[thesis] oil: draft failed validation, repairing: some error"))
    assert watcher.repaired is True
    assert watcher.messages


def test_repair_watcher_ignores_unrelated_warnings():
    watcher = E.RepairWatcher()
    watcher.emit(_record("[thesis] oil: some other warning entirely"))
    assert watcher.repaired is False


# -- format_report --------------------------------------------------------------


def test_format_report_includes_the_call_all_time_high_and_reach():
    analytics = _analytics(
        all_time_high=PriceExtreme(price=117.58, at=datetime(2026, 3, 9, tzinfo=timezone.utc)),
        long_reach=_q(0.01, 0.02, 0.03, 0.045, 0.06),
    )
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="Long Brent at 5x.",
                entry_px=113.87, stop_px=111.29, targets=[116.5], leverage=5, confidence=0.5,
                invalidation="i")
    scenarios = [Scenario(name="s", probability=1.0, move_lo_pct=0.0, move_hi_pct=0.05, description="d")]
    thesis = _thesis(call=call, scenarios=scenarios)
    report = E.format_report(thesis, analytics, errors=[], repaired=False)
    assert "all-time high: 117.58" in report
    assert "2026-03-09" in report
    assert "empirical 7d reach" in report
    assert "target 116.5 implies" in report
    assert "scenario-implied median" in report
    assert "validator: PASS (0 error(s))" in report
    assert "repair round needed: no" in report
    assert "cost=$0.5000" in report


def test_format_report_lists_validator_errors_and_repair_flag():
    analytics = _analytics()
    call = Call(side="long", instrument="xyz:BRENTOIL", one_liner="x", entry_px=113.87,
                stop_px=111.29, targets=[118.87], leverage=5, confidence=0.5, invalidation="i")
    thesis = _thesis(call=call, scenarios=[])
    report = E.format_report(thesis, analytics, errors=["target 118.87 is beyond the venue's all-time high"],
                             repaired=True)
    assert "validator: FAIL (1 error(s))" in report
    assert "target 118.87 is beyond the venue's all-time high" in report
    assert "repair round needed: yes" in report


def test_format_report_handles_missing_cost():
    analytics = _analytics()
    call = Call(side="flat", instrument="xyz:BRENTOIL", one_liner="x", confidence=0.0, invalidation="i")
    thesis = _thesis(call=call, scenarios=[], cost_usd=None)
    report = E.format_report(thesis, analytics, errors=[], repaired=False)
    assert "cost=n/a" in report
