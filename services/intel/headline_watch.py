"""Surprise headlines: catch the market-moving news no calendar can date.

Operator, 2026-11-05: "FIX THEM" -- a US-Iran deal, a strike or a Trump post
has no date, and the thesis only saw news at its next rewrite (daily, after
a scheduled release, or once price had already moved 1.5%).

scripts/trigger_watch.py runs this every minute, no LLM:
  - news: targeted Google News RSS + oilprice.com every NEWS_POLL_S; a NEW
    headline (not seen before, published in the last MAX_AGE_MIN) whose
    title matches a desk's PATTERNS is an alert.
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


_TRUSTED_RX = re.compile(r"\b(" + "|".join(re.escape(t) for t in TRUSTED_SOURCES) + r")\b", re.I)


def trusted_source(source: Optional[str]) -> bool:
    """True for a wire or major outlet (TRUSTED_SOURCES as whole words: "npr"
    must not pass "Townpress Newspaper"), or when no source is given."""
    return True if not source else bool(_TRUSTED_RX.search(source))


def news_alerts(group: str, items: Iterable[Dict[str, Any]], seen: set, now_ms: int) -> List[Dict[str, Any]]:
    """New matching headlines from trusted sources, feed order. Adds every
    item's key to `seen` (matching or not) so a headline alerts once. Items:
    parse_rss_items rows ({title, url, source, published_at})."""
    out = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        k = _key(title)
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
           "PATTERNS", "PRICE_REARM_MIN", "PRICE_SHOCK", "PRICE_WINDOW_MIN", "PRICE_WRAP", "TRUSTED_SOURCES",
           "alerts_for", "headline_blackout", "latest_for", "load_state", "match_headline", "news_alerts",
           "odds_alerts", "price_shock", "read_alerts", "record", "save_state", "trusted_source"]
