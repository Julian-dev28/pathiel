"""services/intel/event_score.py (Stage 2) and the Stage 1 triggers of
headline_watch (data/oil_triggers.json): offline, the model faked."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from services.intel import event_score as ES
from services.intel import headline_watch as HW

MOCK = "Tanker struck by missile in Strait of Hormuz, UKMTO confirms"


# ── Stage 1 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,label", [
    (MOCK, "iran/hormuz"),                                               # the existing pattern wins
    ("Houthis seize vessel in Red Sea", "trigger:chokepoints_maritime"),
    ("UKMTO reports incident 60nm east of Fujairah", "trigger:chokepoints_maritime"),
    ("Saudi Aramco cuts December OSP for Asia", "trigger:supply_policy"),
    ("US tightens sanctions on Iranian shadow fleet", "trigger:supply_policy"),
    ("Biden orders SPR release of 15 million barrels", "spr"),
    ("CENTCOM: strikes on Houthi missile sites", "trigger:geopolitics"),
    ("Hamas, Israel agree ceasefire terms", "trigger:geopolitics"),
    ("Explosion reported at Ras Tanura refinery", "supply shock"),
    ("Two tankers collide off Singapore", "trigger:chokepoints_maritime"),
])
def test_stage1_triggers_match_the_operator_phrases(title, label):
    assert HW.match_headline("oil", title, triggers=True) == label


@pytest.mark.parametrize("title", [
    "Apple launches new iPhone with better camera",
    "Fed holds rates steady, signals patience",
    "Bitcoin climbs past $100k on ETF inflows",
    "Oil prices fall as tanker traffic resumes",         # a price wrap, never an alert
    "Spring sprint: marathon season starts in Berlin",   # "spr" inside a word must not match
    "The tank era of warfare is over, analysts say",     # "tank" is not "tanker"
])
def test_stage1_triggers_leave_noise_alone(title):
    assert HW.match_headline("oil", title, triggers=True) is None
    assert HW.match_headline("oil", title) is None


def test_the_rss_path_is_unchanged_by_the_triggers():
    """A bare trigger word without the pattern context does not alert the
    RSS flow (triggers default False)."""
    assert HW.match_headline("oil", "Houthis seize vessel in Red Sea") is None
    assert HW.match_headline("oil", "Houthis seize vessel in Red Sea", triggers=True)
    assert HW.match_trigger("metals", "Houthis seize vessel in Red Sea") is None


def test_stage1_is_under_five_milliseconds():
    title = (MOCK + " as OPEC+ weighs output cut and CENTCOM confirms airstrike on refinery ") * 2
    HW.match_headline("oil", title, triggers=True)       # warm
    n = 200
    t0 = time.perf_counter()
    for _ in range(n):
        HW.match_headline("oil", title, triggers=True)
        HW.match_headline("oil", "Fed holds rates steady, signals patience on inflation", triggers=True)
    per_call_ms = (time.perf_counter() - t0) / (2 * n) * 1000
    assert per_call_ms < 5.0, per_call_ms


def test_news_alerts_with_triggers_carries_the_provider_fields_and_dedups_with_rss():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    seen = set()
    item = {"title": "Houthis seize vessel in Red Sea", "url": "https://r/1", "source": "reuters.com",
            "published_at": now - timedelta(minutes=2), "provider": "finlight", "sentiment": "negative",
            "received_at": now - timedelta(seconds=30)}
    hits = HW.news_alerts("oil", [item], seen, now_ms, triggers=True)
    assert len(hits) == 1
    a = hits[0]
    assert a["label"] == "trigger:chokepoints_maritime" and a["provider"] == "finlight"
    assert a["sentiment"] == "negative" and a["received_ms"] == now_ms - 30_000 and a["direction"] == 1
    # the RSS poll lists the same title two minutes later: already seen, no second alert
    rss = {"title": "Houthis seize vessel in Red Sea", "url": "https://g/2", "source": "Reuters",
           "published_at": now - timedelta(minutes=2)}
    assert HW.news_alerts("oil", [rss], seen, now_ms + 120_000) == []
    # an untrusted provider source is learned and dropped like any RSS item
    junk = dict(item, title="Tanker seized off Oman", source="randomblog.net", url="https://r/3")
    assert HW.news_alerts("oil", [junk], seen, now_ms, triggers=True) == []


# ── Stage 2: the rule scorer ────────────────────────────────────────────────

def _valid(s):
    assert ES.validate_score(s) is not None, s
    assert set(("event_type", "expected_oil_direction", "confidence_score", "is_confirmed", "scorer",
                "rationale")) <= set(s)
    json.dumps(s)


def test_rule_score_on_the_mock_headline_is_a_confirmed_bullish_chokepoint():
    s = ES.rule_score(MOCK, source="reuters.com", sentiment="negative", sentiment_confidence=0.9,
                      stage1_label="iran/hormuz")
    _valid(s)
    assert s["event_type"] == "chokepoint_disruption" and s["expected_oil_direction"] == "bullish"
    assert s["is_confirmed"] is True and s["confidence_score"] >= 0.9 and s["scorer"] == "rule"


@pytest.mark.parametrize("title,et,direction,confirmed", [
    ("Israel launches airstrikes on Iranian missile bases", "kinetic_strike", "bullish", False),
    ("Iran, US reach deal to reopen Strait of Hormuz", "diplomatic_deescalation", "bearish", False),
    ("OPEC+ agrees surprise output cut", "opec_policy", "bullish", False),
    ("OPEC+ to raise output by 400,000 bpd in January", "opec_policy", "bearish", False),
    ("Reports say Israel may strike Iran nuclear sites, sources say", "unconfirmed_rumor", "bullish", False),
    ("Hamas and Israel agree ceasefire, Pentagon confirms", "diplomatic_deescalation", "bearish", True),
    ("Fire halts Ras Tanura refinery", "chokepoint_disruption", "bullish", False),
    ("Apple launches new iPhone", "noise", "neutral", False),
    ("Oil prices fall as tanker traffic resumes", "noise", "neutral", False),
    ("Hezbollah strikes a deal with Lebanese government", "diplomatic_deescalation", "bearish", False),
])
def test_rule_score_event_types_and_directions(title, et, direction, confirmed):
    s = ES.rule_score(title, source="Reuters")
    _valid(s)
    assert (s["event_type"], s["expected_oil_direction"], s["is_confirmed"]) == (et, direction, confirmed)


def test_rule_score_confidence_moves_with_trust_confirmation_sentiment_and_rumor():
    base = ES.rule_score("Tanker struck by missile in Strait of Hormuz")["confidence_score"]
    trusted = ES.rule_score("Tanker struck by missile in Strait of Hormuz", source="reuters.com")["confidence_score"]
    confirmed = ES.rule_score(MOCK, source="reuters.com")["confidence_score"]
    agree = ES.rule_score(MOCK, source="reuters.com", sentiment="negative", sentiment_confidence=0.9)["confidence_score"]
    disagree = ES.rule_score(MOCK, source="reuters.com", sentiment="positive", sentiment_confidence=0.9)["confidence_score"]
    weak = ES.rule_score(MOCK, source="reuters.com", sentiment="positive", sentiment_confidence=0.2)["confidence_score"]
    rumor = ES.rule_score("Tanker reportedly struck by missile in Strait of Hormuz", source="reuters.com")
    assert base < trusted < confirmed <= 1.0 and agree >= confirmed and disagree < confirmed and weak == confirmed
    assert rumor["event_type"] == "unconfirmed_rumor" and rumor["confidence_score"] < trusted
    assert rumor["is_confirmed"] is False
    assert 0.0 <= ES.rule_score("")["confidence_score"] <= 1.0 and ES.rule_score("")["event_type"] == "noise"


def test_validate_score_is_strict():
    good = {"event_type": "noise", "expected_oil_direction": "neutral", "confidence_score": 0.5,
            "is_confirmed": False, "extra": 1}
    assert ES.validate_score(good) == {"event_type": "noise", "expected_oil_direction": "neutral",
                                       "confidence_score": 0.5, "is_confirmed": False}
    assert ES.validate_score(dict(good, event_type="war")) is None
    assert ES.validate_score(dict(good, expected_oil_direction="up")) is None
    assert ES.validate_score(dict(good, confidence_score=1.5)) is None
    assert ES.validate_score(dict(good, confidence_score="0.5")) is None
    assert ES.validate_score(dict(good, confidence_score=True)) is None
    assert ES.validate_score(dict(good, is_confirmed="yes")) is None
    assert ES.validate_score("x") is None and ES.validate_score(None) is None


# ── Stage 2: the local model and its fallback ───────────────────────────────

def test_the_model_is_used_only_from_a_local_url(monkeypatch):
    assert ES.is_local_url("http://localhost:11434/api/generate")
    assert ES.is_local_url("http://127.0.0.1:8000/v1/chat/completions")
    assert ES.is_local_url("http://10.0.0.7:8000/v1/chat/completions")
    assert ES.is_local_url("http://gpu-box.local:8000/v1/chat/completions")
    assert not ES.is_local_url("https://api.openai.com/v1/chat/completions", resolve=lambda h: "104.18.0.1")
    assert not ES.is_local_url("https://api.anthropic.com/v1/messages", resolve=lambda h: "104.18.0.2")
    assert not ES.is_local_url("ftp://localhost/x") and not ES.is_local_url("not a url")
    assert not ES.is_local_url("http://nowhere.invalid/x", resolve=lambda h: (_ for _ in ()).throw(OSError()))
    # a hosted URL in the env: refused before any request, the rule scorer answers
    monkeypatch.setenv(ES.SCORER_URL_ENV, "https://api.openai.com/v1/chat/completions")
    monkeypatch.setattr(ES, "is_local_url", lambda url, resolve=None: False)
    calls = []

    async def post(url, body, timeout_s):
        calls.append(url)
        return {}
    s = asyncio.run(ES.score_event(MOCK, source="reuters.com", post=post))
    assert calls == [] and s["scorer"] == "rule" and "model unavailable" in s["rationale"]


def test_a_valid_model_answer_replaces_the_rule_and_keeps_it_alongside(monkeypatch):
    url = "http://localhost:11434/api/generate"
    seen = {}

    async def post(u, body, timeout_s):
        seen.update(body)
        return {"response": json.dumps({"event_type": "kinetic_strike", "expected_oil_direction": "bullish",
                                        "confidence_score": 0.8, "is_confirmed": True})}
    s = asyncio.run(ES.score_event(MOCK, source="reuters.com", url=url, post=post))
    assert s["scorer"] == "model" and s["event_type"] == "kinetic_strike" and s["confidence_score"] == 0.8
    assert s["rule"]["event_type"] == "chokepoint_disruption"
    assert seen["format"] == "json" and seen["stream"] is False and MOCK in seen["prompt"]
    ES.validate_score(s)


def test_openai_compatible_shape_and_fenced_json_are_read():
    url = "http://127.0.0.1:8000/v1/chat/completions"

    async def post(u, body, timeout_s):
        assert body["response_format"] == {"type": "json_object"} and body["messages"][0]["role"] == "user"
        return {"choices": [{"message": {"content": "Here you go:\n```json\n" + json.dumps({
            "event_type": "diplomatic_deescalation", "expected_oil_direction": "bearish",
            "confidence_score": 0.6, "is_confirmed": False}) + "\n```"}}]}
    s = asyncio.run(ES.model_score("x", url=url, post=post))
    assert s["event_type"] == "diplomatic_deescalation" and s["scorer"] == "model"


@pytest.mark.parametrize("answer", [
    {"response": "not json at all"},
    {"response": json.dumps({"event_type": "war", "expected_oil_direction": "bullish", "confidence_score": 0.5,
                             "is_confirmed": True})},
    {"response": json.dumps({"event_type": "noise", "expected_oil_direction": "bullish", "confidence_score": 7,
                             "is_confirmed": True})},
    {},
    "garbage",
])
def test_a_bad_model_answer_falls_back_to_the_rule_scorer(answer):
    async def post(u, body, timeout_s):
        return answer
    s = asyncio.run(ES.score_event(MOCK, source="reuters.com", url="http://localhost:11434/api/generate", post=post))
    assert s["scorer"] == "rule" and s["event_type"] == "chokepoint_disruption"


def test_a_model_timeout_or_error_falls_back_to_the_rule_scorer():
    async def slow(u, body, timeout_s):
        await asyncio.sleep(5)
        return {}

    async def boom(u, body, timeout_s):
        raise ConnectionError("down")
    for post in (slow, boom):
        s = asyncio.run(ES.score_event(MOCK, source="reuters.com", url="http://localhost:11434/api/generate",
                                       post=post, **({} if post is boom else {})))
        assert s["scorer"] == "rule"
    t0 = time.perf_counter()
    s = asyncio.run(ES.model_score(MOCK, url="http://localhost:11434/api/generate", post=slow, timeout_s=0.2))
    assert s is None and time.perf_counter() - t0 < 2.0


def test_score_event_sync_works_outside_and_inside_a_running_loop():
    s = ES.score_event_sync(MOCK, source="reuters.com")
    assert s["scorer"] == "rule" and s["expected_oil_direction"] == "bullish"

    async def inside():
        return ES.score_event_sync(MOCK, source="reuters.com")
    assert asyncio.run(inside())["event_type"] == "chokepoint_disruption"
