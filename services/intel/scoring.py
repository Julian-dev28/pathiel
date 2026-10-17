"""Deterministic relevance + stance scoring. No model calls, no randomness —
same headline in, same score out, forever (see CLAUDE.md "latent vs
deterministic space": this is squarely deterministic, so it is a script,
not a prompt).

relevance() combines three gates multiplicatively, not additively:

    relevance = match(title, queries) * recency(age) * tier(source)

Multiplicative, not a weighted sum, on purpose. An additive formula with a
big fixed "any match at all" floor lets a PERFECT keyword match from a junk
source still clear the desk's `min_headline_relevance` bar — and that is a
real, observed failure mode, not a hypothetical one: Robinhood's templated
"BTC price on Oct 16, 2026 at 11pm EDT — Crypto Prediction Market" posts and
Moomoo's auto-translated "$Bitcoin (BTC.CC)$ stays on the Bitcoin
blockchain..." feed items both keyword-match a bitcoin query PERFECTLY
(they contain "BTC"/"Bitcoin" verbatim) while carrying zero news content.
Multiplying by a tier weight that is allowed to go as low as 0.2 for named
template/noise sources is what actually suppresses them; a +0.15-weighted
additive term does not move enough. Captured real examples backing this are
in services/intel/tests/fixtures/eval_labels_btc.json (see the `source`
field on the rows labeled `relevant: false`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, FrozenSet, Iterable, Optional

from contracts.strategies.models import Stance
from services.intel.textnorm import normalize_title, title_tokens

# ── source tier ──────────────────────────────────────────────────────────
# Weight by how much a desk trader should trust the byline, not by raw
# traffic. Wire services and the primary-source agencies (EIA/IEA/OPEC
# publish the numbers everyone else's "news" repeats) sit at the top.
_TIER1 = {
    "reuters", "bloomberg", "wsj", "wall street journal", "cnbc", "ap",
    "ap news", "associated press", "bbc", "financial times", "ft",
    "eia", "iea", "opec", "platts", "s&p global", "dow jones", "cftc",
    "federal register", "treasury", "ofac",
}
_TIER2 = {
    "yahoo finance", "the guardian", "guardian", "new york times", "nyt",
    "nbc news", "the hill", "cbs news", "abc news", "axios", "politico",
    "investopedia", "coindesk", "cointelegraph", "decrypt", "the block",
    "barron's", "barrons", "forbes", "business insider", "marketwatch",
    "oilprice.com", "oilprice", "goldman sachs", "bitcoin magazine",
    "the motley fool", "motley fool", "24/7 wall st", "24/7 wall st.",
    "fortune", "nerdwallet", "investing.com",
}
# Named, specific, low-signal-despite-perfect-keyword-match sources (see
# module docstring). Pattern-matched on a substring of the lowercased
# source field, deliberately short-listed and commented — this is NOT a
# general "unknown source" penalty, which is what tier3 below already is.
_TIER_JUNK = {"robinhood", "moomoo"}

_TIER1_W, _TIER2_W, _TIER3_W, _JUNK_W = 1.0, 0.85, 0.65, 0.2


def source_tier_weight(source: Optional[str]) -> float:
    if not source:
        return _TIER3_W
    s = source.strip().lower()
    if any(j in s for j in _TIER_JUNK):
        return _JUNK_W
    if s in _TIER1:
        return _TIER1_W
    if s in _TIER2:
        return _TIER2_W
    return _TIER3_W


# ── keyword / phrase match ──────────────────────────────────────────────

def _phrase_match(phrase: str, title_norm: str, title_toks: FrozenSet[str]) -> float:
    p_norm = normalize_title(phrase)
    if not p_norm:
        return 0.0
    p_toks = frozenset(p_norm.split())
    if len(p_toks) == 1:
        # Single-token phrases are usually tickers/labels ("WTI", "BTC",
        # "CL"). Require exact token membership, NOT substring-of-title —
        # a 2-letter ticker like "CL" is a substring of dozens of ordinary
        # words ("circle", "clean", "include") and a raw `in` check would
        # false-positive constantly.
        return 1.0 if next(iter(p_toks)) in title_toks else 0.0
    if p_norm in title_norm:
        return 1.0
    inter = len(p_toks & title_toks)
    return inter / len(p_toks) if p_toks else 0.0


def match_score(title: str, phrases: Iterable[str]) -> float:
    """Best match across every query phrase / instrument label / coin."""
    title_norm = normalize_title(title)
    title_toks = title_tokens(title)
    best = 0.0
    for phrase in phrases:
        if not phrase:
            continue
        best = max(best, _phrase_match(phrase, title_norm, title_toks))
        if best >= 1.0:
            break
    return best


def recency_weight(published_at: Optional[datetime], now: datetime,
                    half_life_hours: float = 36.0) -> float:
    """Floor 0.5 so an old-but-on-topic headline is discounted, not erased —
    only `match_score` is allowed to zero a headline out entirely. Unknown
    publish time gets the floor (treated as fully stale, not fully fresh;
    an undated headline earns its relevance from topic match alone)."""
    if published_at is None:
        return 0.5
    pub = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - pub).total_seconds() / 3600.0)
    decay = 0.5 ** (age_hours / half_life_hours)
    return 0.5 + 0.5 * decay


def relevance(title: str, *, queries: Iterable[str], source: Optional[str],
               published_at: Optional[datetime], now: datetime,
               half_life_hours: float = 36.0) -> float:
    m = match_score(title, queries)
    if m <= 0.0:
        return 0.0
    r = recency_weight(published_at, now, half_life_hours)
    t = source_tier_weight(source)
    return round(min(1.0, max(0.0, m * r * t)), 4)


# ── stance lexicon ───────────────────────────────────────────────────────
# Per-desk bull/bear word lists. Not a DeskSpec field (the contract is
# owned by desk-core; this stays a services/intel-local config) — keyed by
# desk_id with a generic financial-sentiment fallback for any desk that
# doesn't get its own list yet.
#
# "bull"/"bear" means "this word, in this headline, implies the PRICE goes
# up/down" — for oil, a supply cut is bullish for price; for a bond, the
# same word would mean something else entirely, which is exactly why this
# is per-desk and not one global list.
_LEXICONS: Dict[str, Dict[str, FrozenSet[str]]] = {
    "oil": {
        "bull": frozenset({
            "outage", "outages", "attack", "attacks", "attacked", "attacking",
            "draw", "draws", "drawdown", "sanction", "sanctions", "sanctioned",
            "strike", "strikes", "struck", "shortage", "shortages", "disruption",
            "disruptions", "blockade", "blockades",
        }),
        "bear": frozenset({
            "release", "releases", "released", "build", "builds", "ceasefire",
            "ceasefires", "deal", "deals", "restart", "restarts", "restarted",
            "surplus", "glut", "truce", "reserve", "reserves",
        }),
    },
    "btc": {
        "bull": frozenset({
            "inflow", "inflows", "adoption", "halving", "rally", "rallies",
            "surge", "surges", "approval", "approved", "accumulation",
            "buying", "bullish", "breakout",
        }),
        "bear": frozenset({
            "hack", "hacked", "ban", "banned", "outflow", "outflows",
            "liquidation", "liquidations", "crash", "crashes", "selloff",
            "sell-off", "lawsuit", "bearish", "exploit", "exploited",
        }),
    },
    "_default": {
        "bull": frozenset({
            "surge", "surges", "rally", "rallies", "soar", "soars", "jump",
            "jumps", "gain", "gains", "beat", "beats", "upgrade", "upgraded",
        }),
        "bear": frozenset({
            "plunge", "plunges", "crash", "crashes", "miss", "misses",
            "slump", "slumps", "drop", "drops", "fall", "falls", "sinks",
            "downgrade", "downgraded",
        }),
    },
}


def stance(title: str, desk_id: str) -> Stance:
    lex = _LEXICONS.get(desk_id, _LEXICONS["_default"])
    toks = title_tokens(title)
    bulls = len(toks & lex["bull"])
    bears = len(toks & lex["bear"])
    if bulls == 0 and bears == 0:
        return "unknown"
    if bulls > bears:
        return "bull"
    if bears > bulls:
        return "bear"
    return "neutral"
