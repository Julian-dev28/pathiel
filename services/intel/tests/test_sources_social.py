"""services/intel/sources/social.py and the post/official step of the headline
watcher (headline_watch.match_post / post_alerts / news_alerts(shared=True),
trigger_watch.headline_pass with injected fetchers).

Operator, 2026-11-05: "A Truth Social / X feed for the headline watcher.
BUILD IT THEN, CHECK IF IT CAN BE DONE FIRST". Fixtures are real captures of
that day: fixtures/trumpstruth_feed.xml (12 of 540 posts, including the
2026-11-02 "Oil Prices are DROPPING LIKE A ROCK ... War with Iran" post and
its repost), fixtures/whitehouse_news.xml, fixtures/state_press_releases.xml
(bodies cut). No network: http_client is monkeypatched where a fetch runs.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.intel import headline_watch as HW
from services.intel import http_client
from services.intel.sources import social
from services.intel.tests.conftest import fixture_text

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("trigger_watch_social", ROOT / "scripts" / "trigger_watch.py")
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)

NOW = datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc)        # 14 min after the Iran/oil post
NOW_MS = int(NOW.timestamp() * 1000)
IRAN_POST_ID = "117548803071800973"
IRAN_REPOST_ID = "117549834242650471"
TARGETS = [("oil", "xyz:BRENTOIL", "oil"), ("metals", "xyz:SILVER", "metals")]


def _posts():
    return social.parse_trumpstruth(fixture_text("trumpstruth_feed.xml"))


def _post(text, age_min=5, pid=None, **kw):
    row = {"id": pid or str(abs(hash(text))), "url": "https://truthsocial.com/@realDonaldTrump/" + (pid or "1"),
           "text": text, "title": text[:200], "source": social.TRUMP_SOURCE, "post_type": "original",
           "published_at": NOW - timedelta(minutes=age_min)}
    row.update(kw)
    return row


# ── parsing, real fixtures ──────────────────────────────────────────────────

def test_parse_trumpstruth_fixture_keeps_the_original_id_url_time_and_type():
    posts = _posts()
    assert len(posts) == 12
    first = posts[0]
    assert first["id"] == "117564115835486203"
    assert first["url"] == "https://truthsocial.com/@realDonaldTrump/117564115835486203"
    assert first["archive_url"] == "https://www.trumpstruth.org/statuses/43490"
    assert first["source"] == social.TRUMP_SOURCE and first["post_type"] == "original"
    assert first["published_at"] == datetime(2026, 11, 5, 13, 40, 44, tzinfo=timezone.utc)
    assert first["text"].startswith("The vote totals for the Fake Dan Sullivan") and "<" not in first["text"]
    assert first["title"] == first["text"][:200]
    by_id = {p["id"]: p for p in posts}
    iran = by_id[IRAN_POST_ID]
    assert iran["text"].startswith("Oil Prices are DROPPING LIKE A ROCK, down almost $10 a Barrel")
    assert iran["published_at"] == datetime(2026, 11, 2, 20, 46, 7, tzinfo=timezone.utc) or \
        iran["published_at"].strftime("%Y-%m-%d %H:%M") == "2026-11-02 20:46"
    assert by_id[IRAN_REPOST_ID]["post_type"] == "repost" and "Oil Prices" in by_id[IRAN_REPOST_ID]["text"]
    types = {p["post_type"] for p in posts}
    assert {"original", "repost", "quote"} <= types
    # an image post and a link post keep a (useless) text, never a crash
    assert any(p["text"] == "View image" for p in posts)
    assert any("truthsocial.com/@WhiteHouse" in p["text"] for p in posts)


def test_strip_html_decodes_entities_and_collapses_whitespace():
    assert social.strip_html("<p>Oil &amp; Gas</p><p>down   10%</p>") == "Oil & Gas down 10%"
    assert social.strip_html(None) == "" and social.strip_html("  ") == ""


def test_parse_official_fixtures_label_the_source_and_key_by_url():
    wh = social.parse_official(fixture_text("whitehouse_news.xml"), "whitehouse.gov")
    st = social.parse_official(fixture_text("state_press_releases.xml"), "state.gov")
    assert len(wh) == 8 and len(st) == 10
    assert all(r["source"] == "whitehouse.gov" for r in wh) and all(r["source"] == "state.gov" for r in st)
    assert wh[0]["title"] == "President Trump Will Again Defy the Odds, the Fake News, and the Radical Left"
    assert wh[0]["url"].startswith("https://www.whitehouse.gov/") and wh[0]["id"] == wh[0]["url"]
    assert wh[0]["published_at"] == datetime(2026, 11, 4, 22, 39, 3, tzinfo=timezone.utc)
    assert st[0]["title"].startswith("Disrupting Iran") and st[0]["published_at"].tzinfo is not None
    assert all(HW.trusted_source(r["source"]) for r in wh + st)


def test_the_new_sources_are_trusted_and_old_junk_still_is_not():
    assert HW.trusted_source("truthsocial:realDonaldTrump")
    assert HW.trusted_source("whitehouse.gov") and HW.trusted_source("state.gov")
    assert not HW.trusted_source("Townpress Newspaper")


# ── post patterns, real posts first ─────────────────────────────────────────

def test_the_real_iran_oil_post_and_its_repost_match_for_both_desks():
    """Since gold (2026-11-05) an Iran post is the metals desk's safe-haven
    headline too (trump:iran), not only oil's."""
    by_id = {p["id"]: p for p in _posts()}
    assert HW.match_post("oil", by_id[IRAN_POST_ID]["text"]) == "trump:iran"
    assert HW.match_post("oil", by_id[IRAN_REPOST_ID]["text"]) == "trump:iran"
    assert HW.match_post("metals", by_id[IRAN_POST_ID]["text"]) == "trump:iran"


