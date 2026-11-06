"""headline_watch direction (+1 / -1 / 0 per alert) and the one-sided
headline blackout.

Operator, 2026-11-05: "we SHOULDNT PAUSE FOR BULLISH HEADLINES" (the oil
thesis is long). An alert whose direction supports the trade side no longer
holds that side's entries; against or unknown still holds. Polarity is per
group: the operator's feed tagged "Iran Opens Strait of Hormuz to China and
India as U.S. Blockade Continues" as "bull"; for oil it is -1 (more supply).

Every file here is a tmp_path file; nothing touches /tmp/trigger-watch.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from services.intel import headline_watch as HW
from services.strategies import event_blackout as EB

NOW_MS = int(datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc).timestamp() * 1000)
MIN = 60_000


@pytest.fixture
def alerts_file(tmp_path, monkeypatch):
    path = tmp_path / "headline_alerts.json"
    monkeypatch.setattr(HW, "ALERTS_PATH", str(path))
    monkeypatch.setattr(HW, "STATE_PATH", str(tmp_path / "headline_state.json"))
    return path


# ── today's real alerts (2026-11-05/06 /tmp/trigger-watch/headline_alerts.json) ──

REAL_ROWS = [
    ({"coin": "xyz:BRENTOIL", "group": "oil", "kind": "price", "label": "price shock",
      "title": "xyz:BRENTOIL -1.02% in 15 min (97.6495 -> 96.6535)", "at_ms": 1793890591649, "move": -0.0102}, -1),
    ({"group": "oil", "kind": "odds", "label": "odds jump", "at_ms": 1793891844562,
      "title": "Will Kazem Gharibabadi attend a US x Iran diplomatic meeting by December 31, 2026?: 50% -> 60%"}, -1),
    ({"group": "oil", "kind": "odds", "label": "odds jump", "at_ms": 1793892383531,
      "title": "Will Iran target an Arab country by November 15, 2026?: 68% -> 60%"}, -1),
    ({"group": "oil", "kind": "news", "label": "iran/hormuz", "source": "oilprice.com", "at_ms": 1793894504359,
      "title": "Iran Opens Strait of Hormuz to China and India as U.S. Blockade Continues"}, -1),
    ({"group": "oil", "kind": "news", "label": "iran/hormuz", "source": "Bloomberg", "at_ms": 1793977560000,
      "title": "U.S., Iran edge closer to deal amid hopes for nuclear compromise - Bloomberg News"}, -1),
    ({"group": "oil", "kind": "odds", "label": "odds jump", "at_ms": 1793977000000,
      "title": "Will Iran target an Arab country by November 30, 2026?: 60% -> 80%"}, 1),
    ({"group": "oil", "kind": "news", "label": "trump/oil", "at_ms": 1793977600000,
      "title": "Trump's Iran Strategy Reaches a Critical Crossroads After Midterm Defeat"}, 0),
]


@pytest.mark.parametrize("row,expected", REAL_ROWS, ids=[r["title"][:40] for r, _ in REAL_ROWS])
def test_the_real_alert_rows_without_a_direction_field_are_read_by_the_same_rule(row, expected):
    """The live file predates the field: a price row's `move`, an odds row's
    '50% -> 60%' tail and a headline's words give the direction."""
    assert "direction" not in row
    assert HW.alert_direction(row) == expected


def test_a_stored_direction_wins_and_garbage_is_unknown():
    assert HW.alert_direction({"kind": "news", "group": "oil", "title": "US strikes Iran", "direction": -1}) == -1
    assert HW.alert_direction({"kind": "odds", "group": "oil", "title": "no percentages here"}) == 0
    assert HW.alert_direction({"kind": "price", "group": "oil", "title": "xyz:BRENTOIL +1.30% in 15 min"}) == 1
    assert HW.alert_direction({"kind": "price", "coin": "xyz:GOLD", "title": "no move"}) == 0


