"""Expected-ROI bracket: the EV math, the always-negative market case, the
breakeven share, and the annualisation. Pure functions on synthetic ledger rows."""
from __future__ import annotations

import time

import pytest

from services.polymarket_scout import roi
from services.polymarket_scout.scout import FEE_PER_FILL

NOW_S = time.time()


def _row(**over):
    r = {"market_id": "1", "side": "YES", "llm_yes": 0.80, "mkt_yes": 0.40,
         "fill_px": 0.42, "edge": 0.40, "lane": "trending", "resolved": False,
         "ts": int(NOW_S * 1000),
         "end_date": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                   time.gmtime(NOW_S + 10 * 86400))}
    r.update(over)
    return r


# ── EV ───────────────────────────────────────────────────────────────────────
def test_ev_at_a_fair_coin_is_exactly_the_fee_drag():
    # 50c fill, 50% chance: gross EV is zero, so what is left is the fees
    ev = roi.ev_per_dollar(0.5, 0.50)
    assert ev == pytest.approx(-(FEE_PER_FILL + 0.5 * FEE_PER_FILL), abs=1e-9)
    assert ev < 0


def test_ev_rises_with_our_probability():
    assert roi.ev_per_dollar(0.9, 0.40) > roi.ev_per_dollar(0.6, 0.40) > roi.ev_per_dollar(0.3, 0.40)


def test_ev_is_clamped_to_a_probability():
    assert roi.ev_per_dollar(5.0, 0.40) == roi.ev_per_dollar(1.0, 0.40)
    assert roi.ev_per_dollar(-2.0, 0.40) == roi.ev_per_dollar(0.0, 0.40)


# ── which side wins ──────────────────────────────────────────────────────────
def test_win_probability_flips_with_the_side():
    yes, no = _row(side="YES"), _row(side="NO")
    assert roi.our_win_prob(yes) == 0.80 and roi.our_win_prob(no) == pytest.approx(0.20)
    assert roi.market_win_prob(yes) == 0.40 and roi.market_win_prob(no) == pytest.approx(0.60)


# ── the floor is structural ──────────────────────────────────────────────────
@pytest.mark.parametrize("side,mkt,fill", [
    ("YES", 0.10, 0.11), ("YES", 0.50, 0.51), ("YES", 0.90, 0.91),
    ("NO", 0.10, 0.90), ("NO", 0.75, 0.26),
])
def test_market_case_is_negative_for_every_row(side, mkt, fill):
    """If the price is right, we are paying fees to hold a coin flip. A market
    case that ever prints positive means the fee model got dropped."""
    s = roi.summarise([_row(side=side, mkt_yes=mkt, fill_px=fill)])
    assert s["market_case_roi_per_position"] < 0


def test_our_case_beats_the_market_case_whenever_we_disagree_in_our_favour():
    s = roi.summarise([_row(llm_yes=0.80, mkt_yes=0.40, fill_px=0.42)])
    assert s["our_case_roi_per_position"] > s["market_case_roi_per_position"]


def test_a_forecast_that_agrees_with_the_market_earns_the_fee_loss_only():
    s = roi.summarise([_row(llm_yes=0.42, mkt_yes=0.42, fill_px=0.42)])
    assert s["our_case_roi_per_position"] == pytest.approx(
        s["market_case_roi_per_position"], abs=1e-9)
    assert s["breakeven_share_of_edge"] is None      # no gap to be right about


# ── breakeven ────────────────────────────────────────────────────────────────
def test_breakeven_is_the_share_of_the_gap_that_must_be_real():
    assert roi.breakeven_calibration(0.30, -0.03) == pytest.approx(0.0909, abs=1e-3)
    assert roi.breakeven_calibration(0.03, -0.03) == pytest.approx(0.5, abs=1e-9)


def test_breakeven_above_one_means_it_cannot_clear_zero():
    be = roi.breakeven_calibration(-0.01, -0.05)     # even our case loses
    assert be is not None and be > 1.0


def test_breakeven_is_none_when_our_case_is_no_better():
    assert roi.breakeven_calibration(-0.05, -0.01) is None


# ── holding period ───────────────────────────────────────────────────────────
def test_days_to_resolution_measured_from_the_signal_not_from_now():
    r = _row(ts=int((NOW_S - 5 * 86400) * 1000))     # signalled 5 days ago
    assert roi.days_to_resolution(r, NOW_S) == pytest.approx(15.0, abs=0.1)


def test_days_to_resolution_is_floored_so_annualisation_stays_sane():
    r = _row(end_date=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW_S + 600)))
    assert roi.days_to_resolution(r, NOW_S) == roi.MIN_DAYS


def test_days_to_resolution_of_a_junk_date_is_none():
    assert roi.days_to_resolution(_row(end_date="soon")) is None


