"""Hormuz transit-count markets (2026-11-09: a low bucket rising held a long)."""
from services.intel import headline_watch as HW

Q = "Will there be between {} and {} average daily transits of the Strait of Hormuz on November 30?"


def test_low_bucket_rising_is_bullish_oil_and_no_longer_holds_a_long():
    q = Q.format(0, 4)
    assert HW.odds_polarity("oil", q) == 1
    assert HW.odds_direction("oil", q, 0.10) == 1        # 51% -> 61%


def test_high_bucket_is_bearish_and_middle_is_unknown():
    assert HW.odds_polarity("oil", Q.format(80, 100)) == -1
    assert HW.odds_polarity("oil", Q.format(30, 40)) == 0
    assert HW.transit_polarity("Will Iran close the Strait of Hormuz?") == 0


def test_the_real_alert_row_reads_bullish():
    row = {"kind": "odds", "group": "oil",
           "title": Q.format(0, 4) + ": 51% -> 61%"}
    assert HW.alert_direction(row) == 1
