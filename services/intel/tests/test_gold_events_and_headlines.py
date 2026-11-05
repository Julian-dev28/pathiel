"""2026-11-05: gold (xyz:GOLD) is the metals desk's primary. The scheduled
events reach it through the "metals" coin group ("silver" stays valid as the
data file's old name for the same group), the impact-3 blackout covers it,
and the headline watcher has a metals group with gold's own patterns and a
0.7% price shock. Pure, no network."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.intel import headline_watch as HW
from services.intel import scheduled_events as S

GOLD, SILVER, BRENT = "xyz:GOLD", "xyz:SILVER", "xyz:BRENTOIL"
MIN = 60_000
NOW_MS = int(datetime(2026, 11, 5, 12, tzinfo=timezone.utc).timestamp() * 1000)


def ms(*a) -> int:
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


# ── the metals group and its alias ──────────────────────────────────────────

def test_metals_group_covers_silver_and_gold_and_silver_is_its_alias():
    assert S.COIN_GROUPS["metals"] == frozenset({SILVER, GOLD}) == S.METALS_COINS
    assert "silver" not in S.COIN_GROUPS and S.GROUP_ALIASES == {"silver": "metals"}
    assert S.canonical_group("silver") == "metals" and S.canonical_group("oil") == "oil"
    assert S.coins_for(["silver"]) == S.coins_for(["metals"]) == frozenset({SILVER, GOLD})
    assert S.coins_for(["oil", "silver"]) == frozenset({BRENT, "xyz:CL", SILVER, GOLD})
    assert S.group_of(GOLD) == "metals" == S.group_of(SILVER) and S.group_of(BRENT) == "oil"
    assert S.group_of("BTC") is None


def test_validate_event_accepts_metals_and_the_silver_alias_and_rejects_gold_as_a_group():
    row = {"date": "2026-11-20", "at_utc": None, "event": "x", "kind": "k", "coins": ["metals"], "impact": 1,
           "verified": True, "source": "s"}
    S.validate_event(row)
    S.validate_event(dict(row, coins=["silver"]))
    S.validate_event(dict(row, coins=["oil", "metals"]))
    with pytest.raises(ValueError, match="coins must be"):
        S.validate_event(dict(row, coins=["gold"]))


def test_every_data_file_row_resolves_to_real_coins():
    for e in S.load()["events"]:
        assert S.coins_for(e["coins"]), e["event"]


# ── gold gets the macro rows and the blackout ───────────────────────────────

def test_gold_sees_the_macro_rows_silver_had():
    rows = S.market_events(ms(2026, 11, 5), ms(2027, 1, 31), GOLD)
    kinds = {r["kind"] for r in rows}
    assert {"nfp", "pce", "fomc_minutes", "china_data", "summit", "iran_talks", "ppi", "fomc", "cpi",
            "election", "funding_deadline"} <= kinds
    assert "opec_meeting" not in kinds and "opec_momr" not in kinds and "iea_omr" not in kinds
    silver_rows = S.market_events(ms(2026, 11, 5), ms(2027, 1, 31), SILVER)
    assert [r["title"] for r in rows] == [r["title"] for r in silver_rows]     # one group, same calendar


@pytest.mark.parametrize("at,kind,until", [
    (ms(2026, 11, 10, 13, 0), "cpi", "2026-11-10 15:30Z"),          # CPI 08:30 ET, 2h either side
    (ms(2026, 12, 9, 20, 0), "fomc", "2026-12-09 21:00Z"),          # FOMC 14:00 EST
    (ms(2026, 11, 7, 12, 0), "iran_talks", "2026-11-08 00:00Z"),    # Iran's reply to the US draft: all day, impact 3
    (ms(2026, 11, 3, 13, 53), "election", "2026-11-04 18:00Z"),     # the midterms window
])
def test_gold_impact_3_blackouts(at, kind, until):
    b = S.blackout(GOLD, at)
    assert b and b["kind"] == kind and b["until_utc"] == until and b["coin"] == GOLD
    assert b["reason"].startswith("Event blackout: no new entries until")


@pytest.mark.parametrize("at", [
    ms(2026, 11, 29, 12, 0),      # OPEC+ meeting: oil only
    ms(2026, 11, 6, 13, 30),      # jobs report: impact 2, shown, not blocking
    ms(2026, 11, 9, 1, 30),       # China CPI: impact 1
])
def test_gold_not_blocked_by_oil_only_or_low_impact_rows(at):
    assert S.blackout(GOLD, at) is None


def test_event_blackout_covers_gold_and_the_eia_window_never_does():
    from services.strategies.event_blackout import event_blackout, upcoming_events
    cpi = event_blackout(GOLD, ms(2026, 11, 10, 13, 0))
    assert cpi and cpi["reason"].startswith("Event blackout") and "CPI" in cpi["reason"]
    assert event_blackout(GOLD, ms(2026, 11, 12, 16, 0)) is None          # Thursday EIA: oil only
    lines = [S.short_line(r) for r in upcoming_events(GOLD, 72.0, at_ms=ms(2026, 11, 5, 11, 0))]
    assert lines[0] == "Fri 11-06 13:30Z US jobs report (Oct NFP)"
    assert lines[1].startswith("Sat 11-07 Iran's reply") and "[blackout]" in lines[1]


def test_the_gold_thesis_gets_the_scheduled_events_as_catalysts():
    from services.intel.sources.catalysts import calendar_catalysts
    from services.strategies.desks import get_spec
    now = datetime(2026, 11, 5, 11, tzinfo=timezone.utc)
    cats = calendar_catalysts(get_spec("metals"), now)
    kinds = {c.kind for c in cats}
    assert {"nfp", "china_data", "iran_talks", "cpi", "cftc_cot"} <= kinds   # no FOMC inside this window
    assert "opec_momr" not in kinds and "eia_weekly" not in kinds
    iran = next(c for c in cats if c.kind == "iran_talks")
    assert iran.importance == 3


# ── the headline watcher's metals group ─────────────────────────────────────

def test_gold_price_shock_is_0_7pct_and_the_others_are_unchanged():
    assert HW.PRICE_SHOCK == {BRENT: 0.010, "xyz:CL": 0.010, SILVER: 0.012, GOLD: 0.007}
    h = [(NOW_MS - (14 - i) * MIN, 4100.0) for i in range(14)]
    assert HW.price_shock(GOLD, h, NOW_MS, 4100.0 * (1 - 0.0065)) is None
    shock = HW.price_shock(GOLD, h, NOW_MS, 4100.0 * (1 - 0.0075))
    assert shock and shock["group"] == "metals" and shock["coin"] == GOLD and "-0.75%" in shock["title"]


@pytest.mark.parametrize("title,label", [
    ("Powell announces emergency rate cut", "fed"),
    ("Fed surprises with a 50bp hike", "fed"),
    ("PBOC adds gold to reserves for a 13th month", "central bank gold"),
    ("Poland's central bank buys 20 tonnes of gold", "central bank gold"),
    ("Trump imposes tariff on gold and copper imports", "tariff/metals"),
    ("Iran, US reach deal to reopen Strait of Hormuz", "iran/hormuz"),
    ("US strikes Iranian missile sites", "iran/hormuz"),
    ("Treasury yields spike after hot jobs print", "yields/dollar shock"),
    ("Trump says he will fire Powell", "trump/fed"),
])
def test_metals_patterns_match_what_moves_gold(title, label):
    assert HW.match_headline("metals", title) == label


@pytest.mark.parametrize("title", [
    "Gold edges higher as dollar softens",          # a price wrap: the shock catches the move itself
    "Dollar surges on strong payrolls",             # a wrap too (PRICE_WRAP), never a news alert
    "Gold jewelry demand rises in India",
    "Central bank of Kenya names new governor",
    "Iran wins Asian Cup qualifier",
])
def test_metals_patterns_skip_wraps_and_noise(title):
    assert HW.match_headline("metals", title) is None


def test_metals_news_queries_and_odds_terms_cover_gold():
    assert "metals" in HW.NEWS_QUERIES and "silver" not in HW.NEWS_QUERIES
    assert any("gold" in q for q in HW.NEWS_QUERIES["metals"]) and len(HW.NEWS_QUERIES["metals"]) == 3
    assert all(q.endswith("when:1h") for q in HW.NEWS_QUERIES["metals"])
    rx = HW.ODDS_TERMS["metals"]
    for q in ("Will gold close above $4500 in 2026?", "Fed rate cut in December?", "US x Iran ceasefire by Nov 30?"):
        assert rx.search(q), q
    assert not rx.search("Will OPEC cut output?")


def test_trump_posts_on_the_fed_metals_tariffs_and_iran_wake_the_metals_desk():
    assert HW.match_post("metals", "Powell must cut interest rates NOW!") == "trump:fed"
    assert HW.match_post("metals", "A 25% TARIFF on all gold and bullion imports.") == "trump:tariff/metals"
    assert HW.match_post("metals", "Iran wants a deal. It could happen!") == "trump:iran"
    assert HW.match_post("metals", "KENTUCKY CLOSING SOON. A BIG DEAL!") is None


def test_a_metals_alert_blacks_out_gold_and_silver_for_30_minutes(tmp_path):
    path = str(tmp_path / "alerts.json")
    HW.record([{"group": "metals", "kind": "news", "label": "fed", "title": "Fed surprises with a hike",
                "at_ms": NOW_MS}], path)
    for coin in (GOLD, SILVER):
        b = HW.headline_blackout(coin, NOW_MS + 10 * MIN, path)
        assert b and b["kind"] == "headline" and b["reason"].startswith("Headline blackout")
        assert HW.headline_blackout(coin, NOW_MS + 31 * MIN, path) is None
    assert HW.headline_blackout(BRENT, NOW_MS + 10 * MIN, path) is None
    assert [a["title"] for a in HW.alerts_for([GOLD], HW.read_alerts(path))] == ["Fed surprises with a hike"]