def test_annualisation_scales_with_turnover():
    fast = roi.summarise([_row(end_date=time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW_S + 5 * 86400)))], NOW_S)
    slow = roi.summarise([_row(end_date=time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW_S + 100 * 86400)))], NOW_S)
    assert fast["our_case_roi_per_position"] == slow["our_case_roi_per_position"]
    assert fast["our_case_annualised"] > slow["our_case_annualised"]


# ── report ───────────────────────────────────────────────────────────────────
def test_report_splits_by_lane_and_excludes_resolved_rows():
    rows = [_row(lane="trending"), _row(lane="judgment"),
            _row(lane="trending", resolved=True)]
    rep = roi.report(rows, NOW_S)
    assert rep["open"] == 2 and rep["resolved"] == 1
    assert rep["by_lane"]["trending"]["n"] == 1
    assert rep["by_lane"]["judgment"]["n"] == 1
    assert rep["by_lane"]["sports"] == {"n": 0}


def test_report_on_an_empty_ledger_does_not_divide_by_zero():
    rep = roi.report([], NOW_S)
    assert rep["open"] == 0 and rep["book"] == {"n": 0}
    assert "conditional" in roi._fmt(rep) or "REALISED ROI: none" in roi._fmt(rep)


def test_report_says_out_loud_that_nothing_is_realised_yet():
    out = roi._fmt(roi.report([_row()], NOW_S))
    assert "REALISED ROI: none" in out
    assert "0 RESOLVED" in out


def test_report_stops_saying_that_once_something_resolves():
    out = roi._fmt(roi.report([_row(resolved=True)], NOW_S))
    assert "REALISED ROI: none" not in out


# ── concentration ────────────────────────────────────────────────────────────
def test_concentration_counts_distinct_events_not_rows():
    """Two rows on one ceasefire event are one bet. The gate's n assumes
    independent draws, so this is the number that says how much of n is real."""
    rows = [_row(meta={"event_title": "Israel x Iran ceasefire"}, category="iran"),
            _row(meta={"event_title": "Israel x Iran ceasefire"}, category="iran"),
            _row(meta={"event_title": "Fed Decision"}, category="economy")]
    c = roi.concentration(rows)
    assert c["n"] == 3 and c["effective_n"] == 2
    assert c["independence"] == pytest.approx(2 / 3, abs=1e-3)
    assert c["top_theme"] == "iran" and c["top_theme_share"] == pytest.approx(2 / 3, abs=1e-3)


def test_untagged_is_reported_separately_not_as_the_top_theme():
    """'untagged' is a recording gap, not a theme — naming it as the top theme
    would hide the real concentration underneath it."""
    rows = [_row(category=""), _row(category=""), _row(category="iran")]
    c = roi.concentration(rows)
    assert c["top_theme"] == "iran"
    assert c["untagged_share"] == pytest.approx(2 / 3, abs=1e-3)


def test_concentration_of_nothing_is_empty():
    assert roi.concentration([]) == {"n": 0}


# ── mark to market ───────────────────────────────────────────────────────────
def test_mtm_values_a_yes_position_at_the_current_price():
    m = roi.mark_to_market([_row(side="YES", fill_px=0.26)], {"1": 0.90})
    assert m["mean_mtm_pct"] == pytest.approx((0.90 - 0.26) / 0.26, abs=1e-3)
    assert m["winners"] == 1


def test_mtm_values_a_no_position_off_the_complement():
    m = roi.mark_to_market([_row(side="NO", fill_px=0.15)], {"1": 0.88})
    # the NO leg is worth 1-0.88 = 0.12 against a 0.15 fill
    assert m["mean_mtm_pct"] == pytest.approx((0.12 - 0.15) / 0.15, abs=1e-3)
    assert m["winners"] == 0


def test_mtm_reports_a_median_so_one_winner_cannot_carry_the_mean():
    rows = [_row(market_id=str(i), side="YES", fill_px=0.50) for i in range(5)]
    prices = {"0": 0.99, "1": 0.48, "2": 0.47, "3": 0.49, "4": 0.48}
    m = roi.mark_to_market(rows, prices)
    assert m["mean_mtm_pct"] > 0          # the one winner drags the mean up
    assert m["median_mtm_pct"] < 0        # the book is actually down
    assert m["winners"] == 1


def test_mtm_skips_positions_the_board_no_longer_quotes():
    m = roi.mark_to_market([_row(market_id="1"), _row(market_id="gone")], {"1": 0.5})
    assert m["n"] == 1                    # the delisted one is skipped, not guessed


def test_mtm_with_no_quotes_is_empty():
    assert roi.mark_to_market([_row()], {}) == {"n": 0}


def test_report_labels_mtm_as_unrealised_and_never_as_return():
    out = roi._fmt(roi.report([_row(side="YES", fill_px=0.26)], NOW_S, prices={"1": 0.9}))
    assert "MARK-TO-MARKET (unrealised" in out
    assert "only resolution pays" in out
    assert "REALISED ROI: none" in out