def test_every_other_real_post_in_the_fixture_is_a_miss():
    for p in _posts():
        if p["id"] in (IRAN_POST_ID, IRAN_REPOST_ID):
            continue
        for g in ("oil", "metals"):
            assert HW.match_post(g, p["text"]) is None, (g, p["text"][:80])


@pytest.mark.parametrize("text,label", [
    ("Great talks with Iran today. They want a settlement, and they want it fast!", "trump:iran"),
    ("Just spoke with MBS. OPEC will be increasing production. Prices coming down!", "trump:oil"),
    ("Oil prices are way down, $58 a barrel. DRILL, BABY, DRILL!", "trump:oil"),
    ("I have ordered a massive strike on Houthi positions in Yemen. It is over for them!", "trump:geo"),
    ("Russia and Ukraine have agreed to a ceasefire, effective immediately. Sanctions to follow if broken.", "trump:geo"),
    ("Tariffs on China go to 100% on November 1st. They know why!", "trump:tariff"),
    ("Hormuz will be reopened or Tehran will pay a price like never before.", "trump:iran"),
])
def test_oil_posts_that_matter_match(text, label):
    assert HW.match_post("oil", text) == label


@pytest.mark.parametrize("text,label", [
    ("Jerome \"Too Late\" Powell should have cut rates months ago. The Fed is a disgrace!", "trump:fed"),
    ("Powell must lower interest rates NOW. No inflation!", "trump:fed"),
    ("The Federal Reserve meets today. Cut by a full point!", "trump:fed"),
    ("Kevin Hassett would be a great Fed Chairman!", "trump:fed"),
    ("50% tariff on all copper imports, effective August 1st.", "trump:tariff/metals"),
])
def test_silver_posts_that_matter_match(text, label):
    assert HW.match_post("metals", text) == label