# ── headlines and posts ─────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("Brent crude jumps, settles up over $3 after tanker attacks", 1),      # the 11-05 evening
    ("US strikes Iran's Qeshm Island", 1),
    ("Explosion shuts major Saudi pipeline", 1),
    ("OPEC+ agrees surprise output cut", 1),
    ("US imposes naval blockade on Iranian ports", 1),
    ("US tightens sanctions on Iran oil exports", 1),
    ("Iran seizes tanker in Strait of Hormuz", 1),
    ("Iran Opens Strait of Hormuz to China and India as U.S. Blockade Continues", -1),
    ("U.S., Iran edge closer to deal amid hopes for nuclear compromise - Bloomberg News", -1),
    ("Iran, US reach deal to reopen Strait of Hormuz", -1),
    ("Trump says Iran talks going well", -1),
    ("Trump orders release from Strategic Petroleum Reserve", -1),
    ("U.S. Offers Another 17 Million SPR Barrels as Fuel Crisis Deepens", -1),
    ("OPEC+ Agrees to Boost Oil Output When Strait of Hormuz Reopens", -1),
    ("US lifts sanctions on Iranian crude", -1),
    ("De-escalation: Iran and US agree to talks", -1),
    ("US and Iran strike a deal on Hormuz transit", -1),                      # "strike a deal" is not a strike
    ("STAYING THE COURSE: OPEC+ Keeps Oil Output Policy Unchanged for October", 0),
    ("Iran, US reach deal; Houthis attack tanker off Yemen", 0),               # conflict
    ("Trump's Iran Strategy Reaches a Critical Crossroads After Midterm Defeat", 0),
    ("", 0),
])
def test_oil_headline_direction(title, expected):
    assert HW.text_direction("oil", title) == expected


@pytest.mark.parametrize("title,expected", [
    ("Tehran says talks with Washington collapsed", 1),
    ("Iran rejects US deal offer", 1),
    ("No deal: Iran walks out of Geneva talks", 1),
    ("Ceasefire collapses as Iran fires missiles at Gulf tankers", 1),
    ("Iran rules out any agreement with Washington", 1),
    ("Oil Prices are DROPPING LIKE A ROCK, down almost $10 a Barrel, A RECORD, because people feel that "
     "there will not be a War with Iran", -1),                                  # Trump, 2026-11-02
    ("Trump calls off strikes on Iran after Tehran signals talks", -1),
    ("US averts war with Iran as deal is signed", -1),
])
def test_negations_flip_instead_of_matching_the_bare_word(title, expected):
    assert HW.text_direction("oil", title) == expected


@pytest.mark.parametrize("title,expected", [
    ("Fed surprises with a hike", -1),
    ("Powell: hawkish hold", -1),
    ("Dollar surges as yields spike after hot CPI", -1),
    ("Fed signals no rate cut this year", -1),                                 # negation
    ("Iran wants a deal. It could happen!", -1),                               # risk-on
    ("Powell must cut interest rates NOW!", 1),
    ("Fed emergency cut after bank failures", 1),
    ("US strikes Iran's Qeshm Island", 1),                                     # risk-off
    ("PBOC adds gold to reserves for 12th month", 1),
    ("Fed holds rates steady", 0),
    ("A 25% TARIFF on all gold and bullion imports.", 0),
])
def test_metals_direction(title, expected):
    assert HW.text_direction("metals", title) == expected


def test_unknown_group_is_zero():
    assert HW.text_direction("crypto", "US strikes Iran") == 0
    assert HW.odds_direction("crypto", "Will Iran target an Arab country?", 0.1) == 0


# ── odds: sign(change) x the market's polarity ──────────────────────────────

