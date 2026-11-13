"""Stage 2 of the streaming headline pipeline: what KIND of event is this
headline, which way should oil go, how sure, and is it confirmed.

Strict JSON, one shape (SCHEMA):

    {"event_type": "kinetic_strike" | "chokepoint_disruption" |
                   "diplomatic_deescalation" | "opec_policy" |
                   "unconfirmed_rumor" | "noise",
     "expected_oil_direction": "bullish" | "bearish" | "neutral",
     "confidence_score": 0..1,
     "is_confirmed": bool,
     "scorer": "rule" | "model",
     "rationale": short str}

Two scorers:
  rule_score   no LLM, pure, the default and the fallback. Reads the
               headline with fixed cue lists (event type), the SAME
               direction word lists the headline blackout uses
               (headline_watch.text_direction), the provider's sentiment,
               source trust (headline_watch.trusted_source) and
               confirmation cues (an official or military source, "confirms")
               against rumor cues ("reportedly", "unconfirmed", "sources say").
  model_score  optional, async, a LOCAL model only: EVENT_SCORER_URL names
               an Ollama (/api/generate) or an OpenAI-compatible
               (/v1/chat/completions, vLLM, llama.cpp) endpoint. The URL must
               resolve to a loopback, private or .local host; anything else
               is refused before a byte is sent (the project rule: no hosted
               LLM API). The model's JSON is validated against SCHEMA; any
               failure, timeout or refusal falls back to rule_score.

score_event is the entry point: rule first, model when configured, never
raises. The live blackout's direction field is NOT read from here: the
alert keeps headline_watch.text_direction's sign, and this JSON rides along
(alert["stage2"]) for the event log, the console line and the tick agent.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

from services.intel import headline_watch as HW

logger = logging.getLogger(__name__)

SCORER_URL_ENV = "EVENT_SCORER_URL"
SCORER_MODEL_ENV = "EVENT_SCORER_MODEL"
SCORER_TIMEOUT_ENV = "EVENT_SCORER_TIMEOUT_S"
DEFAULT_MODEL = "llama3.1"
DEFAULT_TIMEOUT_S = 8.0

EVENT_TYPES = ("kinetic_strike", "chokepoint_disruption", "diplomatic_deescalation", "opec_policy",
               "unconfirmed_rumor", "noise")
DIRECTIONS = ("bullish", "bearish", "neutral")
SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "event_type": {"type": "string", "enum": list(EVENT_TYPES)},
        "expected_oil_direction": {"type": "string", "enum": list(DIRECTIONS)},
        "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
        "is_confirmed": {"type": "boolean"},
    },
    "required": ["event_type", "expected_oil_direction", "confidence_score", "is_confirmed"],
}

# ── cue lists (lower-case regex, whole words) ───────────────────────────────
_RX = lambda p: re.compile(p, re.I)  # noqa: E731
CHOKEPOINT = _RX(r"\b(strait of hormuz|hormuz|bab[ -]el[ -]mandeb|red sea|suez|ukmto|tankers?|vessels?|ships?|"
                 r"shipping|seiz\w*|hijack\w*|board(ed|ing)|blockade\w*|strait|naval mines?|mines? in)\b")
KINETIC = _RX(r"\b(air ?strikes?|strikes?|struck|missiles?|drones?|attack\w*|explosion|blast|bomb\w*|shell\w*|"
              r"rockets?|fires? on|fired on|hit by|retaliat\w*|intercept\w*|downed|shot down)\b")
DIPLOMATIC = _RX(r"\b(ceasefire|cease-fire|truce|peace (talks|deal|agreement|plan)|de-?escalat\w*|"
                 r"talks|negotiat\w*|deal|agreement|accord|reopen\w*|resum\w*|normaliz\w*|lift\w* sanctions)\b")
OPEC = _RX(r"\b(opec\+?|aramco|ras tanura|output (cut|hike|increase|target|quota)|production (cut|hike|increase|"
           r"quota)|quota|spr|strategic petroleum reserve|export ban|sanctions?)\b")
# "strike a deal" is diplomacy, not a kinetic strike
DEAL_STRIKE = _RX(r"\bstrikes?\s+(a |the )?(deal|agreement|accord|truce)\b")
# a supply site out of action (headline_watch's "supply shock" pattern): a
# physical disruption, filed under chokepoint_disruption (the schema's one
# "supply is physically cut" type) with its own rationale
SUPPLY_SITE = _RX(r"\b(refiner(y|ies)|pipelines?|terminals?|oil ?fields?|ports?|platforms?|facilit(y|ies))\b")
SUPPLY_DAMAGE = _RX(r"\b(fire|fires|explosion|blast|outage|shut\w*|halt\w*|offline|damage\w*|evacuat\w*|leak\w*)\b")
CONFIRM = _RX(r"\b(confirms?|confirmed|ukmto|centcom|pentagon|navy|idf|irgc|ministry|minister|statement|"
              r"officials? (say|said|confirm)|official:|government (says|said)|announces?|declares?|"
              r"says in statement|according to (the )?(ministry|navy|military|pentagon))\b")
RUMOR = _RX(r"\b(reportedly|unconfirmed|unverified|sources? say|sources? said|according to sources|rumou?rs?|"
            r"may have|could|might|reports? (of|that|say)|claims?|alleged\w*|said to|appears? to|possible|"
            r"speculat\w*|report:|reported)\b")
# the market reaction itself, not an event
WRAP = HW.PRICE_WRAP

_BASE = {"kinetic_strike": 0.55, "chokepoint_disruption": 0.6, "diplomatic_deescalation": 0.5,
         "opec_policy": 0.5, "unconfirmed_rumor": 0.3, "noise": 0.1}
_DEFAULT_DIR = {"kinetic_strike": "bullish", "chokepoint_disruption": "bullish",
                "diplomatic_deescalation": "bearish", "opec_policy": "neutral",
                "unconfirmed_rumor": "neutral", "noise": "neutral"}


def _negated(title: str) -> str:
    """headline_watch's negation rewrite ("no deal" -> dealfails, "will not
    be a war" -> waraverted), so the cues read the same text the direction
    lists do. Pure."""
    t = title or ""
    for rx, token in HW.DIRECTION_WORDS["oil"]["negations"]:
        t = rx.sub(token, t)
    return t


# the de-escalation words proper (a truce announced, not one collapsing):
# when one of these comes BEFORE the first kinetic cue, the headline is
# about the de-escalation ("Houthis announce truce with US, Red Sea attacks
# to stop"); a kinetic cue first is the strike ("Israel strikes Gaza despite
# ceasefire talks")
DEESCALATION_FIRST = _RX(r"\b(ceasefire|cease-fire|truce|peace (talks|deal|agreement|plan)|de-?escalat\w*|"
                         r"waraverted|agree\w* (to )?(a )?(deal|ceasefire|truce)|reach\w* (a )?(deal|agreement))\b")


def event_type_of(title: str) -> str:
    """The event type by cue precedence: chokepoint > kinetic > OPEC >
    diplomatic, except that a de-escalation word ahead of the first kinetic
    cue files the headline as diplomatic. A rumor cue without a confirmation
    cue on a non-noise event makes it unconfirmed_rumor. Pure."""
    t = _negated(title)
    if WRAP.search(t):
        return "noise"
    no_deal = DEAL_STRIKE.sub(" ", t)
    kin = KINETIC.search(no_deal)
    kinetic = bool(kin)
    deesc = DEESCALATION_FIRST.search(t)
    if deesc and kin and deesc.start() < kin.start():
        et = "diplomatic_deescalation"
    elif CHOKEPOINT.search(t) and (kinetic or re.search(r"\b(seiz|hijack|blockade|board|clos|shut|halt|disrupt|"
                                                         r"divert|suspend|mine)\w*", t, re.I)):
        et = "chokepoint_disruption"
    elif kinetic:
        et = "kinetic_strike"
    elif OPEC.search(t) and re.search(r"\b(cut|hike|boost|increase|raise|quota|output|production|release|"
                                      r"emergency|meeting|agree|export ban|sanction)\w*", t, re.I):
        et = "opec_policy"
    elif DIPLOMATIC.search(t) or "dealfails" in t or "waraverted" in t:
        et = "diplomatic_deescalation"
    elif CHOKEPOINT.search(t) or (SUPPLY_SITE.search(t) and SUPPLY_DAMAGE.search(t)):
        et = "chokepoint_disruption"
    else:
        return "noise"
    if RUMOR.search(t) and not CONFIRM.search(t):
        return "unconfirmed_rumor"
    return et


def first_cue_direction(title: str) -> int:
    """When BOTH direction lists match (text_direction is 0, the blackout
    holds), the side whose first phrase comes earliest in the title: the
    headline's main verb leads, the "after ..." clause is background
    ("Strait of Hormuz shipping resumes after tanker traffic halt" is
    bearish; "Houthis announce truce with US, Red Sea attacks to stop" is
    bearish; "Ceasefire collapses as strikes resume" is bullish through the
    dealfails rewrite). 0 when one side or neither matches, or a tie. Pure,
    and never consulted by the blackout."""
    t = _negated(title)
    words = HW.DIRECTION_WORDS["oil"]
    bull = [m.start() for rx in words["bull"] for m in [rx.search(t)] if m]
    bear = [m.start() for rx in words["bear"] for m in [rx.search(t)] if m]
    if not bull or not bear:
        return 0
    b, r = min(bull), min(bear)
    return 1 if b < r else -1 if r < b else 0


def _direction_word(title: str, event_type: str) -> str:
    """text_direction's sign first (the blackout's reading), the first-cue
    rule when both lists fired, the event type's default when the lists are
    silent."""
    d = HW.text_direction("oil", title) or first_cue_direction(title)
    if d:
        return "bullish" if d > 0 else "bearish"
    if event_type == "unconfirmed_rumor":
        # the rumor's own content still has a sign: "Tanker reportedly struck" leans bullish
        kinetic = bool(KINETIC.search(DEAL_STRIKE.sub(" ", title)))
        if kinetic or CHOKEPOINT.search(title):
            return "bullish"
        if DIPLOMATIC.search(title):
            return "bearish"
        return "neutral"
    return _DEFAULT_DIR[event_type]


def rule_score(title: str, *, source: Optional[str] = None, sentiment: Optional[str] = None,
               sentiment_confidence: Optional[float] = None, stage1_label: Optional[str] = None,
               entities: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The rule scorer. Pure, deterministic, under a millisecond.

    confidence: the event type's base, +0.15 trusted source, +0.15 a
    confirmation cue, +0.10 the direction lists read a sign, +0.10 the
    provider's sentiment agrees with the direction (negative news is bullish
    oil for a strike or a chokepoint, positive news is bearish for a
    de-escalation), -0.10 when it disagrees, -0.15 a rumor cue, +0.05 a
    Stage 1 pattern hit (not a trigger). Clamped to [0, 1]."""
    t = (title or "").strip()
    et = event_type_of(t)
    direction = _direction_word(t, et)
    trusted = HW.trusted_source(source) if source else False
    confirm = bool(CONFIRM.search(t))
    rumor = bool(RUMOR.search(t))
    conf = _BASE[et]
    why = [et]
    if trusted:
        conf += 0.15
        why.append("trusted source")
    if confirm:
        conf += 0.15
        why.append("confirmation cue")
    if rumor:
        conf -= 0.15
        why.append("rumor cue")
    if HW.text_direction("oil", t):
        conf += 0.10
        why.append(f"direction words {direction}")
    elif first_cue_direction(t):
        conf += 0.05
        why.append(f"first cue {direction} (lists conflict)")
    s = (sentiment or "").lower() or None
    s_conf = float(sentiment_confidence) if sentiment_confidence is not None else 1.0
    if s in ("positive", "negative") and direction != "neutral" and s_conf >= 0.5:
        # risk news reads negative; for oil, negative risk news is bullish
        agrees = (s == "negative") == (direction == "bullish")
        conf += 0.10 if agrees else -0.10
        why.append(f"sentiment {s} {'agrees' if agrees else 'disagrees'}")
    if stage1_label and not str(stage1_label).startswith("trigger:"):
        conf += 0.05
        why.append(f"pattern {stage1_label}")
    if et == "noise":
        direction = "neutral"
    conf = round(max(0.0, min(1.0, conf)), 2)
    return {"event_type": et, "expected_oil_direction": direction, "confidence_score": conf,
            "is_confirmed": bool(confirm and not rumor), "scorer": "rule", "rationale": "; ".join(why)}