@pytest.mark.parametrize("text", [
    "KENTUCKY CLOSING SOON. A BIG DEAL! President DJT",
    "Stock Market just hit another Record High. Your 401(k)s are through the roof!",
    "Crime rates are down 40% in Washington, D.C. since the National Guard arrived.",
    "We made a great deal for the people of Kentucky!",
    "It is my Great Honor to endorse Tracey Powell, who is running to represent Indiana! Oil & Gas champion.",
    "Senator Roger Marshall will vote for a thriving Kansas Oil and Gas Industry. Complete and Total Endorsement!",
    "John James, Brave Iraq War Veteran, is running to become the next Governor of Michigan!",
    "Her opponent, Frank Mrvan, strongly opposed our Great STEEL TARIFFS. Barb is running for Congress!",
    "Watching the Pre-Election Results on FoxNews. Have to go somewhere else until that LOSER is off the air!",
    "View image",
    "View video View video",
    "https:// truthsocial.com/@WhiteHouse/11 7550235500967429",
    "RT @ realDonaldTrump Reposted Donald J. Trump View video",
    "",
])
def test_routine_posts_never_match(text):
    assert HW.match_post("oil", text) is None and HW.match_post("metals", text) is None


# ── post_alerts: dedup by id, stale skip, the alert row ─────────────────────

def test_post_alerts_dedup_by_id_skip_stale_and_undated_and_build_the_row():
    seen: set = set()
    posts = [
        _post("Great talks with Iran today!", age_min=10, pid="a1"),
        _post("Great talks with Iran today! (edited)", age_min=10, pid="a1"),          # same id: once
        _post("OPEC will be increasing oil production.", age_min=HW.MAX_AGE_MIN + 1, pid="a2"),  # stale
        _post("Hormuz is open.", age_min=5, pid="a3", published_at=None),              # no time: never
        _post("Stock Market Record High!", age_min=1, pid="a4"),                       # no match
    ]
    out = HW.post_alerts("oil", posts, seen, NOW_MS)
    assert [(a["label"], a["post_id"]) for a in out] == [("trump:iran", "a1")]
    a = out[0]
    assert a["group"] == "oil" and a["kind"] == "post" and a["source"] == social.TRUMP_SOURCE
    assert a["title"] == "Great talks with Iran today!" and a["url"].endswith("/a1")
    assert a["at_ms"] == NOW_MS and a["published_ms"] == NOW_MS - 10 * 60_000 and a["post_type"] == "original"
    assert seen == {"oil:post:a1", "oil:post:a2", "oil:post:a3", "oil:post:a4"}
    # the second poll sees nothing new; the metals desk judges the same posts on its own key
    # (an Iran post is gold's safe-haven headline too, since 2026-11-05)
    assert HW.post_alerts("oil", posts, seen, NOW_MS + 60_000) == []
    assert [(a["label"], a["post_id"]) for a in HW.post_alerts("metals", posts, seen, NOW_MS)] == [("trump:iran", "a1")]
    assert "metals:post:a1" in seen and HW.post_alerts("metals", posts, seen, NOW_MS + 60_000) == []


def test_post_alerts_on_the_real_fixture_fourteen_minutes_after_the_post():
    seen: set = set()
    out = HW.post_alerts("oil", _posts(), seen, NOW_MS)
    # the original is 14 min old: alerts; the repost (11-03 01:08) is in the future here and so is
    # everything newer: never an alert (now_ms - pub_ms < 0 is not stale, but it matched the same post,
    # so only the id decides) -> exactly the original and the repost, as two distinct ids
    labels = sorted((a["label"], a["post_id"]) for a in out)
    assert labels == [("trump:iran", IRAN_POST_ID), ("trump:iran", IRAN_REPOST_ID)]
    assert len(seen) == 12


def test_the_alert_row_feeds_record_latest_for_and_the_blackout(tmp_path, monkeypatch):
    path = str(tmp_path / "headline_alerts.json")
    monkeypatch.setattr(HW, "ALERTS_PATH", path)
    a = HW.post_alerts("oil", [_post("Great talks with Iran today!", pid="p1")], set(), NOW_MS)[0]
    assert HW.record([a]) == [a]
    assert HW.latest_for("xyz:BRENTOIL")["post_id"] == "p1"
    b = HW.headline_blackout("xyz:BRENTOIL", NOW_MS + 60_000)
    assert b and "trump:iran: Great talks with Iran today!" in b["reason"]


