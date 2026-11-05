"""Surprise headlines: catch the market-moving news no calendar can date.

Operator, 2026-11-05: "FIX THEM" -- a US-Iran deal, a strike or a Trump post
has no date, and the thesis only saw news at its next rewrite (daily, after
a scheduled release, or once price had already moved 1.5%).

scripts/trigger_watch.py runs this every minute, no LLM:
  - news: targeted Google News RSS + oilprice.com every NEWS_POLL_S; a NEW
    headline (not seen before, published in the last MAX_AGE_MIN) whose
    title matches a desk's PATTERNS is an alert. The official whitehouse.gov
    news feed (sources/social.py; state.gov refuses our User-Agent) rides
    the same poll and the same PATTERNS.
  - posts: Trump's Truth Social posts via the trumpstruth.org archive every
    POSTS_POLL_S; a NEW post (by id) inside MAX_AGE_MIN of its original time
    that matches POST_PATTERNS is an alert. Truth Social itself and X have
    no feed we may read (sources/social.py has the check).
  - odds: Polymarket markets matching the desk's ODDS_TERMS; a move of
    ODDS_JUMP or more since the last poll is an alert.
  - price shock: the mid moving PRICE_SHOCK or more within PRICE_WINDOW_MIN
    is an alert (the headline we have not read yet).
An alert is recorded in ALERTS_PATH and then:
  1. the watcher wakes that desk's tick at once, position or not;
  2. scripts/thesis_due.py makes the thesis due ("headline: ...") past the
     one-an-hour gap (HEADLINE_MIN_GAP_MIN instead);
  3. services/strategies/event_blackout.event_blackout refuses NEW entries on
     the desk's coins for HEADLINE_BLACKOUT_MIN, so nothing opens on a stale
     thesis while the rewrite runs.
Interpreting the headline (bullish? does it break the thesis?) stays latent:
the rewrite and the tick agent judge it. This module only notices.

Pure functions plus a small JSON file; the fetching lives in trigger_watch.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ALERTS_PATH = os.environ.get("PATHIEL_HEADLINE_ALERTS") or "/tmp/trigger-watch/headline_alerts.json"
STATE_PATH = os.environ.get("PATHIEL_HEADLINE_STATE") or "/tmp/trigger-watch/headline_state.json"

NEWS_POLL_S = 120
POSTS_POLL_S = 60                  # Trump's posts (the trumpstruth.org archive): every pass
MAX_AGE_MIN = 45
ODDS_JUMP = 0.08
PRICE_WINDOW_MIN = 15
PRICE_SHOCK = {"xyz:BRENTOIL": 0.010, "xyz:CL": 0.010, "xyz:SILVER": 0.012}
HEADLINE_BLACKOUT_MIN = 30
HEADLINE_MIN_GAP_MIN = 30          # a headline rewrite still waits this long after the last thesis
MAX_ALERTS_PER_DAY = 6             # per group; caps rewrite cost (~$1 each)
PRICE_REARM_MIN = PRICE_WINDOW_MIN  # one price-shock alert per coin per window, not one a minute
KEEP_ALERTS = 100
MIN_MS = 60_000

# Google News queries per group (when:1h keeps them fresh).
NEWS_QUERIES = {
    "oil": ["Iran OR Hormuz OR Tehran oil when:1h", "OPEC OR \"crude oil\" OR Brent when:1h",
            "Trump Iran OR Trump oil OR \"strategic petroleum reserve\" when:1h"],
    "silver": ["Powell OR \"Federal Reserve\" OR \"rate cut\" when:1h", "silver price OR precious metals when:1h"],
}

_IRAN = r"(iran|iranian|tehran|hormuz|persian gulf|khamenei|araghchi|pezeshkian)"
# Event words only. "war" and "sanction" are out on purpose: with a war on,
# every market wrap says "Iran war", and sanctions are a slow process (live
# smoke 2026-11-05: both matched routine stories).
_ACT = (r"(deal|ceasefire|cease-fire|truce|agreement|accord|peace|talks|negotiat|strike|strikes|attack|"
        r"missile|drone|seiz|blockade|reopen|closed|closure|escalat|retaliat)")
_SITE = r"(refinery|pipeline|tanker|terminal|oilfield|oil field|port)"
_DAMAGE = r"(attack|explosion|blast|fire|fires|outage|shut|halt)"
PATTERNS: Dict[str, List[Tuple[str, re.Pattern]]] = {
    "oil": [
        ("iran/hormuz", re.compile(rf"\b{_IRAN}\w*\b.*\b{_ACT}\w*|\b{_ACT}\w*\b.*\b{_IRAN}\w*", re.I)),
        ("opec", re.compile(r"\bopec\+?\b.*\b(cut|hike|increase|boost|emergency|surprise|output|quota)\w*", re.I)),
        ("trump/oil", re.compile(r"\btrump\b.*\b(iran|oil|opec|tariff|saudi|spr|strategic petroleum)\w*", re.I)),
        ("spr", re.compile(r"\b(strategic petroleum reserve|spr release)\b", re.I)),
        ("supply shock", re.compile(rf"\b{_SITE}s?\b.*\b{_DAMAGE}\w*|\b{_DAMAGE}\w*\b.*\b{_SITE}s?\b", re.I)),
    ],
    "silver": [
        ("fed", re.compile(r"\b(powell|federal reserve|fomc|the fed)\b.*\b(emergency|cut|hike|surprise|resign|fire)\w*", re.I)),
        ("tariff/metals", re.compile(r"\b(tariff|export ban|export control)\w*\b.*\b(silver|metals?|copper)\b", re.I)),
        ("trump/fed", re.compile(r"\btrump\b.*\b(fed|powell|rates?)\b", re.I)),
    ],
}
ODDS_TERMS = {"oil": re.compile(r"(iran|hormuz|ceasefire|opec|oil)", re.I),
              "silver": re.compile(r"(fed|rate cut|powell|recession)", re.I)}

# Trump's own posts (operator, 2026-11-05: "A Truth Social / X feed for the
# headline watcher"). A post is short and not headline-shaped: "Oil Prices
# are DROPPING LIKE A ROCK ... there will not be a War with Iran" has no
# verb a wire would use, so PATTERNS miss it. Tuned on 540 real posts of
# 2026-11-01..05 (fixtures/trumpstruth_feed.xml keeps 12): the 538 that are
# endorsements, election posts and TV complaints must not match. "deal",
# "strike", "attack", "sanction", "tariff", "war" need a counterparty or a
# trade partner next to them ("KENTUCKY CLOSING SOON. A BIG DEAL!" is not
# news); "rates" needs "interest" or "cut"/"hike" ("crime rates" is not).
_POST_OIL = (r"(oil prices?|price of oil|oil (is|was|has|went|going|dropping|falling|rising|surging|down|up)|"
             r"oil (production|output|supply|exports?|imports?|embargo|sanctions?|tankers?|fields?)|barrels?|crude|"
             r"gasoline|opec\+?|saudi\w*|aramco|strategic petroleum reserve|spr|energy prices|drill,? baby)")
_POST_GEO_ACT = (r"(ceasefire|cease-fire|truce|peace (deal|agreement|talks|plan)|deal|agreement|settlement|strikes?|"
                 r"struck|attack\w*|bomb\w*|sanction\w*|blockade|embargo|invasion|invade\w*|troops|warships?|"
                 r"war(?! (veteran|hero|college)))")
_POST_GEO_WHO = (r"(israel\w*|netanyahu|russia\w*|putin|ukrain\w*|zelensk\w*|venezuela\w*|maduro|china|chinese|"
                 r"xi jinping|president xi|houthi\w*|yemen|hamas|gaza|hezbollah|lebanon|iraq\w*|syria\w*|qatar|"
                 r"kuwait|emirates|uae|middle east)")
_POST_TARIFF_WHO = r"(china|chinese|india\w*|russia\w*|canad\w*|mexic\w*|europe\w*|eu|oil|crude|energy)"
_POST_FED = (r"(the fed|federal reserve|fomc|interest rates?|rate cuts?|rate hikes?|(cut|lower|raise) (the )?rates|"
             r"(jerome|chairman|\"?too late\"?) powell)")
_POST_FED_NAME = r"(powell|hassett|warsh)"
_POST_FED_CTX = r"(fed|federal reserve|rates?|interest|too late|jerome|chairman|inflation)"
POST_PATTERNS: Dict[str, List[Tuple[str, re.Pattern]]] = {
    "oil": [
        ("trump:iran", re.compile(rf"\b{_IRAN}\w*\b", re.I)),
        ("trump:oil", re.compile(rf"\b{_POST_OIL}\b", re.I)),
        ("trump:geo", re.compile(rf"\b{_POST_GEO_ACT}\b.*\b{_POST_GEO_WHO}\b|\b{_POST_GEO_WHO}\b.*\b{_POST_GEO_ACT}\b",
                                 re.I | re.S)),
        ("trump:tariff", re.compile(rf"\btariff\w*\b.*\b{_POST_TARIFF_WHO}\b|\b{_POST_TARIFF_WHO}\b.*\btariff\w*\b",
                                    re.I | re.S)),
    ],
    "silver": [
        ("trump:fed", re.compile(rf"\b{_POST_FED}\b", re.I)),
        # a Fed name only with Fed context: "Tracey Powell for State Senate" is a candidate
        ("trump:fed", re.compile(rf"\b{_POST_FED_NAME}\b.*\b{_POST_FED_CTX}\b|\b{_POST_FED_CTX}\b.*\b{_POST_FED_NAME}\b",
                                 re.I | re.S)),
        ("trump:tariff/metals", re.compile(r"\btariff\w*\b.*\b(silver|gold|copper|metals?)\b|"
                                           r"\b(silver|gold|copper|metals?)\b.*\btariff\w*\b", re.I | re.S)),
    ],
}
# An archived image/video post with no words, or a bare link: nothing to read.
_POST_EMPTY = re.compile(r"^(\s*(view (image|video)|rt:?|rt @ \w+|reposted donald j\. trump|quoted donald j\. trump|"
                         r"https?://\S*|\S*truthsocial\.com\S*)\s*)*$", re.I)
# Endorsements and get-out-the-vote lists: the bulk of the feed, never market
# news, and the text that mentions "Oil & Gas", "Iraq War Veteran", "STEEL
# TARIFFS" or a candidate named Powell (22 of 503 real posts matched before
# this guard; 0 after).
_POST_ROUTINE = re.compile(r"(my great honor to endorse|complete and total endorsement|get out and vote for|"
                           r"make a plan to (get out and )?vote|is running (for|to represent|to be the next|to become)|"
                           r"for (u\.s\. )?(senate|congress|governor|lieutenant governor|attorney general|"
                           r"secretary of state) \(|\bvote for\b.*\bfor (u\.s\. )?(senate|congress|governor)\b)", re.I | re.S)

# A price wrap ("Oil falls as ...", "Brent edges higher on ...") reports the
# market's reaction, which price_shock already catches; the news alert is for
# the event itself, before the move. Never a news alert.
PRICE_WRAP = re.compile(
    r"^[\w'. ]{0,24}?\b(oil|brent|crude|wti|gas|lng|gold|silver|stocks?|shares|dollar|euro|rand|yen|bonds?|"
    r"treasur\w+|markets?|wall street|futures|equities)\b( oil| prices?| futures| markets?| shares| stocks)?\s+"
    r"(up|down|fall|falls|fell|rise|rises|rose|slip|slips|climb|climbs|edge|edges|steady|steadies|gain|gains|"
    r"drop|drops|retreat|retreats|bounce|bounces|rally|rallies|jump|jumps|slide|slides|tumble|tumbles|settle|"
    r"settles|dip|dips|hold|holds|ease|eases|extend|extends|surge|surges|plunge|plunges|rebound|rebounds|"
    r"waver|wavers|mixed|flat|little changed)\b", re.I)

# Google News mixes wires with content farms. A news alert needs one of these
# (substring of the lower-cased source); every other item is still learned
# (dedup) and still counts as odds/price context. No source given: allowed.
TRUSTED_SOURCES = (
    "reuters", "bloomberg", "associated press", "ap news", "apnews", "afp", "cnbc", "financial times", "ft.com",
    "wall street journal", "wsj", "axios", "al jazeera", "bbc", "new york times", "nytimes", "washington post",
    "the guardian", "oilprice", "politico", "cnn", "fox news", "fox business", "sky news", "the hill",
    "times of israel", "iran international", "nbc", "abc news", "cbs", "the times", "telegraph", "yahoo",
    "marketwatch", "investing.com", "rigzone", "s&p global", "platts", "argus", "upi", "france 24", "dw",
    "nikkei", "economist", "semafor", "independent", "newsweek", "the national", "arab news", "al arabiya",
    "middle east eye", "jerusalem post", "haaretz", "barron", "forbes", "business insider", "the street",
    "seeking alpha", "zerohedge", "white house", "truth social", "x.com", "twitter", "kyodo", "tass", "xinhua",
    "fxstreet", "business standard", "business times", "iranwire", "marketscreener", "eia",
    "energy information administration", "al-monitor", "al monitor", "amwaj", "nournews", "isna", "fars",
    "press tv", "irna", "tasnim", "mehr news", "anadolu", "the national news", "euronews", "la times", "npr",
    "pbs", "usa today", "new york post", "daily mail", "the sun", "mirror", "express", "bne", "energy intel",
    "energyintel", "quantum commodity", "montel", "hellenic shipping", "lloyd's list", "tradewinds", "splash247",
    "maritime executive", "gcaptain", "spglobal", "ice.com", "cme", "world oil", "hart energy", "ogj",
    "oil & gas journal", "upstream", "offshore", "natural gas intel", "kitco", "mining.com", "bullionvault",
    # the official feeds and Trump's posts (sources/social.py), since 2026-11-05
    "whitehouse.gov", "state.gov", "truthsocial:realdonaldtrump",
)


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def _key(text: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", " ", text.lower()).strip().encode()).hexdigest()[:16]


def _alerts_path(path: Optional[str]) -> str:
    """Resolved at call time so a test (or the watcher) can point ALERTS_PATH
    at a temp file after import; a default bound at def time could not."""
    return path or ALERTS_PATH


def match_headline(group: str, title: str) -> Optional[str]:
    """The first pattern label `title` matches for `group`, else None. A price
    wrap never matches (PRICE_WRAP)."""
    title = title or ""
    if PRICE_WRAP.search(title):
        return None
    for label, rx in PATTERNS.get(group, []):
        if rx.search(title):
            return label
    return None


def match_post(group: str, text: str) -> Optional[str]:
    """The first POST_PATTERNS label a Trump post `text` matches for `group`,
    else None. An image/video/link-only post never matches."""
    text = (text or "").strip()
    if not text or _POST_EMPTY.match(text) or _POST_ROUTINE.search(text):
        return None
    for label, rx in POST_PATTERNS.get(group, []):
        if rx.search(text):
            return label
    return None


_TRUSTED_RX = re.compile(r"\b(" + "|".join(re.escape(t) for t in TRUSTED_SOURCES) + r")\b", re.I)


def trusted_source(source: Optional[str]) -> bool:
    """True for a wire or major outlet (TRUSTED_SOURCES as whole words: "npr"
    must not pass "Townpress Newspaper"), or when no source is given."""
    return True if not source else bool(_TRUSTED_RX.search(source))


def news_alerts(group: str, items: Iterable[Dict[str, Any]], seen: set, now_ms: int, *,
                shared: bool = False) -> List[Dict[str, Any]]:
    """New matching headlines from trusted sources, feed order. Adds every
    item's key to `seen` (matching or not) so a headline alerts once. Items:
    parse_rss_items rows ({title, url, source, published_at}). `shared`: the
    same items go to every group (the official feeds), so the key carries the
    group and the item id (URL): each desk judges the item once, and an edited
    title does not alert twice."""
    out = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        k = f"{group}:{_key(str(it.get('id') or title))}" if shared else _key(title)
        if k in seen:
            continue
        seen.add(k)
        pub = it.get("published_at")
        pub_ms = int(pub.timestamp() * 1000) if isinstance(pub, datetime) else None
        if pub_ms is not None and now_ms - pub_ms > MAX_AGE_MIN * MIN_MS:
            continue
        if not trusted_source(it.get("source")):
            continue
        label = match_headline(group, title)
        if label:
            out.append({"group": group, "kind": "news", "label": label, "title": title[:200],
                        "source": it.get("source"), "url": it.get("url"), "at_ms": now_ms,
                        "published_ms": pub_ms})
    return out


def post_alerts(group: str, posts: Iterable[Dict[str, Any]], seen: set, now_ms: int) -> List[Dict[str, Any]]:
    """New Trump posts (sources/social.parse_trumpstruth rows: {id, url, text,
    source, published_at, post_type}) that match POST_PATTERNS for `group`,
    feed order. Dedup by post id per group (an edited post keeps its id and
    does not alert twice); every post's key goes to `seen`. A post older than
    MAX_AGE_MIN by its ORIGINAL time, or with no time, never alerts: the
    archive catches up on old posts in bursts."""
    out = []
    for p in posts:
        text = (p.get("text") or "").strip()
        pid = str(p.get("id") or p.get("url") or "")
        if not pid and not text:
            continue
        k = f"{group}:post:{pid or _key(text)}"
        if k in seen:
            continue
        seen.add(k)
        pub = p.get("published_at")
        pub_ms = int(pub.timestamp() * 1000) if isinstance(pub, datetime) else None
        if pub_ms is None or now_ms - pub_ms > MAX_AGE_MIN * MIN_MS:
            continue
        label = match_post(group, text)
        if label:
            out.append({"group": group, "kind": "post", "label": label, "title": text[:200],
                        "source": p.get("source"), "url": p.get("url"), "at_ms": now_ms,
                        "published_ms": pub_ms, "post_id": pid, "post_type": p.get("post_type")})
    return out


def odds_alerts(group: str, odds: Iterable[Dict[str, Any]], prev: Dict[str, float],
                now_ms: int) -> List[Dict[str, Any]]:
    """Markets whose probability moved ODDS_JUMP or more since `prev` (id ->
    probability; updated in place). odds rows: {id, question, probability, url}."""
    out = []
    rx = ODDS_TERMS.get(group)
    for o in odds:
        q = o.get("question") or ""
        if rx is None or not rx.search(q) or o.get("probability") is None:
            continue
        mid = o.get("id")
        if mid is None:
            continue
        p = float(o["probability"])
        old = prev.get(mid)
        prev[mid] = p
        if old is not None and abs(p - old) >= ODDS_JUMP:
            out.append({"group": group, "kind": "odds", "label": "odds jump",
                        "title": f"{q[:150]}: {old:.0%} -> {p:.0%}", "url": o.get("url"), "at_ms": now_ms})
    return out


def price_shock(coin: str, history: Sequence[Tuple[int, float]], now_ms: int, mid: float) -> Optional[Dict[str, Any]]:
    """An alert when `mid` sits PRICE_SHOCK or more from any mid in the last
    PRICE_WINDOW_MIN of `history` ((ms, mid), oldest first). None otherwise."""
    thr = PRICE_SHOCK.get(coin)
    if not thr or not mid:
        return None
    window = [p for t, p in history if now_ms - t <= PRICE_WINDOW_MIN * MIN_MS]
    if not window:
        return None
    ref = max(window, key=lambda p: abs(mid - p))
    move = (mid - ref) / ref
    if abs(move) >= thr:
        return {"coin": coin, "group": _group_of(coin), "kind": "price", "label": "price shock",
                "title": f"{coin} {move:+.2%} in {PRICE_WINDOW_MIN} min ({ref:g} -> {mid:g})", "at_ms": now_ms,
                "move": round(move, 5)}
    return None


# ── the alert file ──────────────────────────────────────────────────────────

def read_alerts(path: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        with open(_alerts_path(path)) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def record(alerts: Sequence[Dict[str, Any]], path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Append `alerts` (capped at MAX_ALERTS_PER_DAY per group per UTC day);
    returns the ones actually kept."""
    path = _alerts_path(path)
    cur = read_alerts(path)
    kept = []
    for a in alerts:
        day = _utc(a["at_ms"])[:10]
        n = sum(1 for x in cur if x.get("group") == a.get("group") and _utc(x["at_ms"])[:10] == day)
        if n >= MAX_ALERTS_PER_DAY:
            continue
        cur.append(a)
        kept.append(a)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cur[-KEEP_ALERTS:], fh)
    os.replace(tmp, path)
    return kept


