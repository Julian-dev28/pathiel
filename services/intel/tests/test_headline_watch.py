"""services/intel/headline_watch.py and the headline step of
scripts/trigger_watch.py: surprise headlines wake the desk, make the thesis
due and hold new entries for 30 minutes.

Operator, 2026-11-05: "FIX THEM". A US-Iran deal, a strike or a Trump post
has no date; the thesis only saw it at the next rewrite. On 11-04 the midterm
result dropped Brent 9% and the desk reacted late.

Every file here is a tmp_path file; nothing touches /tmp/trigger-watch.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.intel import headline_watch as HW
from services.strategies import event_blackout as EB

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("trigger_watch_hw", ROOT / "scripts" / "trigger_watch.py")
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)
_tspec = importlib.util.spec_from_file_location("thesis_due_hw", ROOT / "scripts" / "thesis_due.py")
TD = importlib.util.module_from_spec(_tspec)
_tspec.loader.exec_module(TD)

NOW = datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
MIN = 60_000


def _item(title, age_min=5, source="Reuters"):
    return {"title": title, "url": "https://x/" + str(abs(hash(title))), "source": source,
            "published_at": NOW - timedelta(minutes=age_min)}


@pytest.fixture
def alerts_file(tmp_path, monkeypatch):
    path = tmp_path / "headline_alerts.json"
    monkeypatch.setattr(HW, "ALERTS_PATH", str(path))
    monkeypatch.setattr(HW, "STATE_PATH", str(tmp_path / "headline_state.json"))
    return path


# ── patterns ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,label", [
    ("Iran, US reach deal to reopen Strait of Hormuz", "iran/hormuz"),
    ("US strikes Iran's Qeshm Island", "iran/hormuz"),
    ("Tehran says talks with Washington collapsed", "iran/hormuz"),
    ("OPEC+ agrees surprise output cut", "opec"),
    ("Trump says Iran talks going well", "iran/hormuz"),
    ("Trump orders release from Strategic Petroleum Reserve", "trump/oil"),
    ("Explosion shuts major Saudi pipeline", "supply shock"),
])
def test_oil_headlines_that_matter_match(title, label):
    assert HW.match_headline("oil", title) == label


@pytest.mark.parametrize("title", [
    "Oil prices steady as traders await data",
    "Brent edges higher ahead of EIA report",
    "Silver jewelry demand rises in India",
    "Weekly market wrap: stocks mixed",
    "Hormuz Shipping Co. reports quarterly earnings",
    # live smoke 2026-11-05: wraps and slow stories that the first draft matched
    "Oil up as investors weigh uncertainties around Iran war",
    "Oil prices fall as US election results raise hopes of Iran deal",      # the reaction: price_shock's job
    "How A Chandigarh Couple Allegedly Helped Iran's Sanctioned Oil Tankers Sail Under False Flags",
    "US sanctions target an alleged disguise for Iranian oil ships",
    "Middle East crude oil premiums tumble to pre-war levels amid ample supply",
    "European Gas Prices Drop on Iran Deal Hopes after U.S. Midterms",
    "Asian stocks slide as Iran ceasefire talks stall",
])
def test_routine_oil_headlines_do_not_match(title):
    assert HW.match_headline("oil", title) is None


def test_the_event_itself_matches_even_when_a_wrap_about_it_does_not():
    assert HW.match_headline("oil", "Iran Reports More Tanker Fires in Strait of Hormuz") == "supply shock"
    assert HW.match_headline("oil", "Iran and US sign deal to end the war") == "iran/hormuz"
    assert HW.match_headline("oil", "Iran talks collapse, Tehran walks out") == "iran/hormuz"
    assert HW.match_headline("oil", "Oil prices fall as Iran and US sign deal") is None


def test_trusted_sources():
    assert HW.trusted_source("Reuters") and HW.trusted_source("Bloomberg.com") and HW.trusted_source("oilprice.com")
    assert HW.trusted_source(None) and HW.trusted_source("")
    assert not HW.trusted_source("Townpress Newspaper") and not HW.trusted_source("theopenpress.in")
    assert not HW.trusted_source("News18") and not HW.trusted_source("WANA News Agency")
    assert HW.trusted_source("Al Arabiya English") and HW.trusted_source("The Washington Post")
    items = [_item("Iran, US reach deal to reopen Strait of Hormuz", source="Townpress Newspaper"),
             _item("US strikes Iran's Qeshm Island", source="Reuters")]
    seen: set = set()
    out = HW.news_alerts("oil", items, seen, NOW_MS)
    assert [a["title"] for a in out] == ["US strikes Iran's Qeshm Island"] and len(seen) == 2


def test_silver_patterns():
    assert HW.match_headline("silver", "Powell announces emergency rate cut") == "fed"
    assert HW.match_headline("silver", "Trump says he will fire Powell") == "trump/fed"
    assert HW.match_headline("silver", "China export controls hit silver and copper") == "tariff/metals"
    assert HW.match_headline("silver", "Silver jewelry demand rises in India") is None
    assert HW.match_headline("silver", "Iran, US reach deal to reopen Strait of Hormuz") is None
    assert HW.match_headline("gas", "anything") is None


# ── news_alerts ─────────────────────────────────────────────────────────────

def test_news_alerts_match_dedup_and_stale():
    seen: set = set()
    items = [_item("Iran, US reach deal to reopen Strait of Hormuz"),
             _item("Oil prices steady as traders await data"),
             _item("OPEC+ agrees surprise output cut", age_min=HW.MAX_AGE_MIN + 1),   # too old
             _item("US strikes Iran's Qeshm Island", age_min=HW.MAX_AGE_MIN - 1)]
    out = HW.news_alerts("oil", items, seen, NOW_MS)
    assert [a["title"] for a in out] == ["Iran, US reach deal to reopen Strait of Hormuz",
                                         "US strikes Iran's Qeshm Island"]
    assert out[0] == {"group": "oil", "kind": "news", "label": "iran/hormuz",
                      "title": "Iran, US reach deal to reopen Strait of Hormuz", "source": "Reuters",
                      "url": out[0]["url"], "at_ms": NOW_MS, "published_ms": NOW_MS - 5 * MIN}
    assert len(seen) == 4                       # every title learned, matching or not
    # the same titles again (even re-cased, re-punctuated) alert nothing
    again = [_item("IRAN, US REACH DEAL TO REOPEN STRAIT OF HORMUZ!"), _item("US strikes Iran's Qeshm Island")]
    assert HW.news_alerts("oil", again, seen, NOW_MS) == []
    # an item without a date is taken as fresh; an empty title is skipped
    nodate = [{"title": "Iran seizes tanker in Hormuz", "published_at": None}, {"title": ""}]
    assert len(HW.news_alerts("oil", nodate, seen, NOW_MS)) == 1


# ── odds_alerts ─────────────────────────────────────────────────────────────

def test_odds_jump_threshold_and_first_sighting():
    prev: dict = {}
    mkts = [{"id": "a", "question": "US-Iran ceasefire by Dec 31?", "probability": 0.20},
            {"id": "b", "question": "Fed cuts in December?", "probability": 0.50},     # not an oil term
            {"id": None, "question": "Iran deal?", "probability": 0.5}]               # no id: skipped
    assert HW.odds_alerts("oil", mkts, prev, NOW_MS) == []        # first sighting: learn only
    assert prev == {"a": 0.20}
    mkts[0]["probability"] = 0.27                                   # +0.07: under the 0.08 jump
    assert HW.odds_alerts("oil", mkts, prev, NOW_MS) == []
    mkts[0]["probability"] = 0.19                                   # -0.08 from 0.27
    out = HW.odds_alerts("oil", mkts, prev, NOW_MS)
    assert len(out) == 1 and out[0]["label"] == "odds jump" and "27% -> 19%" in out[0]["title"]
    assert prev["a"] == 0.19


# ── price_shock ─────────────────────────────────────────────────────────────

def _hist(start_ms, mids, step_min=1):
    return [(start_ms + i * step_min * MIN, m) for i, m in enumerate(mids)]


def test_price_shock_both_directions_and_under_threshold():
    h = _hist(NOW_MS - 14 * MIN, [100.0] * 14)
    down = HW.price_shock("xyz:BRENTOIL", h, NOW_MS, 98.9)      # -1.1%
    up = HW.price_shock("xyz:BRENTOIL", h, NOW_MS, 101.05)      # +1.05%
    assert down and down["label"] == "price shock" and down["group"] == "oil" and "-1.10%" in down["title"]
    assert up and "+1.05%" in up["title"]
    assert HW.price_shock("xyz:BRENTOIL", h, NOW_MS, 99.1) is None            # -0.9%
    assert HW.price_shock("xyz:SILVER", h, NOW_MS, 98.9) is None              # silver needs 1.2%
    assert HW.price_shock("xyz:SILVER", h, NOW_MS, 98.7)["group"] == "silver"
    assert HW.price_shock("xyz:GOLD", h, NOW_MS, 90.0) is None                # no threshold: never
    assert HW.price_shock("xyz:BRENTOIL", [], NOW_MS, 90.0) is None            # no history yet


def test_price_shock_only_looks_inside_the_window():
    old = _hist(NOW_MS - 40 * MIN, [100.0] * 10)                 # 40 to 31 min ago
    recent = _hist(NOW_MS - 10 * MIN, [99.0] * 10)               # price already down, 10 min ago
    assert HW.price_shock("xyz:BRENTOIL", old + recent, NOW_MS, 98.9) is None   # -0.1% inside 15 min
    edge = [(NOW_MS - 15 * MIN, 100.0)] + recent                 # exactly 15 min ago still counts
    assert HW.price_shock("xyz:BRENTOIL", edge, NOW_MS, 98.9) is not None


# ── the alert file ──────────────────────────────────────────────────────────

def _alert(at_ms, group="oil", label="iran/hormuz", title="Iran deal", **kw):
    return dict({"group": group, "kind": "news", "label": label, "title": title, "at_ms": at_ms}, **kw)


def test_record_caps_per_group_per_day_and_keeps_the_file_small(alerts_file):
    day = [_alert(NOW_MS + i * MIN, title=f"oil {i}") for i in range(8)]
    kept = HW.record(day)
    assert [a["title"] for a in kept] == [f"oil {i}" for i in range(HW.MAX_ALERTS_PER_DAY)]
    assert HW.record([_alert(NOW_MS + 9 * MIN, group="silver", title="silver 1")])    # another group: fine
    assert HW.record([_alert(NOW_MS + 10 * MIN, title="oil late")]) == []              # oil is capped today
    tomorrow = NOW_MS + 24 * 60 * MIN
    assert HW.record([_alert(tomorrow, title="oil tomorrow")])                          # a new UTC day
    rows = HW.read_alerts()
    assert len(rows) == HW.MAX_ALERTS_PER_DAY + 2 and rows[-1]["title"] == "oil tomorrow"
    assert json.loads(alerts_file.read_text()) == rows
    # five a day for forty days (under the cap): the file keeps the newest KEEP_ALERTS
    HW.record([_alert(tomorrow + d * 24 * 60 * MIN + i * MIN, group="silver", title=f"s{d}.{i}")
               for d in range(40) for i in range(5)])
    rows = HW.read_alerts()
    assert len(rows) == HW.KEEP_ALERTS and rows[-1]["title"] == "s39.4"


def test_read_alerts_tolerates_a_missing_or_broken_file(tmp_path):
    assert HW.read_alerts(str(tmp_path / "nope.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert HW.read_alerts(str(bad)) == []
    bad.write_text('{"a": 1}')
    assert HW.read_alerts(str(bad)) == []


def test_latest_for_and_alerts_for_match_by_coin_or_group(alerts_file):
    HW.record([_alert(NOW_MS, title="oil news"),
               _alert(NOW_MS + MIN, group="silver", label="fed", title="fed news"),
               dict(_alert(NOW_MS + 2 * MIN, group="oil", label="price shock", title="shock"), coin="xyz:CL")])
    assert HW.latest_for("xyz:BRENTOIL")["title"] == "shock"     # CL's shock is an oil-group alert
    assert HW.latest_for("xyz:SILVER")["title"] == "fed news"
    assert HW.latest_for("xyz:GOLD") is None
    rows = HW.read_alerts()
    assert [a["title"] for a in HW.alerts_for(["xyz:BRENTOIL", "xyz:CL"], rows)] == ["oil news", "shock"]
    assert [a["title"] for a in HW.alerts_for(["xyz:SILVER"], rows)] == ["fed news"]
    assert HW.alerts_for(["BTC"], rows) == []


def test_headline_blackout_window_boundaries(alerts_file):
    HW.record([_alert(NOW_MS)])
    end = NOW_MS + HW.HEADLINE_BLACKOUT_MIN * MIN
    assert HW.headline_blackout("xyz:BRENTOIL", NOW_MS - 1) is None           # before the alert
    hit = HW.headline_blackout("xyz:BRENTOIL", NOW_MS)
    assert hit and hit["end_ms"] == end and hit["until_utc"] == "2026-11-05 20:30Z"
    assert hit["reason"].startswith("Headline blackout: no new entries until 2026-11-05 20:30Z (iran/hormuz: Iran deal)")
    assert HW.headline_blackout("xyz:CL", end - 1) is not None                 # same group
    assert HW.headline_blackout("xyz:BRENTOIL", end) is None                   # end is open
    assert HW.headline_blackout("xyz:SILVER", NOW_MS + MIN) is None            # other group


def test_state_round_trip_trims_seen(tmp_path):
    path = str(tmp_path / "state.json")
    assert HW.load_state(path) is None
    HW.save_state({"seen": [str(i) for i in range(2500)], "odds": {"a": 0.2}, "last_news_s": 1.0}, path)
    st = HW.load_state(path)
    assert len(st["seen"]) == 2000 and st["seen"][0] == "500" and st["odds"] == {"a": 0.2}


# ── trigger_watch.headline_pass with injected fetchers ──────────────────────

TARGETS = [("oil", "xyz:BRENTOIL", "oil"), ("metals", "xyz:SILVER", "silver")]


def test_headline_pass_primes_then_alerts_on_a_new_headline_only():
    state: dict = {}
    hist: dict = {}
    feed = {"oil": [_item("Iran, US reach deal to reopen Strait of Hormuz")], "silver": []}
    now_s = NOW_MS / 1000
    out = W.headline_pass(TARGETS, now_s, state, hist, {"xyz:BRENTOIL": 100.0, "xyz:SILVER": 50.0},
                          fetch_news=lambda g: feed[g], fetch_odds_fn=lambda d: [], prime=True)
    assert out == [] and len(state["seen"]) == 1 and state["last_news_s"] == now_s
    # one minute later: the poll interval has not passed, no fetch at all
    calls = []
    out = W.headline_pass(TARGETS, now_s + 60, state, hist, {"xyz:BRENTOIL": 100.1},
                          fetch_news=lambda g: calls.append(g) or [], fetch_odds_fn=lambda d: [])
    assert out == [] and calls == []
    # at the poll: the old headline is known, the new one alerts for the oil desk only
    feed["oil"].append(_item("US strikes Iran's Qeshm Island"))
    feed["silver"] = [_item("Silver jewelry demand rises in India")]
    out = W.headline_pass(TARGETS, now_s + HW.NEWS_POLL_S, state, hist, {"xyz:BRENTOIL": 100.2},
                          fetch_news=lambda g: feed[g], fetch_odds_fn=lambda d: [])
    assert [(d, a["label"], a["title"]) for d, a in out] == [("oil", "iran/hormuz", "US strikes Iran's Qeshm Island")]
    assert len(state["seen"]) == 3
    # a fetcher that raises costs nothing but that poll's news
    out = W.headline_pass(TARGETS, now_s + 2 * HW.NEWS_POLL_S, state, hist, {},
                          fetch_news=lambda g: 1 / 0, fetch_odds_fn=lambda d: 1 / 0)
    assert out == []


def test_headline_pass_price_shock_fires_once_then_rearms():
    state: dict = {}
    hist: dict = {}
    t0 = NOW_MS / 1000
    quiet = (lambda g: [], lambda d: [])
    for i in range(10):
        out = W.headline_pass(TARGETS, t0 + 60 * i, state, hist, {"xyz:BRENTOIL": 100.0},
                              fetch_news=quiet[0], fetch_odds_fn=quiet[1])
        assert out == []
    out = W.headline_pass(TARGETS, t0 + 600, state, hist, {"xyz:BRENTOIL": 98.9},
                          fetch_news=quiet[0], fetch_odds_fn=quiet[1])
    assert [(d, a["kind"], a["coin"], a["group"]) for d, a in out] == [("oil", "price", "xyz:BRENTOIL", "oil")]
    assert state["last_shock_ms"]["xyz:BRENTOIL"] == int((t0 + 600) * 1000)
    # the next minutes still sit 1.1% under the window: no second alert (re-arm)
    for i in range(1, HW.PRICE_REARM_MIN):
        assert W.headline_pass(TARGETS, t0 + 600 + 60 * i, state, hist, {"xyz:BRENTOIL": 98.9},
                               fetch_news=quiet[0], fetch_odds_fn=quiet[1]) == []
    # re-armed and still moving: a second leg alerts again
    out = W.headline_pass(TARGETS, t0 + 600 + 60 * HW.PRICE_REARM_MIN, state, hist, {"xyz:BRENTOIL": 97.7},
                          fetch_news=quiet[0], fetch_odds_fn=quiet[1])
    assert len(out) == 1 and "-1.2" in out[0][1]["title"]
    assert len(hist["xyz:BRENTOIL"]) <= HW.PRICE_WINDOW_MIN + 2


def test_headline_pass_odds_jump_wakes_the_desk():
    state: dict = {}
    odds = [{"id": "m1", "question": "US-Iran ceasefire by year end?", "probability": 0.30, "url": "u"}]
    t0 = NOW_MS / 1000
    W.headline_pass(TARGETS[:1], t0, state, {}, {}, fetch_news=lambda g: [], fetch_odds_fn=lambda d: odds)
    odds[0]["probability"] = 0.45
    out = W.headline_pass(TARGETS[:1], t0 + HW.NEWS_POLL_S, state, {}, {},
                          fetch_news=lambda g: [], fetch_odds_fn=lambda d: odds)
    assert len(out) == 1 and out[0][0] == "oil" and out[0][1]["kind"] == "odds"


# ── thesis_due: the headline reason and the 30-vs-60 minute gap ─────────────

T0 = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def _thesis(**kw):
    base = {"id": "oil-x", "created_at": T0.isoformat(), "expires_at": (T0 + timedelta(days=7)).isoformat(),
            "call": {"side": "long", "entry_px": 100.0, "stop_px": 99.0}, "catalysts": []}
    base.update(kw)
    return base


def _hw_alert(minutes_after, title="Iran, US reach deal to reopen Strait of Hormuz"):
    return _alert(int((T0 + timedelta(minutes=minutes_after)).timestamp() * 1000), title=title)


def test_a_headline_after_the_thesis_makes_it_due_past_the_hourly_gap():
    out = TD.refresh_due(_thesis(), T0 + timedelta(minutes=45), mid=100.0, headline_alerts=[_hw_alert(40)])
    assert out["due"] is True
    assert out["reasons"] == ["headline: iran/hormuz: Iran, US reach deal to reopen Strait of Hormuz"]
    assert "overrides the hour" in out["held_back"]
    # without the alert the same minute is held back
    assert TD.refresh_due(_thesis(), T0 + timedelta(minutes=45), mid=100.0)["due"] is False


def test_a_headline_still_waits_thirty_minutes_after_the_last_thesis():
    out = TD.refresh_due(_thesis(), T0 + timedelta(minutes=29), mid=100.0, headline_alerts=[_hw_alert(20)])
    assert out["due"] is False and "even a headline waits 30 min" in out["held_back"]
    out = TD.refresh_due(_thesis(), T0 + timedelta(minutes=30), mid=100.0, headline_alerts=[_hw_alert(20)])
    assert out["due"] is True and out["reasons"][0].startswith("headline:")


def test_a_headline_before_the_thesis_or_in_the_future_does_not_count():
    old = _hw_alert(-5)
    future = _hw_alert(200)
    out = TD.refresh_due(_thesis(), T0 + timedelta(minutes=90), mid=100.0, headline_alerts=[old, future])
    assert out["due"] is False and out["reasons"] == []


def test_past_the_hour_the_headline_joins_the_other_reasons_newest_first():
    alerts = [_hw_alert(70, title="first"), _hw_alert(80, title="newest")]
    out = TD.refresh_due(_thesis(), T0 + timedelta(hours=25), mid=100.0, headline_alerts=alerts)
    assert out["reasons"][0] == "headline: iran/hormuz: newest" and out["reasons"][1].startswith("daily:")


def test_fomc_cpi_nfp_and_elections_are_rewrite_catalysts_now():
    cats = [{"kind": "fomc", "title": "FOMC decision", "at": "2026-11-05T19:00:00Z"},
            {"kind": "nfp", "title": "US jobs report (Oct NFP)", "at": "2026-11-06T13:30:00Z"},
            {"kind": "election", "title": "US midterm elections", "at": "2026-11-04T00:00:00Z"},   # before the thesis
            {"kind": "cpi", "title": "CPI release", "at": "2026-11-10T13:30:00Z"},
            {"kind": "ppi", "title": "US PPI", "at": "2026-11-05T13:30:00Z"}]                      # impact 2: no
    out = TD.refresh_due(_thesis(catalysts=cats), datetime(2026, 11, 5, 19, 15, tzinfo=timezone.utc), mid=100.0)
    assert out["reasons"] == ["catalyst passed: FOMC decision at 2026-11-05 19:00Z"]
    out = TD.refresh_due(_thesis(catalysts=cats), datetime(2026, 11, 6, 13, 46, tzinfo=timezone.utc), mid=100.0)
    assert any("NFP" in r for r in out["reasons"])
    for k in ("fomc", "cpi", "nfp", "election", "api_weekly", "eia_weekly", "opec_meeting"):
        assert k in TD.CATALYST_KINDS
    assert TD.HEADLINE_MIN_GAP_MINUTES == HW.HEADLINE_MIN_GAP_MIN == 30


# ── event_blackout: EIA, then the calendar, then the headline ───────────────

def _ms(*parts):
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def test_event_blackout_order_eia_then_scheduled_then_headline(alerts_file):
    # 2026-11-04: EIA window 13:30Z to 17:30Z; the midterm window runs to 18:00Z.
    HW.record([_alert(_ms(2026, 11, 4, 15, 20), title="Iran deal")])
    assert EB.event_blackout("xyz:BRENTOIL", _ms(2026, 11, 4, 15, 30))["reason"].startswith("EIA blackout")
    assert EB.event_blackout("xyz:BRENTOIL", _ms(2026, 11, 4, 12, 0))["reason"].startswith("Event blackout")
    HW.record([_alert(_ms(2026, 11, 4, 19, 50), title="US strikes Iran's Qeshm Island")])
    hit = EB.event_blackout("xyz:BRENTOIL", _ms(2026, 11, 4, 20, 0))
    assert hit["kind"] == "headline" and hit["until_utc"] == "2026-11-04 20:20Z"
    assert hit["reason"].startswith("Headline blackout: no new entries until 2026-11-04 20:20Z")
    assert EB.event_blackout("xyz:CL", _ms(2026, 11, 4, 20, 19)) is not None       # same group
    assert EB.event_blackout("xyz:BRENTOIL", _ms(2026, 11, 4, 20, 20)) is None
    assert EB.event_blackout("xyz:SILVER", _ms(2026, 11, 4, 20, 0)) is None       # no silver alert


def test_event_blackout_without_an_alert_file_is_unchanged(alerts_file):
    assert EB.event_blackout("xyz:BRENTOIL", _ms(2026, 11, 5, 20, 0)) is None


# ── the brief line ──────────────────────────────────────────────────────────

def test_the_brief_shows_a_recent_alert_for_two_hours(alerts_file):
    """scripts/pathiel-mcp-server.py `_headline_alert_line`, compiled on its
    own (importing the server would read the live env)."""
    import ast
    import time
    import typing
    src = (ROOT / "scripts" / "pathiel-mcp-server.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_headline_alert_line")
    const = next(n for n in tree.body if isinstance(n, ast.Assign)
                 and getattr(n.targets[0], "id", None) == "HEADLINE_ALERT_SHOW_S")
    mod = ast.Module(body=[const, fn], type_ignores=[])
    ns: dict = {"time": time, "Optional": typing.Optional}
    exec(compile(mod, "mcp-server-slice", "exec"), ns)
    line = ns["_headline_alert_line"]
    HW.record([_alert(NOW_MS, title="Iran, US reach deal to reopen Strait of Hormuz")])
    assert line("xyz:BRENTOIL", NOW_MS / 1000 + 60) == (
        " Last headline alert 2026-11-05 20:00Z: iran/hormuz: Iran, US reach deal to reopen Strait of Hormuz.")
    assert line("xyz:BRENTOIL", NOW_MS / 1000 + 2 * 3600 + 1) == ""
    assert line("xyz:SILVER", NOW_MS / 1000 + 60) == ""
    assert "_headline_alert_line(coin)" in src and "the tick does not enter until then" in src


# ── the agent and the loops skill carry the rules ───────────────────────────

def test_the_tick_agent_and_the_loops_skill_mention_the_headline_rules():
    for path in (ROOT / ".claude" / "agents" / "desk-tick.md", ROOT / "agents" / "desk-tick.md"):
        text = path.read_text()
        assert "Headline blackout" in text, path
        assert "action=NONE headline blackout until <utc>" in text, path
        assert "judge the position with\n     the NEW thesis" in text, path
        assert "A headline alone is not a close" in text, path
        assert "Say the headline in your operator sentence" in text, path
    loops = (ROOT / ".claude" / "skills" / "desk-loops" / "SKILL.md").read_text()
    assert '"reason": "headline"' in loops and '"also"' in loops and "one agent per desk" in loops