# ── official feeds through news_alerts(shared=True) ─────────────────────────

def test_shared_items_are_judged_by_every_group_and_keyed_by_url():
    seen: set = set()
    item = {"id": "https://www.whitehouse.gov/x/1", "url": "https://www.whitehouse.gov/x/1",
            "title": "President Trump Orders Release From the Strategic Petroleum Reserve",
            "source": "whitehouse.gov", "published_at": NOW - timedelta(minutes=3)}
    out = HW.news_alerts("oil", [item], seen, NOW_MS, shared=True)
    assert [a["label"] for a in out] == ["trump/oil"] and out[0]["source"] == "whitehouse.gov"
    assert HW.news_alerts("metals", [item], seen, NOW_MS, shared=True) == []     # judged, no silver pattern
    assert len(seen) == 2
    edited = dict(item, title="President Trump Orders a Release From the Strategic Petroleum Reserve")
    assert HW.news_alerts("oil", [edited], seen, NOW_MS, shared=True) == []       # same URL: once
    # the unshared path is unchanged: one key per title, so the second group never sees it
    seen2: set = set()
    HW.news_alerts("oil", [item], seen2, NOW_MS)
    assert len(seen2) == 1 and HW.news_alerts("metals", [item], seen2, NOW_MS) == [] and len(seen2) == 1


def test_official_fixture_items_pass_the_source_gate_and_match_only_events():
    wh = social.parse_official(fixture_text("whitehouse_news.xml"), "whitehouse.gov")
    st = social.parse_official(fixture_text("state_press_releases.xml"), "state.gov")
    now = datetime(2026, 11, 5, 2, 30, tzinfo=timezone.utc)
    seen: set = set()
    out = HW.news_alerts("oil", wh + st, seen, int(now.timestamp() * 1000), shared=True)
    # "Disrupting Iran's Pursuit of Man-Portable Air-Defense Systems" (6 min old) has no event word;
    # "U.S. Sanctions Fraudulent Ship Registries ..." is 8 hours old and sanctions are excluded on purpose
    assert out == []
    assert len(seen) == 18


# ── fetchers: a failure is a reason string, never an exception ──────────────

def test_fetch_trump_posts_parses_the_fixture_and_reports_failures(monkeypatch):
    calls = []

    def ok(url, **kw):
        calls.append((url, kw.get("params"), kw.get("retries")))
        return fixture_text("trumpstruth_feed.xml"), None
    monkeypatch.setattr(http_client, "fetch_text", ok)
    posts, err = social.fetch_trump_posts()
    assert err is None and len(posts) == 12
    assert calls == [(social.TRUMPSTRUTH_FEED_URL, {"limit": social.TRUMPSTRUTH_LIMIT}, 0)]
    monkeypatch.setattr(http_client, "fetch_text", lambda url, **kw: (None, "timeout"))
    assert social.fetch_trump_posts() == ([], "social:trumpstruth:timeout")
    monkeypatch.setattr(http_client, "fetch_text", lambda url, **kw: ("<html>cloudflare</html", None))
    assert social.fetch_trump_posts() == ([], "social:trumpstruth:bad_xml")
    monkeypatch.setattr(http_client, "fetch_text", lambda url, **kw: ("", None))
    assert social.fetch_trump_posts() == ([], "social:trumpstruth:empty")


def test_only_whitehouse_is_fetched_state_gov_refuses_our_user_agent():
    """state.gov's press RSS answers 403 to a non-browser User-Agent (eval
    2026-11-05); we do not spoof one. Its fixture stays parse-tested so the
    feed can be switched on the day it opens up."""
    assert [s for s, _u in social.OFFICIAL_FEEDS] == ["whitehouse.gov"]