@pytest.mark.parametrize("group,question,change,expected", [
    ("oil", "Will Kazem Gharibabadi attend a US x Iran diplomatic meeting by December 31, 2026?", 0.10, -1),
    ("oil", "Will Iran target an Arab country by November 15, 2026?", -0.08, -1),
    ("oil", "Will Iran target an Arab country by November 30, 2026?", 0.20, 1),
    ("oil", "Will the Strait of Hormuz reopen by November 30?", 0.10, -1),
    ("oil", "Will the US and Iran sign a ceasefire by Dec 31?", -0.10, 1),
    ("oil", "Will the ceasefire collapse by Dec 31?", 0.10, 1),
    ("oil", "Will Iran close the Strait of Hormuz by Nov 30?", 0.10, 1),
    ("oil", "Will OPEC+ cut production in December?", 0.10, 1),
    ("oil", "Will Brent close above $100 on Nov 30?", 0.10, 1),
    ("oil", "Who will win the 2026 World Series?", 0.10, 0),
    ("oil", "Will Iran target an Arab country?", 0.0, 0),
    ("metals", "Will the Fed cut rates in December?", 0.10, 1),
    ("metals", "Will the Fed hike rates in 2026?", 0.10, -1),
    ("metals", "Fed decision in December: no change?", 0.10, 0),
])
def test_odds_direction(group, question, change, expected):
    assert HW.odds_direction(group, question, change) == expected


def test_every_alert_constructor_writes_the_direction():
    now = NOW_MS
    item = {"title": "US strikes Iran's Qeshm Island", "url": "u", "source": "Reuters",
            "published_at": datetime(2026, 11, 5, 19, 55, tzinfo=timezone.utc)}
    assert HW.news_alerts("oil", [item], set(), now)[0]["direction"] == 1
    post = {"id": "p1", "text": "There will be NO DEAL with Iran until they stop!", "source": "truthsocial:realdonaldtrump",
            "published_at": datetime(2026, 11, 5, 19, 55, tzinfo=timezone.utc), "url": "u", "post_type": "post"}
    assert HW.post_alerts("oil", [post], set(), now)[0]["direction"] == 1
    prev = {"m1": 0.50}
    odds = [{"id": "m1", "question": "Will Kazem Gharibabadi attend a US x Iran diplomatic meeting by December 31, 2026?",
             "probability": 0.60, "url": "u"}]
    o = HW.odds_alerts("oil", odds, prev, now)[0]
    assert o["direction"] == -1 and o["change"] == 0.1
    hist = [(now - 5 * MIN, 100.0)]
    assert HW.price_shock("xyz:BRENTOIL", hist, now, 98.9)["direction"] == -1
    assert HW.price_shock("xyz:BRENTOIL", hist, now, 101.1)["direction"] == 1
    long_hist = [(now - 200 * MIN, 100.0), (now - 100 * MIN, 100.5)]
    assert HW.price_drift("xyz:BRENTOIL", long_hist, now, 101.8)["direction"] == 1


# ── the one-sided blackout ──────────────────────────────────────────────────

def _alert(at_ms, direction, title="x", group="oil"):
    return {"group": group, "kind": "news", "label": "iran/hormuz", "title": title, "at_ms": at_ms,
            "direction": direction}


def test_an_aligned_alert_does_not_hold_its_side_and_side_none_is_the_old_rule(alerts_file):
    HW.record([_alert(NOW_MS, 1, "US strikes Iran's Qeshm Island")])
    t = NOW_MS + 5 * MIN
    assert HW.headline_blackout("xyz:BRENTOIL", t) is not None                      # old rule: any alert holds
    assert HW.headline_blackout("xyz:BRENTOIL", t, side="long") is None             # aligned: skipped
    short = HW.headline_blackout("xyz:BRENTOIL", t, side="short")
    assert short and short["side"] == "short" and short["direction"] == 1 and short["skipped"] == []
    assert short["reason"] == ("Headline blackout: no new short entries until 2026-11-05 20:30Z "
                               "(iran/hormuz: US strikes Iran's Qeshm Island; against the short); the thesis rewrites first")
    assert HW.headline_blackout("xyz:CL", t, side="long") is None                   # same group, same skip
    aligned = HW.skipped_aligned("xyz:BRENTOIL", t, "long")
    assert aligned and aligned["title"] == "US strikes Iran's Qeshm Island"
    assert HW.skipped_aligned("xyz:BRENTOIL", t, "short") is None
    assert HW.skipped_aligned("xyz:BRENTOIL", t, None) is None
    assert HW.skipped_aligned("xyz:BRENTOIL", NOW_MS + 30 * MIN, "long") is None    # window over


