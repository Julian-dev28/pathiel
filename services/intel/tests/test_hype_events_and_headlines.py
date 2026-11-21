"""2026-11-20: the candidate hype desk (HYPE). The scheduled events reach it
through the "hype" coin group: FOMC and CPI block (generated), same as every
other desk; token_unlock turns on automatically for any crypto-ticker desk
(desk_rules.is_crypto_desk). No macro-kind mapping like the S&P's: HYPE's own
calendar is idiosyncratic, not the US jobs/inflation print calendar. The
headline watcher has a hype group with its own patterns, direction words and
price thresholds measured on HYPE's own candles. Oil, gold and the S&P read
the calendar exactly as before. Pure, no network."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.intel import headline_watch as HW
from services.intel import scheduled_events as S

HYPE, SP500, GOLD, BRENT = "HYPE", "xyz:SP500", "xyz:GOLD", "xyz:BRENTOIL"
H = S.HOUR_MS


def ms(*a) -> int:
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


FOMC_DEC = ms(2026, 12, 9, 19, 0)
CPI_DEC = ms(2026, 12, 10, 13, 30)


# ── the group ────────────────────────────────────────────────────────────

def test_the_hype_group():
    assert S.COIN_GROUPS["hype"] == frozenset({HYPE}) == S.HYPE_COINS
    assert S.group_of(HYPE) == "hype" and S.group_of(GOLD) == "metals" and S.group_of(BRENT) == "oil"
    assert S.coins_for(["hype"]) == frozenset({HYPE})
    row = {"date": "2027-01-28", "at_utc": "2027-01-28T21:30Z", "event": "A Hyperliquid governance vote",
           "kind": "congress", "coins": ["hype"], "impact": 2, "verified": False,
           "source": "https://hyperliquid.xyz"}
    S.validate_event(row)
    S.validate_event(dict(row, coins=["oil", "metals", "hype"]))
    with pytest.raises(ValueError, match="coins must be"):
        S.validate_event(dict(row, coins=["hyp"]))


def test_fomc_and_cpi_block_hype_too():
    assert "hype" in S.GENERATED_BLOCKING["fomc"][1] and "hype" in S.GENERATED_BLOCKING["cpi"][1]
    assert "hype" not in S.GENERATED_BLOCKING["opec_meeting"][1]
    for at, name in ((FOMC_DEC, "FOMC decision"), (CPI_DEC, "US CPI release")):
        b = S.blackout(HYPE, at)
        assert b and b["event"] == name and b["end_ms"] - b["start_ms"] == 4 * H
        assert b["reason"].startswith("Event blackout: no new entries until ")
        assert S.blackout(HYPE, at - 2 * H - 1) is None and S.blackout(HYPE, at + 2 * H) is None


def test_hype_has_no_macro_kind_mapping_like_the_sp500():
    """Unlike SP500_MACRO_KINDS, no kind reaches hype just by being written
    for oil or metals -- only a row that names "hype" directly, or the
    generated FOMC/CPI rows."""
    assert not hasattr(S, "HYPE_MACRO_KINDS")
    assert not hasattr(S, "HYPE_BLOCKING_KINDS")


def test_the_event_blackout_for_hype_has_no_eia_window_and_no_weekend_flat():
    from services.strategies import event_blackout as EB
    from services.strategies.weekend import OIL_COINS
    assert HYPE not in OIL_COINS
    assert EB.eia_blackout(HYPE, ms(2026, 12, 2, 15, 30)) is None           # a Wednesday EIA print
    assert EB.weekend_blackout(HYPE, ms(2026, 12, 5, 12)) is None            # a Saturday
    assert EB.next_weekend(HYPE, ms(2026, 12, 5, 12)) is None
    got = EB.event_blackout(HYPE, FOMC_DEC, side="long")
    assert got and got["kind"] == "fomc"


def test_sp500_oil_and_gold_rows_are_unaffected_by_the_hype_group():
    assert S.COIN_GROUPS["sp500"] == frozenset({SP500})
    assert S.COIN_GROUPS["metals"] == S.METALS_COINS
    assert S.COIN_GROUPS["oil"] == frozenset({BRENT, "xyz:CL"})


def test_the_hype_thesis_gets_token_unlock_and_fomc_cpi_as_catalysts():
    from services.intel.sources import catalysts
    from services.strategies.desks import get_spec
    spec = get_spec("hype")
    assert catalysts.active_kinds(spec) == {"fomc", "cpi", "token_unlock"}
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    cats = catalysts.calendar_catalysts(spec, now)
    kinds = {c.kind for c in cats}
    assert {"fomc", "cpi"} <= kinds
    assert not kinds & {"eia_weekly", "opec_meeting", "earnings", "nfp"}


# ── the headline watcher ─────────────────────────────────────────────────

def test_price_thresholds_are_measured_on_hypes_own_candles_not_borrowed():
    assert HW.PRICE_SHOCK[HYPE] == 0.028 and HW.PRICE_DRIFT[HYPE] == 0.035
    assert HW.PRICE_SHOCK[HYPE] > HW.PRICE_SHOCK[SP500] == HW.PRICE_SHOCK[GOLD]    # HYPE is the loudest coin
    assert HW.PRICE_SHOCK[BRENT] == 0.010 and HW.PRICE_DRIFT[BRENT] == 0.0175      # unchanged
    assert len(HW.NEWS_QUERIES["hype"]) == 3 and all(q.endswith("when:1h") for q in HW.NEWS_QUERIES["hype"])


@pytest.mark.parametrize("title,label,direction", [
    ("Hyperliquid announces token buyback program", "protocol", 1),
    ("Hyperliquid suffers $10M bridge exploit", "exploit", -1),
    ("Hyperliquid HLP vault pauses after outage", "protocol", 0),
    ("HYPE token unlock sparks selling", "unlock", -1),
    ("SEC sues major crypto exchange over unregistered securities", "regulation", -1),
    ("Bitcoin ETF sees record inflows", "btc shock", 1),
    ("Bitcoin ETF outflows accelerate as crypto selloff deepens", "btc shock", -1),
    ("Binance launches new perpetual futures contract", "competition", 0),
])
def test_hype_headlines_match_and_read_their_direction(title, label, direction):
    assert HW.match_headline("hype", title) == label
    assert HW.text_direction("hype", title) == direction


@pytest.mark.parametrize("title", [
    "Hyperliquid price rises as volume grows",         # a price wrap: the price shock catches the move
    "Bitcoin falls as risk appetite fades",
    "HYPE token hits new all-time high",
    "Perp DEX volumes surge across the sector",         # no action word next to a named exchange
])
def test_routine_headlines_do_not_alert(title):
    assert HW.match_headline("hype", title) is None


def test_odds_markets():
    assert HW.ODDS_TERMS["hype"].search("Will the SEC approve a bitcoin ETF?")
    assert HW.odds_polarity("hype", "Will there be a major crypto exchange hack in 2027?") == -1
    assert HW.odds_polarity("hype", "Will the SEC approve a new crypto ETF?") == 1
    assert HW.odds_direction("hype", "Will bitcoin close above $100,000?", +0.1) == 1
    assert HW.odds_direction("hype", "Will bitcoin close above $100,000?", -0.1) == -1


def test_a_bullish_alert_does_not_hold_a_long_and_holds_a_short(tmp_path):
    import json
    path = str(tmp_path / "alerts.json")
    at = ms(2026, 11, 20, 15, 0)
    alert = {"at_ms": at, "group": "hype", "kind": "news", "label": "protocol",
             "title": "Hyperliquid announces token buyback program", "direction": 1}
    with open(path, "w") as fh:
        json.dump([alert], fh)
    assert HW.headline_blackout(HYPE, at + 60_000, path, side="long") is None
    held = HW.headline_blackout(HYPE, at + 60_000, path, side="short")
    assert held and "Headline blackout" in held["reason"]
    assert HW.headline_blackout(HYPE, at + 31 * 60_000, path, side="short") is None
    assert HW.headline_blackout(SP500, at + 60_000, path, side="short") is None       # not hype's alert