def validate_score(data: Any) -> Optional[Dict[str, Any]]:
    """`data` as a SCHEMA-shaped dict, or None when any field is missing or
    out of range. Extra fields are dropped. Pure."""
    if not isinstance(data, dict):
        return None
    et = data.get("event_type")
    d = data.get("expected_oil_direction")
    c = data.get("confidence_score")
    ok = data.get("is_confirmed")
    if et not in EVENT_TYPES or d not in DIRECTIONS or not isinstance(ok, bool):
        return None
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0.0 <= float(c) <= 1.0:
        return None
    return {"event_type": et, "expected_oil_direction": d, "confidence_score": round(float(c), 2),
            "is_confirmed": ok}


def scorer_url() -> Optional[str]:
    return (os.environ.get(SCORER_URL_ENV) or "").strip() or None


def is_local_url(url: str, resolve: Any = socket.gethostbyname) -> bool:
    """True only for http(s) to a loopback, private, link-local or .local
    host (resolved with `resolve`; a hostname that does not resolve is
    not local). The guard that keeps model_score off any hosted API. Pure
    apart from `resolve`."""
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    if host in ("localhost", "host.docker.internal") or host.endswith(".local") or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(resolve(host))
        except (OSError, ValueError):
            return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


PROMPT = ("You classify one news headline for a crude oil trading desk. Reply with ONLY a JSON object: "
          '{"event_type": one of ' + json.dumps(list(EVENT_TYPES)) + ', "expected_oil_direction": one of '
          + json.dumps(list(DIRECTIONS)) + ', "confidence_score": number 0..1, "is_confirmed": boolean}. '
          "bullish = less supply or more risk (strike, seizure, closure, OPEC cut); bearish = a deal, a "
          "reopening, an SPR release, an OPEC hike. is_confirmed = an official or military source confirms, "
          "not 'reportedly'/'sources say'.\nHeadline: ")