def test_an_unknown_direction_holds_both_sides(alerts_file):
    HW.record([_alert(NOW_MS, 0, "Trump's Iran Strategy Reaches a Critical Crossroads")])
    for side in ("long", "short", None):
        hit = HW.headline_blackout("xyz:BRENTOIL", NOW_MS + MIN, side=side)
        assert hit and hit["direction"] == 0, side
        if side:
            assert "direction unknown" in hit["reason"]
    assert HW.skipped_aligned("xyz:BRENTOIL", NOW_MS + MIN, "long") is None


def test_the_newest_non_supporting_alert_sets_the_window(alerts_file):
    """A bullish alert at :00, a bearish one at :05, a bullish one at :20: a
    long is held by the :05 alert until :35, not by the newest; a short is
    held by the :20 alert until :50."""
    HW.record([_alert(NOW_MS, 1, "strike"), _alert(NOW_MS + 5 * MIN, -1, "deal"),
               _alert(NOW_MS + 20 * MIN, 1, "attack")])
    t = NOW_MS + 25 * MIN
    long = HW.headline_blackout("xyz:BRENTOIL", t, side="long")
    assert long["event"] == "deal" and long["until_utc"] == "2026-11-05 20:35Z"
    assert [a["title"] for a in long["skipped"]] == ["attack", "strike"]
    assert HW.headline_blackout("xyz:BRENTOIL", NOW_MS + 35 * MIN, side="long") is None
    short = HW.headline_blackout("xyz:BRENTOIL", t, side="short")
    assert short["event"] == "attack" and short["until_utc"] == "2026-11-05 20:50Z"
    assert HW.headline_blackout("xyz:BRENTOIL", t)["event"] == "attack"             # old rule: the newest


def test_legacy_rows_in_the_file_are_read_by_direction_too(alerts_file):
    """The live file's rows have no direction field; the blackout derives it."""
    rows = [dict(r, at_ms=NOW_MS) for r, _ in REAL_ROWS[:4]]
    alerts_file.write_text(json.dumps(rows))
    assert HW.headline_blackout("xyz:BRENTOIL", NOW_MS + MIN, side="long") is not None   # all four are -1
    assert HW.headline_blackout("xyz:BRENTOIL", NOW_MS + MIN, side="short") is None     # all four support a short


def test_event_blackout_passes_the_side_through_and_the_other_windows_stay_blind(alerts_file):
    HW.record([_alert(NOW_MS, 1, "US strikes Iran's Qeshm Island")])
    t = NOW_MS + MIN                                      # Thu 2026-11-05 20:01Z: no EIA, no weekend
    assert EB.event_blackout("xyz:BRENTOIL", t)["kind"] == "headline"
    assert EB.event_blackout("xyz:BRENTOIL", t, side="long") is None
    assert EB.event_blackout("xyz:BRENTOIL", t, side="short")["kind"] == "headline"
    # the weekend holds a long whatever the alert says (oil coins only)
    sat = int(datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    HW.record([_alert(sat - MIN, 1, "attack")])
    assert EB.event_blackout("xyz:BRENTOIL", sat, side="long")["kind"] == "weekend"
    # the EIA window too (Wed 2026-11-04 15:30Z)
    eia = int(datetime(2026, 11, 4, 15, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert EB.event_blackout("xyz:BRENTOIL", eia, side="long")["event"] == "EIA weekly petroleum report"


def test_supports():
    assert HW.supports({"direction": 1}, "long") and HW.supports({"direction": -1}, "short")
    assert not HW.supports({"direction": 1}, "short") and not HW.supports({"direction": 0}, "long")
    assert not HW.supports({"direction": 1}, None) and not HW.supports({"direction": 1}, "flat")


def test_the_tick_agent_names_the_one_sided_rule():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    for rel in (".claude/agents/desk-tick.md", "agents/desk-tick.md"):
        text = (root / rel).read_text()
        assert 'we SHOULDNT PAUSE FOR BULLISH\n  HEADLINES' in text, rel
        assert "does not hold that side's\n  entries" in text, rel
        assert "never chase and no entry\n  after a break still apply" in text, rel
