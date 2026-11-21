"""2026-11-19: the sp500 desk (xyz:SP500). The scheduled events reach it
through the "sp500" coin group: FOMC and CPI block (generated), the US macro
rows written for oil and metals reach it by kind, the jobs report blocks it
(and only it), and a megacap earnings row blocks by its own impact. The
headline watcher has an sp500 group with its own patterns, direction words
and price thresholds. Oil and gold read the calendar exactly as before.
Pure, no network."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from services.intel import headline_watch as HW
from services.intel import scheduled_events as S

SP500, GOLD, BRENT = "xyz:SP500", "xyz:GOLD", "xyz:BRENTOIL"
H = S.HOUR_MS


def ms(*a) -> int:
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


WINDOW = (ms(2026, 11, 19), ms(2027, 1, 31))
NFP_DEC = ms(2026, 12, 4, 13, 30)        # US jobs report (Nov NFP), impact 2 in the data file
FOMC_DEC = ms(2026, 12, 9, 19, 0)
CPI_DEC = ms(2026, 12, 10, 13, 30)
PPI_DEC = ms(2026, 12, 15, 13, 30)


# ── the group ────────────────────────────────────────────────────────────

def test_the_sp500_group():
    assert S.COIN_GROUPS["sp500"] == frozenset({SP500}) == S.SP500_COINS
    assert S.group_of(SP500) == "sp500" and S.group_of(GOLD) == "metals" and S.group_of(BRENT) == "oil"
    assert S.coins_for(["sp500"]) == frozenset({SP500})
    row = {"date": "2027-01-28", "at_utc": "2027-01-28T21:30Z", "event": "Apple Q1 earnings", "kind": "earnings",
           "coins": ["sp500"], "impact": 3, "verified": False, "source": "https://investor.apple.com"}
    S.validate_event(row)
    S.validate_event(dict(row, coins=["oil", "metals", "sp500"]))
    with pytest.raises(ValueError, match="coins must be"):
        S.validate_event(dict(row, coins=["spx"]))


def test_fomc_and_cpi_block_the_sp500():
    for group in S.GENERATED_BLOCKING.values():
        assert group[1][:2] == ("oil",) + (("metals",) if "metals" in group[1] else ())   # oil first, as before
    assert "sp500" in S.GENERATED_BLOCKING["fomc"][1] and "sp500" in S.GENERATED_BLOCKING["cpi"][1]
    assert "sp500" not in S.GENERATED_BLOCKING["opec_meeting"][1]
    for at, name in ((FOMC_DEC, "FOMC decision"), (CPI_DEC, "US CPI release")):
        b = S.blackout(SP500, at)
        assert b and b["event"] == name and b["end_ms"] - b["start_ms"] == 4 * H
        assert b["reason"].startswith("Event blackout: no new entries until ")
        assert S.blackout(SP500, at - 2 * H - 1) is None and S.blackout(SP500, at + 2 * H) is None


# ── macro rows by kind ───────────────────────────────────────────────────

def test_sp500_sees_the_us_macro_rows_and_none_of_the_oil_or_metals_ones():
    rows = S.market_events(*WINDOW, SP500)
    kinds = {r["kind"] for r in rows}
    assert {"nfp", "pce", "ppi", "retail_sales", "fomc_minutes", "fomc", "cpi", "us_holiday", "early_close",
            "funding_deadline", "trade_deadline", "congress", "election"} <= kinds
    assert not kinds & {"opec_meeting", "opec_momr", "iea_omr", "china_data", "iran_talks", "contract_expiry",
                        "peace_talks", "iaea"}
    assert kinds <= S.SP500_MACRO_KINDS | {"fomc", "cpi"}


def test_the_jobs_report_blocks_the_sp500_and_only_the_sp500():
    b = S.blackout(SP500, NFP_DEC)
    assert b and b["kind"] == "nfp" and (b["start_ms"], b["end_ms"]) == (NFP_DEC - 2 * H, NFP_DEC + 2 * H)
    assert "US jobs report" in b["reason"]
    assert S.blackout(SP500, NFP_DEC - 2 * H - 1) is None and S.blackout(SP500, NFP_DEC + 2 * H) is None
    assert S.blackout(GOLD, NFP_DEC) is None and S.blackout(BRENT, NFP_DEC) is None
    sp = next(r for r in S.market_events(*WINDOW, SP500) if r["at_ms"] == NFP_DEC)
    gold = next(r for r in S.market_events(*WINDOW, GOLD) if r["at_ms"] == NFP_DEC)
    anyone = next(r for r in S.market_events(*WINDOW) if r["at_ms"] == NFP_DEC)
    assert sp["impact"] == 3 and sp["window"] and gold["impact"] == anyone["impact"] == 2
    assert gold["window"] is None and anyone["window"] is None
    assert "[blackout]" in S.short_line(sp) and "[blackout]" not in S.short_line(gold)


def test_an_impact_2_print_does_not_block():
    assert S.blackout(SP500, PPI_DEC) is None
    assert S.SP500_BLOCKING_KINDS == frozenset({"nfp"})


def test_a_megacap_earnings_row_blocks_the_sp500_by_its_own_impact(tmp_path):
    at = "2027-01-28T21:30Z"
    data = {"refreshed": "2026-11-19", "valid_through": "2027-03-31", "how": "test", "events": [
        {"date": "2027-01-28", "at_utc": at, "event": "Apple Q1 earnings (after the close)", "kind": "earnings",
         "coins": ["sp500"], "impact": 3, "verified": False, "source": "https://investor.apple.com"},
        {"date": "2027-01-29", "at_utc": "2027-01-29T21:30Z", "event": "A small cap reports", "kind": "earnings",
         "coins": ["sp500"], "impact": 1, "verified": False, "source": "https://example.com"}]}
    path = tmp_path / "events.json"
    path.write_text(json.dumps(data))
    t = ms(2027, 1, 28, 21, 30)
    b = S.blackout(SP500, t, str(path))
    assert b and b["kind"] == "earnings" and "Apple" in b["reason"]
    assert S.blackout(GOLD, t, str(path)) is None and S.blackout(BRENT, t, str(path)) is None
    assert S.blackout(SP500, ms(2027, 1, 29, 21, 30), str(path)) is None        # impact 1: a catalyst, no block


# ── oil and gold read the calendar as before ─────────────────────────────

def test_oil_and_gold_rows_are_the_ones_the_file_names_for_them():
    """A row reaches Brent or gold only through its own `coins`, never
    through a kind, and with the file's own impact."""
    by_key = {(e["date"], e["kind"], e["event"]): e for e in S.load()["events"]}
    for coin, groups in ((BRENT, {"oil"}), (GOLD, {"metals", "silver"})):
        for r in S.market_events(*WINDOW, coin):
            if r["origin"] != S.CURATED_ORIGIN:
                continue
            day = datetime.fromtimestamp(r["at_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            src = next(e for (d, k, t), e in by_key.items() if k == r["kind"] and t == r["title"]
                       and (d == day or e.get("at_utc", "") and e["at_utc"][:10] == day))
            assert set(src["coins"]) & groups, r["title"]
            assert r["impact"] == src["impact"], r["title"]


def test_the_event_blackout_for_sp500_has_no_eia_window_and_no_weekend_flat():
    from services.strategies import event_blackout as EB
    assert EB.eia_blackout(SP500, ms(2026, 12, 2, 15, 30)) is None           # a Wednesday EIA print
    assert EB.weekend_blackout(SP500, ms(2026, 12, 5, 12)) is None            # a Saturday
    assert EB.next_weekend(SP500, ms(2026, 12, 5, 12)) is None
    got = EB.event_blackout(SP500, NFP_DEC, side="long")
    assert got and got["kind"] == "nfp"
    assert EB.event_blackout(SP500, ms(2026, 12, 5, 12), side="long") is None
    titles = [r["title"] for r in EB.upcoming_events(SP500, 72.0, ms(2026, 12, 8, 12))]
    assert titles[:2] == ["FOMC decision", "US CPI release"] and len(titles) == 3      # plus the funding deadline


def test_the_sp500_thesis_gets_the_macro_rows_as_catalysts():
    from services.intel.sources import catalysts
    from services.strategies.desks import get_spec
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    cats = catalysts.calendar_catalysts(get_spec("sp500"), now)
    kinds = {c.kind for c in cats}
    assert {"fomc", "cpi", "nfp", "pce", "us_holiday"} <= kinds
    assert not kinds & {"eia_weekly", "opec_meeting", "opec_momr", "china_data"}
    assert catalysts.active_kinds(get_spec("sp500")) == {"fomc", "cpi"}       # no hurricane, unlock or SEC kinds


# ── the headline watcher ─────────────────────────────────────────────────

def test_price_thresholds():
    assert HW.PRICE_SHOCK[SP500] == 0.007 and HW.PRICE_DRIFT[SP500] == 0.0075
    assert HW.PRICE_SHOCK[BRENT] == 0.010 and HW.PRICE_DRIFT[BRENT] == 0.0175      # unchanged
    assert HW.PRICE_SHOCK[GOLD] == 0.007 and HW.PRICE_DRIFT[GOLD] == 0.009
    assert len(HW.NEWS_QUERIES["sp500"]) == 3 and all(q.endswith("when:1h") for q in HW.NEWS_QUERIES["sp500"])


@pytest.mark.parametrize("title,label,direction", [
    ("Fed delivers surprise emergency rate cut", "fed", 1),
    ("Powell signals no rate cut in December", "fed", -1),
    ("Trump says he may fire Powell", "trump/fed", 0),
    ("Trump announces sweeping new tariffs on China", "tariffs", -1),
    ("White House pauses tariffs on Mexico for 90 days", "tariffs", 1),
    ("Supreme Court strikes down Trump tariffs", "tariffs", 1),
    ("Nvidia cuts revenue forecast on China export curbs", "megacap", -1),
    ("Apple beats estimates and raises guidance", "megacap", 1),
    ("Microsoft earnings miss expectations as cloud growth slows", "megacap", -1),
    ("Government shutdown begins after Senate vote fails", "washington", -1),
    ("Senate reaches deal to avert government shutdown", "washington", 1),
    ("Moody's downgrades U.S. credit rating", "washington", -1),
    ("NYSE trading halted after circuit breaker trips", "market stress", -1),
    ("Treasury yields surge after hot jobs data", "yields shock", -1),
])
def test_sp500_headlines_match_and_read_their_direction(title, label, direction):
    assert HW.match_headline("sp500", title) == label
    assert HW.text_direction("sp500", title) == direction


@pytest.mark.parametrize("title", [
    "Stocks fall as yields jump",                       # a market wrap: the price shock catches the move
    "Wall Street slides on tariff worries",
    "S&P 500 closes at record high",
    "Nvidia unveils new chip at conference",            # a megacap with no guidance word
    "Tariffs on steel explained: what you need to know",
    "OPEC+ agrees surprise output cut",                 # the oil desk's headline, not this one's
])
def test_routine_headlines_do_not_alert(title):
    assert HW.match_headline("sp500", title) is None


def test_risk_on_and_risk_off_read_the_right_way_round_for_stocks():
    assert HW.text_direction("sp500", "US and China reach trade deal") == 1
    assert HW.text_direction("sp500", "Trade talks collapse between US and China") == -1
    assert HW.text_direction("sp500", "Israel strikes Iran nuclear sites") == -1
    assert HW.text_direction("sp500", "Iran and US agree ceasefire") == 1
    # the same headline is the other way round for gold (risk-off is its bid)
    assert HW.text_direction("metals", "Israel strikes Iran nuclear sites") == 1
    assert HW.text_direction("sp500", "Fed cuts rates as attack escalates") == 0     # both sides: unknown, holds


def test_trump_posts():
    assert HW.match_post("sp500", "The Fed should cut interest rates NOW. Too Late Powell!") == "trump:fed"
    assert HW.match_post("sp500", "Big tariffs on China coming, 100%!") == "trump:tariff"
    assert HW.match_post("sp500", "The Stock Market just hit an ALL TIME HIGH!") == "trump:markets"
    assert HW.match_post("sp500", "My Complete and Total Endorsement of Tracey Powell for State Senate") is None
    assert HW.match_post("sp500", "Great rally in Ohio") is None


@pytest.mark.parametrize("question,polarity", [
    ("Fed rate cut in December?", 1), ("Will the Fed hike rates in 2027?", -1), ("US recession in 2027?", -1),
    ("Government shutdown by January 31?", -1), ("Will the S&P 500 close above $7500 in 2026?", 1),
    ("US x China trade deal by March?", 1), ("Will Trump impose new tariffs on the EU?", -1),
])
def test_odds_markets(question, polarity):
    assert HW.ODDS_TERMS["sp500"].search(question)
    assert HW.odds_polarity("sp500", question) == polarity
    assert HW.odds_direction("sp500", question, +0.1) == polarity
    assert HW.odds_direction("sp500", question, -0.1) == -polarity


def test_a_bullish_alert_does_not_hold_a_long_and_holds_a_short(tmp_path):
    path = str(tmp_path / "alerts.json")
    at = ms(2026, 11, 20, 15, 0)
    alert = {"at_ms": at, "group": "sp500", "kind": "news", "label": "fed",
             "title": "Fed delivers surprise emergency rate cut", "direction": 1}
    with open(path, "w") as fh:
        json.dump([alert], fh)
    assert HW.headline_blackout(SP500, at + 60_000, path, side="long") is None
    held = HW.headline_blackout(SP500, at + 60_000, path, side="short")
    assert held and "Headline blackout" in held["reason"]
    assert HW.headline_blackout(SP500, at + 31 * 60_000, path, side="short") is None
    assert HW.headline_blackout(BRENT, at + 60_000, path, side="short") is None       # not oil's alert