def _request_body(url: str, title: str, source: Optional[str], model: str) -> Dict[str, Any]:
    text = PROMPT + json.dumps(title) + (f"\nSource: {source}" if source else "")
    if "/api/generate" in url or "/api/chat" in url:        # Ollama
        if "/api/chat" in url:
            return {"model": model, "messages": [{"role": "user", "content": text}], "format": "json",
                    "stream": False, "options": {"temperature": 0}}
        return {"model": model, "prompt": text, "format": "json", "stream": False, "options": {"temperature": 0}}
    return {"model": model, "messages": [{"role": "user", "content": text}], "temperature": 0,
            "response_format": {"type": "json_object"}, "max_tokens": 120}


def _extract_text(url: str, data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    if "/api/generate" in url:
        return data.get("response")
    if "/api/chat" in url:
        return (data.get("message") or {}).get("content")
    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return (choices[0].get("message") or {}).get("content") or choices[0].get("text")
    return None


async def model_score(title: str, *, source: Optional[str] = None, url: Optional[str] = None,
                      timeout_s: Optional[float] = None, model: Optional[str] = None,
                      post: Any = None) -> Optional[Dict[str, Any]]:
    """The local model's SCHEMA-shaped verdict, or None (no URL, a non-local
    URL, a timeout, a bad answer). Never raises. `post(url, body, timeout_s)
    -> parsed JSON` is injectable for the tests; the default is httpx."""
    url = url or scorer_url()
    if not url:
        return None
    if not is_local_url(url):
        logger.warning("[event_score] %s is not a local host; refusing (no hosted LLM APIs)", SCORER_URL_ENV)
        return None
    model = model or os.environ.get(SCORER_MODEL_ENV) or DEFAULT_MODEL
    try:
        timeout_s = float(timeout_s if timeout_s is not None else os.environ.get(SCORER_TIMEOUT_ENV) or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout_s = DEFAULT_TIMEOUT_S
    body = _request_body(url, title, source, model)
    try:
        if post is None:
            import httpx
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
        else:
            data = await asyncio.wait_for(post(url, body, timeout_s), timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 - every failure is a fallback
        logger.warning("[event_score] model call failed: %s", f"{type(exc).__name__}: {exc}"[:160])
        return None
    text = _extract_text(url, data)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            parsed = json.loads(m.group(0))
        except ValueError:
            return None
    out = validate_score(parsed)
    if out:
        out["scorer"] = "model"
        out["rationale"] = f"model {model}"
    return out


async def score_event(title: str, *, source: Optional[str] = None, sentiment: Optional[str] = None,
                      sentiment_confidence: Optional[float] = None, stage1_label: Optional[str] = None,
                      entities: Optional[Iterable[Dict[str, Any]]] = None, url: Optional[str] = None,
                      post: Any = None) -> Dict[str, Any]:
    """rule_score, replaced by the local model's verdict when EVENT_SCORER_URL
    is set and the model answers valid JSON in time. Never raises."""
    rule = rule_score(title, source=source, sentiment=sentiment, sentiment_confidence=sentiment_confidence,
                      stage1_label=stage1_label, entities=entities)
    if not (url or scorer_url()):
        return rule
    try:
        m = await model_score(title, source=source, url=url, post=post)
    except Exception:  # noqa: BLE001
        m = None
    if m is None:
        rule["rationale"] = "model unavailable, rule fallback; " + rule["rationale"]
        return rule
    m["rule"] = {k: rule[k] for k in ("event_type", "expected_oil_direction", "confidence_score", "is_confirmed")}
    return m


def score_event_sync(title: str, timeout_s: float = DEFAULT_TIMEOUT_S + 2, **kw: Any) -> Dict[str, Any]:
    """score_event from synchronous code (the watcher's pass). Runs its own
    loop in a worker thread so a caller inside a running loop still works;
    a timeout falls back to the rule scorer."""
    import concurrent.futures
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False
    if not running:
        try:
            return asyncio.run(asyncio.wait_for(score_event(title, **kw), timeout=timeout_s))
        except Exception:  # noqa: BLE001
            return rule_score(title, **{k: v for k, v in kw.items() if k not in ("url", "post")})
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(lambda: asyncio.run(score_event(title, **kw)))
        try:
            return fut.result(timeout=timeout_s)
        except Exception:  # noqa: BLE001
            return rule_score(title, **{k: v for k, v in kw.items() if k not in ("url", "post")})


__all__ = ["DIRECTIONS", "EVENT_TYPES", "SCHEMA", "SCORER_URL_ENV", "event_type_of", "first_cue_direction", "is_local_url",
           "model_score", "rule_score", "score_event", "score_event_sync", "scorer_url", "validate_score"]