def _group_of(coin: str) -> Optional[str]:
    from services.intel.scheduled_events import COIN_GROUPS
    return next((g for g, coins in COIN_GROUPS.items() if coin in coins), None)


def latest_for(coin: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The newest alert that concerns `coin` (its group, or the coin itself)."""
    g = _group_of(coin)
    hits = [a for a in read_alerts(path) if a.get("coin") == coin or (g and a.get("group") == g)]
    return max(hits, key=lambda a: a["at_ms"]) if hits else None


def alerts_for(coins: Iterable[str], alerts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure. The alerts that concern any of `coins` (by coin or by group),
    oldest first. scripts/thesis_due.py filters read_alerts() with it."""
    coins = set(coins)
    groups = {g for c in coins for g in [_group_of(c)] if g}
    hits = [a for a in alerts if a.get("coin") in coins or a.get("group") in groups]
    return sorted(hits, key=lambda a: int(a.get("at_ms") or 0))


def headline_blackout(coin: str, at_ms: int, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Active for HEADLINE_BLACKOUT_MIN after the newest alert on `coin`."""
    a = latest_for(coin, path)
    if not a:
        return None
    end = int(a["at_ms"]) + HEADLINE_BLACKOUT_MIN * MIN_MS
    if int(a["at_ms"]) <= at_ms < end:
        return {"coin": coin, "event": a["title"], "kind": "headline", "start_ms": int(a["at_ms"]),
                "end_ms": end, "until_utc": _utc(end), "alert": a,
                "reason": f"Headline blackout: no new entries until {_utc(end)} "
                          f"({a['label']}: {a['title'][:90]}); the thesis rewrites first"}
    return None


def load_state(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        with open(path or STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_state(state: Dict[str, Any], path: Optional[str] = None) -> None:
    path = path or STATE_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    state = dict(state, seen=list(state.get("seen", []))[-2000:])
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


__all__ = ["ALERTS_PATH", "HEADLINE_BLACKOUT_MIN", "HEADLINE_MIN_GAP_MIN", "NEWS_POLL_S", "NEWS_QUERIES",
           "PATTERNS", "POSTS_POLL_S", "POST_PATTERNS", "PRICE_REARM_MIN", "PRICE_SHOCK", "PRICE_WINDOW_MIN",
           "PRICE_WRAP", "TRUSTED_SOURCES", "alerts_for", "headline_blackout", "latest_for", "load_state",
           "match_headline", "match_post", "news_alerts", "odds_alerts", "post_alerts", "price_shock",
           "read_alerts", "record", "save_state", "trusted_source"]