def test_fetch_official_feeds_isolates_one_failing_feed(monkeypatch):
    monkeypatch.setattr(social, "OFFICIAL_FEEDS", (("whitehouse.gov", social.WHITEHOUSE_NEWS_URL),
                                                    ("state.gov", social.STATE_PRESS_URL)))

    def one_down(url, **kw):
        assert kw.get("retries") == 0
        if url == social.WHITEHOUSE_NEWS_URL:
            return None, "http_503"
        return fixture_text("state_press_releases.xml"), None
    monkeypatch.setattr(http_client, "fetch_text", one_down)
    items, errs = social.fetch_official_feeds()
    assert errs == ["social:whitehouse.gov:http_503"] and len(items) == 10
    assert {i["source"] for i in items} == {"state.gov"}
    monkeypatch.setattr(http_client, "fetch_text", lambda url, **kw: ("not xml", None))
    assert social.fetch_official_feeds() == ([], ["social:whitehouse.gov:bad_xml", "social:state.gov:bad_xml"])
    monkeypatch.setattr(http_client, "fetch_text", lambda url, **kw: (fixture_text("whitehouse_news.xml"), None))
    items, errs = social.fetch_official_feeds()
    assert errs == [] and len(items) == 16          # the same fixture served for both URLs


def test_the_watcher_fetchers_swallow_the_error(monkeypatch):
    monkeypatch.setattr(http_client, "fetch_text", lambda url, **kw: (None, "timeout"))
    assert W.fetch_posts() == [] and W.fetch_official() == []


# ── headline_pass wiring with injected fetchers ─────────────────────────────

def _quiet():
    return dict(fetch_news=lambda g: [], fetch_odds_fn=lambda d: [])


def test_headline_pass_primes_the_new_sources_then_alerts_on_a_new_post_for_the_right_desk():
    state: dict = {}
    t0 = NOW_MS / 1000
    posts = [_post("Great talks with Iran today!", age_min=3, pid="p1")]
    official = [{"id": "u1", "url": "u1", "title": "President Trump Orders Release From the Strategic Petroleum Reserve",
                 "source": "whitehouse.gov", "published_at": NOW - timedelta(minutes=2)}]
    out = W.headline_pass(TARGETS, t0, state, {}, {}, fetch_posts_fn=lambda: posts,
                          fetch_official_fn=lambda: official, prime=True, **_quiet())
    assert out == [] and state["primed_sources"] == ["official", "posts"]
    assert {"oil:post:p1", "metals:post:p1"} <= set(state["seen"]) and state["last_posts_s"] == t0
    # 30 s later: the post poll has not come round; nothing fetched
    calls = []
    out = W.headline_pass(TARGETS, t0 + 30, state, {}, {}, fetch_posts_fn=lambda: calls.append(1) or [],
                          fetch_official_fn=lambda: calls.append(2) or [], **_quiet())
    assert out == [] and calls == []
    # at the post poll: a new post on the Fed wakes the metals desk only; the official feed is not due yet
    posts.append(_post("Powell must cut rates NOW!", age_min=1, pid="p2"))
    out = W.headline_pass(TARGETS, t0 + HW.POSTS_POLL_S, state, {}, {}, fetch_posts_fn=lambda: posts,
                          fetch_official_fn=lambda: calls.append(2) or [], **_quiet())
    assert [(d, a["kind"], a["label"], a["title"]) for d, a in out] == [("metals", "post", "trump:fed",
                                                                          "Powell must cut rates NOW!")]
    assert calls == []
    # at the news poll: a new official item alerts the oil desk, the old one is known
    official.append({"id": "u2", "url": "u2", "title": "US strikes Iran's Qeshm Island",
                     "source": "whitehouse.gov", "published_at": NOW - timedelta(minutes=1)})
    out = W.headline_pass(TARGETS, t0 + HW.NEWS_POLL_S, state, {}, {}, fetch_posts_fn=lambda: posts,
                          fetch_official_fn=lambda: official, **_quiet())
    assert [(d, a["kind"], a["label"], a["source"]) for d, a in out] == [
        ("oil", "news", "iran/hormuz", "whitehouse.gov"), ("metals", "news", "iran/hormuz", "whitehouse.gov")]


