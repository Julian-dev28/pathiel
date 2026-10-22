"""Offline gate test for the trade-grading replay eval
(services/strategies/evals/trades_replay.py). The eval itself only reads two
checked-in fixtures (no network, no live state) so this exercises the exact
same `replay()` path the periodic eval run does -- there is no separate
"live" variant to leave for a nightly run."""
from __future__ import annotations

import pytest

from services.strategies.evals import trades_replay as R


def test_fixtures_load_and_parse():
    thesis = R.load_thesis()
    assert thesis.desk_id == "oil"
    assert thesis.call.instrument == "xyz:BRENTOIL"
    fills = R.load_fills()
    assert fills["coin"] == "xyz:BRENTOIL"
    assert len(fills["fills"]) == 40


def test_replay_matches_the_hand_verified_totals():
    """Hand-verified against the raw (untrimmed) userFillsByTime response
    for the watch wallet, captured 2026-10-23: 11 Open Long, 13 Open Short,
    8 Close Long, 8 Close Short; sum(closedPnl) = -203.507038, sum(fee) =
    5.274728."""
    report = R.replay()
    g = report["grade"]
    assert g["n_fills"] == 40
    assert g["n_opens"] == 24 and g["n_closes"] == 16
    assert g["realized_pnl_usd"] == pytest.approx(-208.781766, abs=1e-4)
    assert g["follow_rate"] == pytest.approx(11 / 24, abs=1e-4)
    assert g["entered_in_zone"] is True
    assert report["passed"] is True


def test_main_prints_a_verdict(capsys):
    rc = R.main([])
    out = capsys.readouterr().out
    assert "VERDICT: PASS" in out
    assert rc == 0


def test_main_json_mode_is_valid_json(capsys):
    import json
    rc = R.main(["--json"])
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["passed"] is True
    assert rc == 0


def test_zero_fills_fails_the_found_trades_check():
    """A thesis window with no overlap to the fixture's fills (the mechanism
    broke, not "nothing traded") must fail, not silently pass."""
    thesis = R.load_thesis()
    outside = thesis.model_copy(update={
        "created_at": thesis.created_at.replace(year=2020),
        "expires_at": thesis.expires_at.replace(year=2020),
    })
    report = R.replay(thesis=outside)
    assert report["grade"]["n_fills"] == 0
    assert report["checks"]["found_trades"] is False
    assert report["passed"] is False