def test_an_old_state_file_learns_the_new_sources_first_and_still_alerts_on_news():
    """The live watcher restarts on a state file written before this feature:
    no primed_sources. Its first pass must not alert on every post already
    out, while a new wire headline still wakes the desk as before."""
    state = {"seen": [], "last_news_s": 0.0, "last_shock_ms": {}, "odds": {}}
    t0 = NOW_MS / 1000
    posts = [_post("Great talks with Iran today!", age_min=3, pid="p1")]
    official = [{"id": "u1", "url": "u1", "title": "US strikes Iran's Qeshm Island",
                 "source": "state.gov", "published_at": NOW - timedelta(minutes=2)}]
    wire = [{"title": "OPEC+ agrees surprise output cut", "url": "w", "source": "Reuters",
             "published_at": NOW - timedelta(minutes=2)}]
    out = W.headline_pass(TARGETS, t0, state, {}, {}, fetch_news=lambda g: wire if g == "oil" else [],
                          fetch_odds_fn=lambda d: [], fetch_posts_fn=lambda: posts, fetch_official_fn=lambda: official)
    assert [(d, a["label"]) for d, a in out] == [("oil", "opec")]
    assert state["primed_sources"] == ["official", "posts"]
    # the next polls alert on what is new only
    posts.append(_post("Hormuz will be reopened or Tehran pays.", age_min=1, pid="p2"))
    official.append({"id": "u2", "url": "u2", "title": "Iran, US reach deal to reopen Strait of Hormuz",
                     "source": "state.gov", "published_at": NOW - timedelta(minutes=1)})
    out = W.headline_pass(TARGETS, t0 + HW.NEWS_POLL_S, state, {}, {}, fetch_news=lambda g: wire if g == "oil" else [],
                          fetch_odds_fn=lambda d: [], fetch_posts_fn=lambda: posts, fetch_official_fn=lambda: official)
    assert sorted((d, a["kind"], a["label"]) for d, a in out) == [("metals", "news", "iran/hormuz"),
                                                                   ("metals", "post", "trump:iran"),
                                                                   ("oil", "news", "iran/hormuz"),
                                                                   ("oil", "post", "trump:iran")]


def test_a_failing_post_or_official_fetcher_costs_only_that_source():
    state = {"primed_sources": ["official", "posts"]}
    t0 = NOW_MS / 1000
    wire = [{"title": "OPEC+ agrees surprise output cut", "url": "w", "source": "Reuters",
             "published_at": NOW - timedelta(minutes=2)}]
    out = W.headline_pass(TARGETS, t0, state, {}, {}, fetch_news=lambda g: wire if g == "oil" else [],
                          fetch_odds_fn=lambda d: [], fetch_posts_fn=lambda: 1 / 0, fetch_official_fn=lambda: 1 / 0)
    assert [(d, a["label"]) for d, a in out] == [("oil", "opec")]
    assert state["primed_sources"] == ["official", "posts"] and state["last_posts_s"] == t0


def test_the_watch_loop_stubs_cover_the_new_fetchers():
    """tests/test_trigger_watch.py and test_headline_watch.py stub fetch_posts
    and fetch_official; a stub missing there would hit the network under the
    gate's offline guard. Pin the names so a rename shows up here."""
    assert callable(W.fetch_posts) and callable(W.fetch_official)
    src = (ROOT / "tests" / "test_trigger_watch.py").read_text()
    assert 'monkeypatch.setattr(W, "fetch_posts"' in src and 'monkeypatch.setattr(W, "fetch_official"' in src
