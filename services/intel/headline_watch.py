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
     thesis while the rewrite runs. Since 2026-11-06 (operator, 2026-11-05:
     "we SHOULDNT PAUSE FOR BULLISH HEADLINES") the hold is one-sided: every
     alert carries a "direction" (+1 bullish for the group's coins, -1
     bearish, 0 unknown; the word lists under DIRECTION below), and an alert
     whose direction supports the trade side does not hold that side's
     entries (headline_blackout(coin, at_ms, side=...)). An alert against the
     side, or of unknown direction, still holds. Steps 1 and 2 ignore the
     direction: every alert wakes the tick and makes the thesis due.
What the headline means for the thesis (does it break it?) stays latent: the
rewrite and the tick agent judge it. This module notices, and reads the
headline's sign off fixed word lists; a title the lists cannot read is 0 and
holds, the safe default.

Since 2026-11-17 (operator: recycled headlines burned the day's alert budget
on nothing): two more guards sit in `news_alerts`, both ahead of
`match_headline`.
  1. Age: a headline with no readable published time is DROPPED, not taken
     as fresh (see MAX_AGE_MIN below for the data this threshold and this
     rule are read from). 2026-11-17, "Rubio condemns Iran strikes on
     Bahrain, Manama demands halt at UN" root-caused here: Google News can
     re-serve a months-old reprint through a feed entry with no <pubDate> at
     all, and the old code took a missing date as "fresh" instead of
     dropping it.
  2. Dedup: `seen` only ever caught a byte-identical title, so the same wire
     story retitled by a second outlet slipped through as "new" -- see
     NEWS_DEDUP_MIN_OVERLAP below. `news_alerts(..., recent=state_list)`
     drops a title that is a near-duplicate (textnorm.is_near_duplicate) of
     one it already alerted on for the same group inside
     NEWS_DEDUP_WINDOW_MIN; `recent` is the caller's own persisted list
     (scripts/trigger_watch.py keeps it in the same headline_state.json
     `seen` already lives in, so it survives the restart every fire causes).
     This does NOT catch a genuinely different follow-up angle on the same
     underlying event (2026-11-17's CNN "dirty bomb" plot story and its
     3-hours-later "triggers emergency meetings" write-up share only 18% of
     their tokens -- the same ratio as two real, unrelated same-day
     headlines in the same log, so text overlap cannot tell them apart
     without risking a real escalation). That case is left to
     MAX_ALERTS_PER_DAY and the operator, not silently merged.

Pure functions plus a small JSON file; the fetching lives in trigger_watch.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from services.intel.textnorm import is_near_duplicate

ALERTS_PATH = os.environ.get("PATHIEL_HEADLINE_ALERTS") or "/tmp/trigger-watch/headline_alerts.json"
STATE_PATH = os.environ.get("PATHIEL_HEADLINE_STATE") or "/tmp/trigger-watch/headline_state.json"

NEWS_POLL_S = 120
POSTS_POLL_S = 60                  # Trump's posts (the trumpstruth.org archive): every pass
# 45 min, from the real fires that mattered: /tmp/trigger-watch/watch.log's
# 19 kind="news" headline fires of 2026-11-05..17 ranged 2.1 to 39.7 minutes
# old by their own published_at (the slowest: "WTI trades weakly below $85
# on US-Iran deal hopes", 39.7 min). 45 leaves ~5 minutes of margin above the
# slowest real fire without being loose enough to admit a stale one. A
# headline with NO readable published_at is dropped outright, never taken as
# fresh (news_alerts, below) -- Google News and oilprice.com sometimes
# re-serve a months-old reprint through an entry with no <pubDate> at all
# (2026-11-17: "Rubio condemns Iran strikes on Bahrain, Manama demands halt
# at UN" read as a months-old June story once traced; the real fires above
# all carry a published_at, so an undated item is categorically different,
# not just "old"). A streaming provider (`triggers=True`, Finlight) that
# omits its own publishDate still carries `received_at` -- OUR clock, set
# the instant the socket delivered it -- so a real-time push is judged on
# that instead of being dropped for the wire's missing metadata.
MAX_AGE_MIN = 45
# One story told by several outlets (or re-synced by Finlight and Google
# News within minutes of each other) must fire once, not once per retitling.
# `seen` (news_alerts) already drops a byte-identical title; it misses a
# paraphrase. 2026-11-13's real duplicate -- "Trump directs Vance to shift
# focus from Iran war to U.S. 'corruption and crime'" (NBC 7 San Diego) and
# "President Donald Trump Directs Vice President JD Vance to Shift Focus
# From Iran War to Crime" (CNBC TV18), 76 minutes apart -- share 71% of the
# smaller title's tokens (textnorm.is_near_duplicate's own ratio). The
# highest-overlap pair of genuinely DIFFERENT same-day Hormuz headlines in
# the same log (two distinct tanker incidents a day apart) sits at 57%.
# 0.65 sits between the two: loose enough to catch a same-story retitling,
# tight enough that two real, different escalations never collide (the
# 76-minute real gap sits well inside the 180-minute window below).
NEWS_DEDUP_MIN_OVERLAP = 0.65
NEWS_DEDUP_WINDOW_MIN = 180
ODDS_JUMP = 0.08
# An odds jump alerts only on a market with at least this much lifetime
# volume (2026-11-22). Overnight 11-21 two thin HYPE markets woke the desk and
# forced thesis rewrites: "Will Hyperliquid reach $76 in November?" ($146 of
# volume in total) went 19% -> 45%, and "$88 in November?" ($377) went 4% ->
# 27%, each on one small trade. Typical volume in the cached markets: oil
# median $40.8k, metals $18-40k, sp500 $10.1k, hype $1.3k, btc $0.7k. $5,000
# keeps oil's and the S&P's real markets and drops the ones a $10 bet can move.
# The thesis still reads every market; only the alert is gated. A row with no
# volume (a caller that does not pass it) alerts as before.
ODDS_ALERT_MIN_VOLUME_USD = 5_000.0
PRICE_WINDOW_MIN = 15
# Gold 0.7% (2026-11-05, the metals desk's primary): on xyz:GOLD's 15m bars
# of 2026-10-06..11-05 a 0.7% close-to-open move printed 4 times (1.0%: once),
# about the rate silver's 1.2% (6) and Brent's 1.0% (12) alert at; p99.5 of
# gold's 15m moves is 0.47%, so 0.7% is a real outlier, not noise.
# S&P 0.7% (2026-11-19, the sp500 desk): on xyz:SP500's 15m bars of
# 2026-09-28..11-19 a 0.7% close-to-open move printed 5 times in 52 days
# (2.9 a month; 0.6%: 9, 0.8%: 2), the rate gold's 0.7% alerts at; p99.5 of
# its 15m moves is 0.41%.
# HYPE 2.8% (2026-11-20, the candidate hype desk): a much louder coin than
# any of the above -- its own 15m bars of 2026-09-28..11-20 put p90 of the
# absolute close-to-open move at 0.61%, p99 at 1.40%, p99.5 at 1.71%. A
# threshold at gold/sp500's own 0.7% would fire on roughly 1 in 25 bars of
# HYPE (noise, not a headline); 2.8% is the level that alerts at the SAME
# rate they do, 5 times in 52 days, 2.9 a month.
PRICE_SHOCK = {"xyz:BRENTOIL": 0.010, "xyz:CL": 0.010, "xyz:SILVER": 0.012, "xyz:GOLD": 0.007,
               "xyz:SP500": 0.007, "HYPE": 0.028}
HEADLINE_BLACKOUT_MIN = 30
HEADLINE_MIN_GAP_MIN = 30          # a headline rewrite still waits this long after the last thesis
MAX_ALERTS_PER_DAY = 6             # per group; caps rewrite cost (~$1 each)
PRICE_REARM_MIN = PRICE_WINDOW_MIN  # one price-shock alert per coin per window, not one a minute

# Drift: a steady repricing the 15-minute shock misses (operator, 2026-11-05:
# "Brent crude jumps, settles up over $3 after tanker attacks" -- the climb
# ran +1.2% per 2h from 17:00 to 20:40Z, no 15-minute leg reached 1%, and the
# attack headline itself never reached a trusted feed in time). Thresholds sit
# near each coin's 95th percentile of 4h moves over the 60 days to 2026-11-05
# (xyz:BRENTOIL p90 1.56% p95 1.93%; xyz:GOLD p90 0.66% p95 0.90%); Brent's
# 1.75% catches that evening's +1.83%. One alert per coin per window.
DRIFT_WINDOW_MIN = 240
# The S&P's 4h moves: p95 0.60% over 208 days of 1h bars, 0.76% over the last
# 52 days of 15m bars (p99 1.10% / 1.37%); 0.75% sits at the recent p95.
# HYPE's 4h moves (1h bars, 2026-04-26..11-20): p90 2.69%, p95 3.57%, p99
# 5.72%; 3.5% sits at its own p95, the same methodology as the S&P's.
PRICE_DRIFT = {"xyz:BRENTOIL": 0.0175, "xyz:CL": 0.0175, "xyz:SILVER": 0.025, "xyz:GOLD": 0.009,
               "xyz:SP500": 0.0075, "HYPE": 0.035}
DRIFT_REARM_MIN = DRIFT_WINDOW_MIN
HISTORY_KEEP_MIN = DRIFT_WINDOW_MIN + 2   # minutes of mids the watcher keeps per coin
KEEP_ALERTS = 100
MIN_MS = 60_000

# Google News queries per group (when:1h keeps them fresh). The groups are
# scheduled_events.COIN_GROUPS: "oil" (Brent, WTI) and "metals" (gold, the
# desk's primary since 2026-11-05, and silver). The metals group was called
# "silver" until then; its Fed and tariff patterns carry over to gold, which
# trades the same rate risk, plus gold's own: central bank buying, the
# safe-haven bid on an Iran/Hormuz escalation, a yields or dollar shock.
NEWS_QUERIES = {
    "oil": ["Iran OR Hormuz OR Tehran oil when:1h", "OPEC OR \"crude oil\" OR Brent when:1h",
            "Trump Iran OR Trump oil OR \"strategic petroleum reserve\" when:1h"],
    "metals": ["Powell OR \"Federal Reserve\" OR \"rate cut\" when:1h",
               "gold price OR bullion OR \"central bank\" gold when:1h",
               "silver price OR precious metals OR tariff metals when:1h"],
    # the sp500 desk (2026-11-19): the Fed, tariffs and Washington, the megacaps
    "sp500": ["Powell OR \"Federal Reserve\" OR \"rate cut\" when:1h",
              "tariffs OR \"trade deal\" OR \"government shutdown\" OR \"debt ceiling\" when:1h",
              "Nvidia OR Apple OR Microsoft OR Amazon OR Alphabet OR Meta OR Tesla guidance when:1h"],
    # the candidate hype desk (2026-11-20): the protocol itself, a security
    # incident (existential for a perp DEX token), and bitcoin/crypto risk
    # appetite it is beta-exposed to.
    "hype": ["Hyperliquid when:1h", "crypto exchange hack OR exploit when:1h",
             "bitcoin OR crypto market when:1h"],
}

# The names that carry about a third of the index's weight.
_MEGACAP = r"(nvidia|apple|microsoft|amazon|alphabet|google|meta|tesla|broadcom)"
_GUIDE = r"(guidance|forecast|outlook|earnings|results|revenue|profit)"
_GUIDE_ACT = r"(miss|beat|cut|rais|slash|warn|lower|lift|top|disappoint|withdraw)"
_TARIFF_ACT = (r"(impos|announc|rais|hik|doubl|paus|delay|suspend|lift|roll|exempt|retaliat|threaten|"
               r"new|sweeping|reciprocal|struck down|strikes? down|rul)")

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
    "metals": [
        ("fed", re.compile(r"\b(powell|federal reserve|fomc|the fed|fed)\b.*\b(emergency|cut|hike|surprise|resign|fire)\w*",
                            re.I)),
        ("tariff/metals", re.compile(r"\b(tariff|export ban|export control)\w*\b.*\b(gold|bullion|silver|metals?|copper)\b",
                                     re.I)),
        ("trump/fed", re.compile(r"\btrump\b.*\b(fed|powell|rates?)\b", re.I)),
        # gold since 2026-11-05: a central bank buying or selling bullion
        # (PBOC's monthly reserve print, a Polish or Turkish purchase), the
        # safe-haven bid when Iran/Hormuz escalates (the oil pattern, since
        # gold moves on the same headline), and a yields or dollar shock
        # (a wrap headline that opens with "Dollar ..." is still filtered by
        # PRICE_WRAP; the price shock catches gold's own reaction).
        ("central bank gold", re.compile(r"\b(central banks?|pboc|people's bank|reserve bank|bundesbank|national bank)\b"
                                         r".*\b(gold|bullion)\b|\b(gold|bullion)\b.*\b(reserves?|purchases?|buying|"
                                         r"bought|sold|selling)\b.*\b(central banks?|pboc)\b", re.I)),
        ("iran/hormuz", re.compile(rf"\b{_IRAN}\w*\b.*\b{_ACT}\w*|\b{_ACT}\w*\b.*\b{_IRAN}\w*", re.I)),
        ("yields/dollar shock", re.compile(r"\b(treasury yields?|real yields?|dollar|dxy|greenback)\b.*"
                                           r"\b(spike|surge|soar|plunge|tumble|crash|jump)\w*", re.I)),
    ],
    # The S&P (2026-11-19). Event words only, like the groups above: a market
    # wrap ("Stocks fall as ...") is dropped by PRICE_WRAP and the price shock
    # catches the move itself.
    "sp500": [
        ("fed", re.compile(r"\b(powell|federal reserve|fomc|the fed|fed)\b.*\b(emergency|cut|hike|surprise|resign|fire)\w*",
                            re.I)),
        ("trump/fed", re.compile(r"\btrump\b.*\b(fed|powell|rates?)\b", re.I)),
        ("tariffs", re.compile(rf"\btariffs?\b.*\b{_TARIFF_ACT}\w*|\b{_TARIFF_ACT}\w*\b.*\btariffs?\b", re.I)),
        ("megacap", re.compile(rf"\b{_MEGACAP}\b.*\b{_GUIDE}\b.*\b{_GUIDE_ACT}\w*|"
                               rf"\b{_MEGACAP}\b.*\b{_GUIDE_ACT}\w*\b.*\b{_GUIDE}\b", re.I)),
        ("washington", re.compile(r"\b(government shutdown|debt ceiling|debt limit|u\.?s\.? default|"
                                  r"credit rating|downgrad\w*)\b.*\b(begin|start|avert|end|deal|vote|fail|loom|"
                                  r"u\.?s\.?|united states|treasur)\w*|"
                                  r"\b(avert|end|deal|vote|fail)\w*\b.*\b(government shutdown|debt ceiling|debt limit)\b",
                                  re.I)),
        ("market stress", re.compile(r"\b(circuit breaker|trading halt\w*|flash crash|limit[- ]down|"
                                     r"bank (failure|run|collapse)|fdic (seiz|tak)\w*)\b", re.I)),
        ("yields shock", re.compile(r"\b(treasury yields?|10-year yields?|bond yields?)\b.*"
                                    r"\b(spike|surge|soar|plunge|tumble|crash|jump)\w*", re.I)),
    ],
    # The candidate hype desk (2026-11-20). Event words only, like the groups
    # above: a price wrap is caught by the price shock, not a news alert.
    "hype": [
        ("protocol", re.compile(r"\b(hyperliquid|hlp vault|assistance fund)\b.*\b(launch\w*|upgrad\w*|propos\w*|"
                                r"vot\w*|pass\w*|reject\w*|buyback\w*|burn\w*|halt\w*|pause\w*|resum\w*|migrat\w*|"
                                r"outag\w*|down\w*)\b", re.I)),
        ("exploit", re.compile(r"\b(hyperliquid|hlp vault|perp dex|perpetual exchange|defi protocol|bridge|"
                               r"crypto exchange)\b.*\b(exploit\w*|hack\w*|drain\w*|breach\w*|compromis\w*|"
                               r"stolen|rug pull)\w*|\b(exploit\w*|hack\w*|drain\w*|stolen|rug pull)\w*\b.*"
                               r"\b(hyperliquid|hlp vault|perp dex|bridge)\b", re.I)),
        ("unlock", re.compile(r"\bhype\b.*\b(unlock\w*|vesting|cliff)\b|\b(unlock\w*|vesting|cliff)\b.*\bhype\b",
                              re.I)),
        ("competition", re.compile(r"\b(binance|coinbase|okx|bybit|kraken)\b.*\b(perp\w*|perpetual exchange|"
                                   r"derivatives?|futures)\b|\b(perp\w*|perpetual exchange|derivatives?|futures)\b"
                                   r".*\b(binance|coinbase|okx|bybit|kraken)\b", re.I)),
        ("regulation", re.compile(r"\b(sec|cftc)\b.*\b(sue\w*|charg\w*|enforc\w*|approv\w*|reject\w*|rule\w*|"
                                  r"propos\w*)\w*\b.*\b(crypto|digital assets?|stablecoins?|exchange)\b|"
                                  r"\b(sec|cftc)\b.*\b(crypto|digital assets?|stablecoins?)\b.*"
                                  r"\b(sue\w*|charg\w*|enforc\w*|approv\w*|reject\w*|rule\w*|propos\w*)\w*|"
                                  r"\b(crypto market structure|stablecoin)\b.*\b(bill|act|law|legislation)\b.*"
                                  r"\b(pass\w*|sign\w*|vote\w*|fail\w*)\w*", re.I)),
        ("btc shock", re.compile(r"\bbitcoin\b.*\b(etf)\b.*\b(approv\w*|reject\w*|launch\w*|outflows?|inflows?)\w*|"
                                 r"\b(crypto|bitcoin)\b.*\b(liquidat\w*|flash crash)\b", re.I)),
    ],
}
ODDS_TERMS = {"oil": re.compile(r"(iran|hormuz|ceasefire|opec|oil)", re.I),
              "metals": re.compile(r"(fed|rate cut|powell|recession|gold|iran|ceasefire)", re.I),
              "sp500": re.compile(r"(fed|rate cut|powell|recession|shutdown|tariff|trade deal|s&p|debt ceiling)",
                                  re.I),
              "hype": re.compile(r"(hyperliquid|hype token|bitcoin|crypto regulation|crypto market|"
                                 r"stablecoin|sec crypto)", re.I)}

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
_POST_SP_TARIFF_WHO = (r"(china|chinese|india\w*|canad\w*|mexic\w*|europe\w*|eu|japan\w*|korea\w*|taiwan\w*|"
                       r"vietnam\w*|chips?|semiconductors?|pharma\w*|autos?|cars|steel|aluminum|imports?)")
POST_PATTERNS: Dict[str, List[Tuple[str, re.Pattern]]] = {
    "oil": [
        ("trump:iran", re.compile(rf"\b{_IRAN}\w*\b", re.I)),
        ("trump:oil", re.compile(rf"\b{_POST_OIL}\b", re.I)),
        ("trump:geo", re.compile(rf"\b{_POST_GEO_ACT}\b.*\b{_POST_GEO_WHO}\b|\b{_POST_GEO_WHO}\b.*\b{_POST_GEO_ACT}\b",
                                 re.I | re.S)),
        ("trump:tariff", re.compile(rf"\btariff\w*\b.*\b{_POST_TARIFF_WHO}\b|\b{_POST_TARIFF_WHO}\b.*\btariff\w*\b",
                                    re.I | re.S)),
    ],
    "metals": [
        ("trump:fed", re.compile(rf"\b{_POST_FED}\b", re.I)),
        # a Fed name only with Fed context: "Tracey Powell for State Senate" is a candidate
        ("trump:fed", re.compile(rf"\b{_POST_FED_NAME}\b.*\b{_POST_FED_CTX}\b|\b{_POST_FED_CTX}\b.*\b{_POST_FED_NAME}\b",
                                 re.I | re.S)),
        ("trump:tariff/metals", re.compile(r"\btariff\w*\b.*\b(silver|gold|bullion|copper|metals?)\b|"
                                           r"\b(silver|gold|bullion|copper|metals?)\b.*\btariff\w*\b", re.I | re.S)),
        # gold since 2026-11-05: a post on Iran is the safe-haven headline
        # (the 11-02 "there will not be a War with Iran" post moved gold too).
        ("trump:iran", re.compile(rf"\b{_IRAN}\w*\b", re.I)),
    ],
    # The S&P (2026-11-19): the Fed (the metals patterns: a Fed name needs Fed
    # context), tariffs with a trade partner or a sector next to them, and the
    # market itself by name.
    "sp500": [
        ("trump:fed", re.compile(rf"\b{_POST_FED}\b", re.I)),
        ("trump:fed", re.compile(rf"\b{_POST_FED_NAME}\b.*\b{_POST_FED_CTX}\b|\b{_POST_FED_CTX}\b.*\b{_POST_FED_NAME}\b",
                                 re.I | re.S)),
        ("trump:tariff", re.compile(rf"\btariff\w*\b.*\b{_POST_SP_TARIFF_WHO}\b|\b{_POST_SP_TARIFF_WHO}\b.*\btariff\w*\b",
                                    re.I | re.S)),
        ("trump:markets", re.compile(r"\b(stock market|stocks|wall street|s&p|dow jones|nasdaq|401\(?k\)?s?)\b", re.I)),
    ],
}
# An archived image/video post with no words, or a bare link: nothing to read.
# Was one regex, ^(\s*(view image|...|https?://\S*|\S*truthsocial\.com\S*)\s*)*$,
# whose nested repetition backtracks exponentially on a crafted post (CodeQL
# py/redos, 2026-11-15). Now linear: drop link words, then the filler phrases.
_POST_FILLER = re.compile(r"view (?:image|video)|rt @ \w+|rt:?|reposted donald j\. trump|quoted donald j\. trump",
                          re.I)


def _is_link_word(word: str) -> bool:
    """A URL, or a bare truthsocial.com address (truthsocial.com/@x)."""
    w = word.lower()
    if w.startswith(("http://", "https://")):
        return True
    host = (urlparse("//" + w).hostname or "")
    return host == "truthsocial.com" or host.endswith(".truthsocial.com")


def post_is_empty(text: str) -> bool:
    """True for a post with no words: only links, "View Image", "RT @ name",
    "ReTruthed" filler. Linear in the length of the text."""
    words = " ".join(w for w in (text or "").split() if not _is_link_word(w))
    return not _POST_FILLER.sub(" ", words).strip()
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

# ── DIRECTION: the one place the sign of a headline is read ─────────────────
# Operator, 2026-11-05: "we SHOULDNT PAUSE FOR BULLISH HEADLINES" (the oil
# thesis is long). Every alert gets "direction" for the GROUP'S COINS: +1 the
# headline is bullish for them, -1 bearish, 0 unknown or conflicting.
#   price shock / drift: the sign of the move.
#   news / posts / official: count the bullish and the bearish phrases below
#     in the title; one side only decides, both or neither is 0. A negated
#     phrase ("no deal", "rejects deal", "talks collapse", "ceasefire
#     collapses", "will not be a war") is rewritten FIRST into a token on the
#     opposite list (deal fails = more risk = bullish oil; war averted =
#     bearish oil), so the negation flips instead of matching the bare word.
#   odds: sign(change) x the market's polarity (+1 when a rising YES is
#     bullish for the coin: "Will Iran target an Arab country" up = bullish
#     oil; "US x Iran diplomatic meeting", "ceasefire", "Hormuz reopens" up =
#     bearish oil). Unknown polarity = 0.
# Polarity is per group: for OIL, less supply or more risk is bullish (an
# attack, a seizure, a closure, a tightened blockade, an OPEC cut); a deal, a
# reopening, an SPR release or an OPEC hike is bearish. The feed that tagged
# "Iran Opens Strait of Hormuz to China and India as U.S. Blockade Continues"
# as "bull" was reading risk, not supply: for oil it is -1 (more supply). For
# METALS (gold) risk-off and a Fed cut are bullish; a Fed hike, hawkish talk,
# a strong dollar, surging yields and risk-on (a deal, a ceasefire) bearish.
# A bare "blockade", "war" or "sanctions" carries no sign: with a war on
# every wrap says "Iran war", and "blockade continues" is the status quo.
_W = r"\w*"
# negations: (pattern, replacement token); the token sits on the flipped list
_OIL_NEGATIONS = [
    (re.compile(r"\b(no|not?\s+\w*\s*|without (a |an )?|rejects?\w*\s+(a |the |any )?|rules? out (a |the |any )?|"
                r"collapse of (the )?|end of (the )?|scraps?\w*\s+(a |the )?|abandons?\w*\s+(a |the )?|"
                r"walks? (away|out) (of|from) (the )?)(?:[\w']+\s+){0,2}"
                r"(peace |nuclear |ceasefire |cease-fire )?(deal|ceasefire|cease-fire|truce|agreement|accord|"
                r"peace talks|talks|negotiations?)\b", re.I), " dealfails "),
    (re.compile(r"\b(deal|ceasefire|cease-fire|truce|agreement|accord|talks|negotiations?|peace)\s+(?:[\w']+\s+){0,3}"
                r"(collaps\w*|fail\w*|fall\w* (through|apart)|break\w* down|stall\w*|unravel\w*|"
                r"off the table|dead|over)\b", re.I), " dealfails "),
    (re.compile(r"\b(no|not be a|won't be a|will not be a|avoid\w*|avert\w*|rul\w* out|calls? off|"
                r"cancel\w*|halt\w*|suspend\w*)\s+(a |an |the |any |further |more )?(war|strikes?|attacks?|"
                r"escalation|retaliation|blockade)\b", re.I), " waraverted "),
]
_OIL_BULL = [
    r"\bdealfails\b", r"\battack\w*", r"\bstrikes?\b(?! (a |the )?(deal|agreement|accord|truce))",
    r"\bstruck\b(?! (a |the )?(deal|agreement|accord|truce))",
    r"\bseiz\w*", r"\bhijack\w*", r"\bboard(ed|s|ing)\b.*\b(tanker|vessel|ship)", r"\bexplo(sion|des?)\b",
    r"\bblast\b", r"\b(sea |naval )?mines?\b", r"\bmissiles?\b", r"\bdrones?\b", r"\bclos(ed|ure|es|ing)\b",
    r"\bshuts?( down)?\b", r"\bshutdown\b", r"\bhalts?\b", r"\boutage", r"\bdisrupt\w*", r"\bsabotag\w*",
    r"\b(impos|tighten|expand|enforc|new|launch|begin|start)\w*\W+(a |the |its |naval )?blockade\b",
    r"\bblockade\W+(tighten|impos|expand|begin|start)\w*", r"\bsanctions?\b.*\b(impos|tighten|new|fresh|slap)\w*",
    r"\b(impos|tighten|new|fresh|slap)\w*\b.*\bsanctions?\b", r"(?<!de-)(?<!de)\bescalat\w*", r"\bretaliat\w*",
    r"\bopec\+?\b.*\b(cut|curb|reduc|trim)\w*", r"\b(output|production|supply) cuts?\b", r"\bcuts? (output|production)\b",
    r"\bsupply (shock|shortage|disruption)", r"\bshortage",
]
_OIL_BEAR = [
    r"\bwaraverted\b", r"\bdeal\b", r"\bceasefire\b", r"\bcease-fire\b", r"\btruce\b", r"\bagreement\b", r"\baccord\b",
    r"\bpeace\b", r"\bde-?escalat\w*", r"\bre-?open\w*", r"\bresum\w*", r"\brestor\w*", r"\bnormaliz\w*",
    r"\bopens?\b.*\b(strait|hormuz|port|pipeline|terminal|flows?|exports?|transit|shipping|waterway)",
    r"\b(strait|hormuz|port|pipeline|terminal|flows?|exports?|transit|shipping|waterway)\w*\b.*\bopens?\b",
    r"\bsanctions?\b.*\b(lift|eas|waiv|relief|suspend|remov)\w*", r"\b(lift|eas|waiv|suspend|remov)\w*\b.*\bsanctions?\b",
    r"\btalks\b.*\b(progress|going well|advanc|productive|constructive|positive)\w*",
    r"\b(progress|productive|constructive|positive|good|great)\w*\b.*\btalks\b",
    r"\bopec\+?\b.*\b(hike|boost|increase|raise|rais|add)\w*", r"\b(output|production|supply) (hike|boost|increase)s?\b",
    r"\b(boost|raise|hike|increase)\w* (oil |crude )?(output|production|supply)\b",
    r"\b(spr|strategic petroleum reserve)\b.*\b(releas|offer|sell|sale|draw|tap)\w*",
    r"\b(releas|offer|sell|sale|draw|tap)\w*\b.*\b(spr|strategic petroleum reserve)\b",
    r"\bsurplus\b", r"\bglut\b", r"\bdemand (slump|drop|fall|slow|destruction)\w*",
]
_METALS_NEGATIONS = [
    (re.compile(r"\b(no|not?\s+\w*\s*|rules? out (a |the |any )?|rejects?\w*\s+(a |the |any )?|delays?\w*\s+(a |the )?|"
                r"pauses?\w*\s+(the )?|holds? off (on )?(a |the )?)(rate |interest rate |emergency )?cuts?\b", re.I),
     " hawkishhold "),
    (re.compile(r"\b(no|not be a|won't be a|will not be a|avoid\w*|avert\w*|rul\w* out)\s+(a |an |the |any )?"
                r"(war|strikes?|attacks?|escalation|retaliation)\b", re.I), " riskon "),
]
_METALS_BULL = [
    r"\bemergency\b.*\bcut", r"\b(rate|interest rate) cuts?\b", r"\bcuts? (interest )?rates?\b", r"\bcut by \d+", r"\bdovish\b",
    r"\bcuts?\b(?! (output|production|supply|tariff|off))",
    r"\bsafe[- ]haven\b", r"\brecession\b", r"\bwar\b(?! (veteran|hero|college))", r"\binva(de|sion)\w*", r"\battack\w*",
    r"\bstrikes?\b(?! (a |the )?(deal|agreement|accord|truce))", r"\bmissiles?\b", r"(?<!de-)(?<!de)\bescalat\w*",
    r"\bretaliat\w*", r"\bseiz\w*", r"\bblockade\W+(tighten|impos|expand)\w*", r"\brisk[- ]off\b",
    r"\b(central banks?|pboc|people's bank|reserve bank|bundesbank|national bank)\b.*\b(buy|bought|purchas|add|rais)\w*",
    r"\b(buy|bought|purchas|add|rais)\w*\b.*\b(central banks?|pboc)\b",
    r"\bdollar\b.*\b(plunge|tumble|slide|slump|fall|weak|drop)\w*", r"\byields?\b.*\b(plunge|tumble|slide|fall|drop)\w*",
]
_METALS_BEAR = [
    r"\bhawkishhold\b", r"\briskon\b", r"\b(rate|interest rate) hikes?\b", r"\bhikes? (interest )?rates?\b", r"\bhawkish\b",
    r"\bhikes?\b(?! (output|production|tariff))",
    r"\bhigher for longer\b", r"\bstrong(er)? dollar\b", r"\bdollar\b.*\b(surge|spike|soar|jump|rall|strengthen|climb)\w*",
    r"\byields?\b.*\b(surge|spike|soar|jump|climb)\w*", r"\bceasefire\b", r"\bcease-fire\b", r"\btruce\b",
    r"\bpeace (deal|agreement|talks|accord)\b", r"\b(peace |nuclear )?deal\b", r"\bde-?escalat\w*", r"\brisk[- ]on\b",
    r"\b(central banks?|pboc|people's bank|reserve bank)\b.*\b(sell|sold|dump|cut|reduc)\w*",
]
# The S&P (2026-11-19): risk-on is bullish. A Fed cut, a tariff pause or a
# trade deal, a ceasefire, a shutdown averted, a megacap beating or raising;
# bearish the mirror: a hike or hawkish hold, new or higher tariffs, a deal
# that fails, an attack or an escalation, a shutdown, a downgrade, a miss or
# a cut to guidance, yields spiking, a bank failing. "no rate cut" and "deal
# fails" are rewritten first, as for the other groups.
_SP500_NEGATIONS = [_METALS_NEGATIONS[0], _OIL_NEGATIONS[0], _OIL_NEGATIONS[1], _OIL_NEGATIONS[2]]
_SP500_BULL = [
    r"\bwaraverted\b", r"\bemergency\b.*\bcut", r"\b(rate|interest rate) cuts?\b", r"\bcuts? (interest )?rates?\b",
    r"\bcut by \d+", r"\bdovish\b", r"\bsoft landing\b", r"\bstimulus\b",
    r"\btariffs?\b.*\b(paus|delay|suspend|lift|roll(ed|s|ing)? back|exempt|cut|lower|struck down)\w*",
    r"\b(struck|strikes?) down\b.*\btariffs?\b",
    r"\b(paus|delay|suspend|lift|roll(ed|s|ing)? back|exempt|lower)\w*\b.*\btariffs?\b",
    r"\btrade (deal|agreement|truce|pact)\b", r"\bceasefire\b", r"\bcease-fire\b", r"\btruce\b",
    r"\bpeace (deal|agreement|talks|accord)\b", r"\bde-?escalat\w*", r"\brisk[- ]on\b",
    r"\bshutdown\b.*\b(avert|end|ends|ended|over|deal)\w*", r"\b(avert|end|ends|ended)\w*\b.*\bshutdown\b",
    r"\b(beat|beats|tops?|topped|crush\w*)\b.*\b(estimates?|expectations?|forecasts?)\b",
    r"\b(rais|lift|boost|hik)\w*\b.*\b(guidance|forecast|outlook)\b",
    r"\byields?\b.*\b(plunge|tumble|slide|fall|drop)\w*",
]
_SP500_BEAR = [
    r"\bhawkishhold\b", r"\bdealfails\b", r"\b(rate|interest rate) hikes?\b", r"\bhikes? (interest )?rates?\b",
    r"\bhawkish\b", r"\bhigher for longer\b", r"\brecession\b", r"\bstagflation\b",
    r"\btariffs?\b.*\b(impos|rais|hik|doubl|new|sweeping|reciprocal|retaliat|threaten|escalat)\w*",
    r"\b(impos|rais|hik|doubl|new|sweeping|reciprocal|retaliat|threaten)\w*\b.*\btariffs?\b",
    r"\btrade war\b", r"\battack\w*", r"\bstrikes?\b(?! (down|a deal|the deal|deal|agreement|accord|truce))",
    r"\bmissiles?\b", r"(?<!de-)(?<!de)\bescalat\w*", r"\bretaliat\w*", r"\binva(de|sion)\w*", r"\brisk[- ]off\b",
    r"\bshutdown\b.*\b(begin|start|loom|drag|extend)\w*", r"\bdowngrad\w*", r"\bdefault\b",
    r"\b(miss|misses|missed|disappoint\w*)\b.*\b(estimates?|expectations?|forecasts?)\b",
    r"\b(cut|cuts|slash|lower|withdraw)\w*\b.*\b(guidance|forecast|outlook)\b", r"\bprofit warning\b",
    r"\byields?\b.*\b(surge|spike|soar|jump|climb)\w*", r"\bbank (failure|run|collapse)\b",
    r"\bcircuit breaker\b", r"\bflash crash\b",
]
# The candidate hype desk (2026-11-20): buybacks/burns and revenue growth are
# bullish for a token whose desk revenue funds them; a hack/exploit/bridge
# drain is bearish and specific to it (an existential risk for a perp DEX, not
# a generic risk-off word); crypto risk appetite (rate cuts, a bitcoin ETF
# catalyst) carries over the same way it does for every risk asset.
_HYPE_NEGATIONS = [_METALS_NEGATIONS[0]]
_HYPE_BULL = [
    r"\bbuyback\w*\b", r"\bburn\w*\b", r"\brevenue\b.*\b(record|surg\w*|grow\w*|ris\w*|jump\w*)\w*",
    r"\b(rate|interest rate) cuts?\b", r"\bcuts? (interest )?rates?\b", r"\bdovish\b", r"\brisk[- ]on\b",
    r"\betf\b.*\b(approv\w*|launch\w*|inflows?)\b|\b(approv\w*|launch\w*|inflows?)\b.*\betf\b",
    r"\blist\w*\b.*\bhype\b|\bhype\b.*\blist\w*\b",
]
_HYPE_BEAR = [
    r"\bexploit\w*\b", r"\bhack\w*\b", r"\bdrain\w*\b", r"\bbreach\w*\b", r"\bstolen\b", r"\brug pull\b",
    r"\b(rate|interest rate) hikes?\b", r"\bhikes? (interest )?rates?\b", r"\bhawkish\b", r"\brisk[- ]off\b",
    r"\betf\b.*\b(reject\w*|outflows?)\b|\b(reject\w*|outflows?)\b.*\betf\b",
    r"\bunlock\w*\b.*\b(dump\w*|sell\w*)\w*", r"\bflash crash\b", r"\bliquidat\w*\b.*\bcascad\w*",
    r"\bsec\b.*\b(sue\w*|charg\w*|enforc\w*)\w*", r"\bdelist\w*\b",
]
DIRECTION_WORDS: Dict[str, Dict[str, Any]] = {
    "oil": {"negations": _OIL_NEGATIONS, "bull": [re.compile(p, re.I | re.S) for p in _OIL_BULL],
            "bear": [re.compile(p, re.I | re.S) for p in _OIL_BEAR]},
    "metals": {"negations": _METALS_NEGATIONS, "bull": [re.compile(p, re.I | re.S) for p in _METALS_BULL],
               "bear": [re.compile(p, re.I | re.S) for p in _METALS_BEAR]},
    "sp500": {"negations": _SP500_NEGATIONS, "bull": [re.compile(p, re.I | re.S) for p in _SP500_BULL],
              "bear": [re.compile(p, re.I | re.S) for p in _SP500_BEAR]},
    "hype": {"negations": _HYPE_NEGATIONS, "bull": [re.compile(p, re.I | re.S) for p in _HYPE_BULL],
            "bear": [re.compile(p, re.I | re.S) for p in _HYPE_BEAR]},
}
# Polymarket questions: +1 when a rising YES is bullish for the group's coins.
ODDS_POLARITY: Dict[str, List[Tuple[int, re.Pattern]]] = {
    "oil": [
        (-1, re.compile(r"\b(diplomatic|meeting|talks|negotiat\w*|ceasefire|cease-fire|truce|deal|agreement|accord|peace|"
                        r"re-?open\w*|normal\w*|resum\w*|lift\w* sanctions|sanctions (relief|lifted|eased)|"
                        r"(below|under) \$|opec\+? (hike|boost|increase|raise)\w*|increase (oil )?(output|production))\b",
                        re.I)),
        (+1, re.compile(r"\b(target\w*|attack\w*|strikes?|strike\b|seiz\w*|clos\w*|blockade|escalat\w*|invade\w*|invasion|"
                        r"mines?|missiles?|retaliat\w*|(above|over|reach|hit) \$|opec\+? (cut|curb)\w*|"
                        r"cut (oil )?(output|production)|nuclear (test|weapon))\b", re.I)),
    ],
    "metals": [
        (-1, re.compile(r"\b(rate hike|hikes? rates?|raise rates?|hold rates?|no (rate )?cut|ceasefire|cease-fire|truce|"
                        r"peace|deal|agreement|(below|under) \$)\b", re.I)),
        (+1, re.compile(r"\b(rate cut|cuts? (interest )?rates?|lower rates?|emergency cut|recession|war|strikes?|attack\w*|"
                        r"invade\w*|invasion|escalat\w*|(above|over|reach|hit) \$)\b", re.I)),
    ],
}
ODDS_POLARITY["sp500"] = [
    (-1, re.compile(r"\b(rate hike|hikes? rates?|raise rates?|hold rates?|no (rate )?cut|recession|shutdown|default|"
                    r"downgrade|tariffs?|trade war|war|strikes?|attack\w*|invade\w*|invasion|escalat\w*|"
                    r"(below|under) \$?\d[\d,.]*)\b", re.I)),
    (+1, re.compile(r"\b(rate cut|cuts? (interest )?rates?|lower rates?|ceasefire|cease-fire|truce|peace|"
                    r"trade deal|(above|over|reach|hit) \$?\d[\d,.]*)\b", re.I)),
]
ODDS_POLARITY["hype"] = [
    (-1, re.compile(r"\b(rate hike|hikes? rates?|raise rates?|hold rates?|no (rate )?cut|recession|hack\w*|"
                    r"exploit\w*|sue\w*|charg\w*|(below|under) \$?\d[\d,.]*)\b", re.I)),
    (+1, re.compile(r"\b(rate cut|cuts? (interest )?rates?|lower rates?|approv\w*|list\w*|"
                    r"(above|over|reach|hit) \$?\d[\d,.]*)\b", re.I)),
]
_ODDS_TITLE = re.compile(r"(\d+)%\s*->\s*(\d+)%\s*$")
_PCT_IN_TITLE = re.compile(r"([+-]\d+(?:\.\d+)?)%")


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def text_direction(group: str, text: str) -> int:
    """+1 / -1 / 0 for a headline or post `text`, by DIRECTION_WORDS[group].
    Negations are rewritten first; one side alone decides; both or neither
    is 0 (unknown: holds). Pure."""
    words = DIRECTION_WORDS.get(group)
    if not words or not text:
        return 0
    t = str(text)
    for rx, token in words["negations"]:
        t = rx.sub(token, t)
    bull = any(rx.search(t) for rx in words["bull"])
    bear = any(rx.search(t) for rx in words["bear"])
    return 1 if bull and not bear else -1 if bear and not bull else 0


# Hormuz transit-count markets (2026-11-09: "Will there be between 0 and 4
# average daily transits of the Strait of Hormuz on November 30?" rose
# 51% -> 61% and read as unknown, holding the long). A LOW bucket rising =
# fewer tankers through the strait = bullish oil; a HIGH bucket = bearish.
TRANSITS_RX = re.compile(r"between\s+(\d+)\s+and\s+(\d+)\s+(?:average\s+)?(?:daily\s+)?transits?.*hormuz", re.I)
TRANSITS_LOW_MAX = 20     # a bucket whose top is at or under this = the strait mostly shut
TRANSITS_HIGH_MIN = 60    # a bucket whose bottom is at or over this = flows near normal


def transit_polarity(question: str) -> int:
    """Oil polarity of a Hormuz transit-count bucket market, 0 if not one. Pure."""
    m = TRANSITS_RX.search(str(question or ""))
    if not m:
        return 0
    lo, hi = int(m.group(1)), int(m.group(2))
    if hi <= TRANSITS_LOW_MAX:
        return 1
    if lo >= TRANSITS_HIGH_MIN:
        return -1
    return 0


def odds_polarity(group: str, question: str) -> int:
    """+1 when a rising YES on `question` is bullish for the group's coins,
    -1 when bearish, 0 unknown or both. Negations ("Will the ceasefire
    collapse") are rewritten the same way text_direction does. Pure."""
    if group == "oil" and transit_polarity(question):
        return transit_polarity(question)
    words = DIRECTION_WORDS.get(group)
    q = str(question or "")
    for rx, token in (words["negations"] if words else []):
        q = rx.sub(token, q)
    if "dealfails" in q or "waraverted" in q or "hawkishhold" in q or "riskon" in q:
        return text_direction(group, q)
    hits = {pol for pol, rx in ODDS_POLARITY.get(group, []) if rx.search(q)}
    return next(iter(hits)) if len(hits) == 1 else 0


def odds_direction(group: str, question: str, change: float) -> int:
    """sign(change) x odds_polarity. Pure."""
    return _sign(change) * odds_polarity(group, question)


def alert_direction(alert: Dict[str, Any]) -> int:
    """The alert's "direction", or the same rule applied to a row recorded
    before the field existed (the live file of 2026-11-05: a price row
    carries "move", an odds row's title ends "50% -> 60%", a news or post
    row has its title). Anything unreadable is 0. Pure."""
    d = alert.get("direction")
    if d in (1, -1, 0):
        return int(d)
    group = alert.get("group") or _group_of(alert.get("coin") or "") or ""
    kind = alert.get("kind")
    title = str(alert.get("title") or "")
    if kind == "price":
        mv = alert.get("move")
        if isinstance(mv, (int, float)):
            return _sign(float(mv))
        m = _PCT_IN_TITLE.search(title)
        return _sign(float(m.group(1))) if m else 0
    if kind == "odds":
        m = _ODDS_TITLE.search(title)
        if not m:
            return 0
        return odds_direction(group, _ODDS_TITLE.sub("", title).rstrip(": "), int(m.group(2)) - int(m.group(1)))
    return text_direction(group, title)


def supports(alert: Dict[str, Any], side: Optional[str]) -> bool:
    """True when the alert's direction is the trade side's (long & +1,
    short & -1). False for side None, an unknown direction, or against."""
    want = 1 if side == "long" else -1 if side == "short" else 0
    return bool(want) and alert_direction(alert) == want


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def _key(text: str) -> str:
    return hashlib.sha1(re.sub(r"\W+", " ", text.lower()).strip().encode()).hexdigest()[:16]


def _alerts_path(path: Optional[str]) -> str:
    """Resolved at call time so a test (or the watcher) can point ALERTS_PATH
    at a temp file after import; a default bound at def time could not."""
    return path or ALERTS_PATH


def _load_triggers() -> Dict[str, List[Tuple[str, re.Pattern]]]:
    """data/oil_triggers.json -> [(label, regex)] per group, compiled once at
    import. Every phrase is a whole-word, case-insensitive match with an
    optional plural s ("tankers"); "OPEC+" keeps its plus. A missing or
    broken file is an empty list, never an import error."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "oil_triggers.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    out: List[Tuple[str, re.Pattern]] = []
    for category, phrases in data.items():
        if category.startswith("_") or not isinstance(phrases, list):
            continue
        alts = []
        for p in phrases:
            p = str(p).strip()
            if not p:
                continue
            esc = re.escape(p).replace(r"\ ", r"\s+")
            tail = r"" if p.endswith("+") else r"s?\b"
            alts.append(rf"(?<!\w){esc}{tail}")
        if alts:
            out.append((f"trigger:{category}", re.compile("|".join(alts), re.I)))
    return {"oil": out}


TRIGGERS: Dict[str, List[Tuple[str, re.Pattern]]] = _load_triggers()


def match_trigger(group: str, title: str) -> Optional[str]:
    """The first Stage 1 trigger label (data/oil_triggers.json) `title`
    matches for `group`, else None. Pure, no price-wrap check: callers go
    through match_headline(triggers=True)."""
    for label, rx in TRIGGERS.get(group, []):
        if rx.search(title or ""):
            return label
    return None


def match_headline(group: str, title: str, *, triggers: bool = False) -> Optional[str]:
    """The first pattern label `title` matches for `group`, else None. A price
    wrap never matches (PRICE_WRAP). `triggers=True` (the streaming
    providers, since 2026-11-14) also reads data/oil_triggers.json AFTER the
    patterns; the default path is unchanged, so the RSS feeds match exactly
    what they matched before."""
    title = title or ""
    if PRICE_WRAP.search(title):
        return None
    for label, rx in PATTERNS.get(group, []):
        if rx.search(title):
            return label
    return match_trigger(group, title) if triggers else None


def match_post(group: str, text: str) -> Optional[str]:
    """The first POST_PATTERNS label a Trump post `text` matches for `group`,
    else None. An image/video/link-only post never matches."""
    text = (text or "").strip()
    if not text or post_is_empty(text) or _POST_ROUTINE.search(text):
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


def _is_recent_duplicate(group: str, title: str, recent: List[Dict[str, Any]], now_ms: int) -> bool:
    """True when `title` is a near-duplicate (textnorm.is_near_duplicate,
    NEWS_DEDUP_MIN_OVERLAP) of a title `news_alerts` already alerted on for
    `group` inside NEWS_DEDUP_WINDOW_MIN. Pure; does not mutate `recent`."""
    cutoff = now_ms - NEWS_DEDUP_WINDOW_MIN * MIN_MS
    return any(r.get("group") == group and int(r.get("at_ms") or 0) >= cutoff
               and is_near_duplicate(title, r.get("title") or "", min_overlap=NEWS_DEDUP_MIN_OVERLAP)
               for r in recent)


def _remember_recent(group: str, title: str, recent: List[Dict[str, Any]], now_ms: int) -> None:
    """Record `title` for `_is_recent_duplicate`, dropping anything already
    past NEWS_DEDUP_WINDOW_MIN. Mutates `recent` in place (same list object
    the caller persists, e.g. in headline_state.json)."""
    cutoff = now_ms - NEWS_DEDUP_WINDOW_MIN * MIN_MS
    recent[:] = [r for r in recent if int(r.get("at_ms") or 0) >= cutoff]
    recent.append({"group": group, "title": title, "at_ms": now_ms})


def news_alerts(group: str, items: Iterable[Dict[str, Any]], seen: set, now_ms: int, *,
                shared: bool = False, triggers: bool = False,
                recent: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """New matching headlines from trusted sources, feed order. Adds every
    item's key to `seen` (matching or not) so a headline alerts once. Items:
    parse_rss_items rows ({title, url, source, published_at}). `shared`: the
    same items go to every group (the official feeds), so the key carries the
    group and the item id (URL): each desk judges the item once, and an edited
    title does not alert twice. `triggers`: a streaming provider's items
    (sources/providers.NewsItem) also match data/oil_triggers.json; the row
    then carries `provider`, `sentiment` and `received_ms` for the event log.
    The dedup key stays the title, so a headline Finlight pushes and Google
    News lists two minutes later alerts once.

    A headline with no readable published_at (and, for a provider item, no
    received_at either) is dropped: age unknown is treated as stale, not
    fresh (MAX_AGE_MIN's docstring has the real-world case this covers).

    `recent`: the caller's persisted near-duplicate pool (a list of
    {group, title, at_ms}, mutated in place -- scripts/trigger_watch.py
    keeps it in headline_state.json next to `seen`). None (the default, and
    every call site that does not pass it) skips the near-duplicate check
    entirely, matching the old behavior; only a title that MATCHES a pattern
    is added to the pool, so a non-matching title never blocks a real one
    later. See NEWS_DEDUP_MIN_OVERLAP's docstring for the threshold."""
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
        rec_at = it.get("received_at")
        rec_ms = int(rec_at.timestamp() * 1000) if isinstance(rec_at, datetime) else None
        age_ms = pub_ms if pub_ms is not None else rec_ms
        if age_ms is None or now_ms - age_ms > MAX_AGE_MIN * MIN_MS:
            continue
        if not trusted_source(it.get("source")):
            continue
        label = match_headline(group, title, triggers=triggers)
        if not label:
            continue
        if recent is not None and _is_recent_duplicate(group, title, recent, now_ms):
            continue
        row = {"group": group, "kind": "news", "label": label, "title": title[:200],
               "source": it.get("source"), "url": it.get("url"), "at_ms": now_ms,
               "published_ms": pub_ms, "direction": text_direction(group, title)}
        if it.get("provider"):
            row["provider"] = it["provider"]
            row["sentiment"] = it.get("sentiment")
            row["received_ms"] = rec_ms
        out.append(row)
        if recent is not None:
            _remember_recent(group, title, recent, now_ms)
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
                        "published_ms": pub_ms, "post_id": pid, "post_type": p.get("post_type"),
                        "direction": text_direction(group, text)})
    return out


def odds_alerts(group: str, odds: Iterable[Dict[str, Any]], prev: Dict[str, float],
                now_ms: int) -> List[Dict[str, Any]]:
    """Markets whose probability moved ODDS_JUMP or more since `prev` (id ->
    probability; updated in place). odds rows: {id, question, probability, url,
    closes_ms?, volume_usd?}. A row under ODDS_ALERT_MIN_VOLUME_USD of
    lifetime volume never alerts (2026-11-22: a $146 market moved 19% -> 45%
    on one trade and woke the HYPE desk). A row whose closes_ms is at or before now_ms is past its
    deadline and never alerts (2026-11-16: an expired "ceasefire by November
    8?" market drifted 24% -> 32% and woke the oil desk)."""
    out = []
    rx = ODDS_TERMS.get(group)
    for o in odds:
        q = o.get("question") or ""
        if rx is None or not rx.search(q) or o.get("probability") is None:
            continue
        mid = o.get("id")
        if mid is None:
            continue
        closes_ms = o.get("closes_ms")
        if closes_ms is not None and closes_ms <= now_ms:
            prev.pop(mid, None)
            continue
        vol = o.get("volume_usd")
        if vol is not None and float(vol) < ODDS_ALERT_MIN_VOLUME_USD:
            prev.pop(mid, None)      # too thin to mean anything; never alerts
            continue
        p = float(o["probability"])
        old = prev.get(mid)
        prev[mid] = p
        if old is not None and abs(p - old) >= ODDS_JUMP:
            out.append({"group": group, "kind": "odds", "label": "odds jump",
                        "title": f"{q[:150]}: {old:.0%} -> {p:.0%}", "url": o.get("url"), "at_ms": now_ms,
                        "change": round(p - old, 4), "direction": odds_direction(group, q, p - old)})
    return out


def price_drift(coin: str, history: Sequence[Tuple[int, float]], now_ms: int, mid: float) -> Optional[Dict[str, Any]]:
    """An alert when `mid` sits PRICE_DRIFT or more from any mid in the last
    DRIFT_WINDOW_MIN of `history`. Needs at least half the window of history,
    so a fresh start cannot call a 30-minute move a 4-hour drift. Pure."""
    thr = PRICE_DRIFT.get(coin)
    if not thr or not mid or not history:
        return None
    window = [(t, p) for t, p in history if now_ms - t <= DRIFT_WINDOW_MIN * MIN_MS]
    if not window or now_ms - window[0][0] < (DRIFT_WINDOW_MIN // 2) * MIN_MS:
        return None
    ref = max((p for _t, p in window), key=lambda p: abs(mid - p))
    move = (mid - ref) / ref
    if abs(move) >= thr:
        return {"coin": coin, "group": _group_of(coin), "kind": "price", "label": "price drift",
                "title": f"{coin} {move:+.2%} within {DRIFT_WINDOW_MIN // 60}h ({ref:g} -> {mid:g})",
                "at_ms": now_ms, "move": round(move, 5), "direction": _sign(move)}
    return None


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
                "move": round(move, 5), "direction": _sign(move)}
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
    from services.intel.scheduled_events import group_of
    return group_of(coin)


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


def _in_window(coin: str, at_ms: int, path: Optional[str]) -> List[Dict[str, Any]]:
    """The alerts on `coin` whose HEADLINE_BLACKOUT_MIN window covers `at_ms`, newest first."""
    g = _group_of(coin)
    hits = [a for a in read_alerts(path) if (a.get("coin") == coin or (g and a.get("group") == g))
            and int(a["at_ms"]) <= at_ms < int(a["at_ms"]) + HEADLINE_BLACKOUT_MIN * MIN_MS]
    return sorted(hits, key=lambda a: -int(a["at_ms"]))


def headline_blackout(coin: str, at_ms: int, path: Optional[str] = None, *,
                      side: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Active for HEADLINE_BLACKOUT_MIN after the newest alert on `coin` that
    does not support `side`. side None (the old rule): any alert holds. side
    "long"/"short" (operator, 2026-11-05: "we SHOULDNT PAUSE FOR BULLISH
    HEADLINES"): an alert whose direction is the side's own (long & +1,
    short & -1) is skipped and listed under "skipped"; an alert against the
    side or of unknown direction (0) still holds. Rows recorded before the
    direction field existed are read by alert_direction. `path` keeps its
    old position; `side` is keyword-only (event_blackout passes side=)."""
    live = _in_window(coin, at_ms, path)
    skipped = [a for a in live if supports(a, side)] if side in ("long", "short") else []
    held = [a for a in live if a not in skipped]
    if not held:
        return None
    a = held[0]
    end = int(a["at_ms"]) + HEADLINE_BLACKOUT_MIN * MIN_MS
    d = alert_direction(a)
    if side in ("long", "short"):
        why = f"against the {side}" if d else "direction unknown"
        reason = (f"Headline blackout: no new {side} entries until {_utc(end)} "
                  f"({a['label']}: {a['title'][:90]}; {why}); the thesis rewrites first")
    else:
        reason = (f"Headline blackout: no new entries until {_utc(end)} "
                  f"({a['label']}: {a['title'][:90]}); the thesis rewrites first")
    return {"coin": coin, "event": a["title"], "kind": "headline", "start_ms": int(a["at_ms"]),
            "end_ms": end, "until_utc": _utc(end), "alert": a, "direction": d, "side": side,
            "skipped": skipped, "reason": reason}


def skipped_aligned(coin: str, at_ms: int, side: Optional[str],
                    path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The newest alert on `coin` still inside its window that supports
    `side`, when NO other in-window alert holds that side (so the brief can
    say "Headline alert (supports the long): ..., no hold"). None otherwise."""
    if side not in ("long", "short"):
        return None
    live = _in_window(coin, at_ms, path)
    if any(not supports(a, side) for a in live):
        return None
    return next((a for a in live if supports(a, side)), None)


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
           "DRIFT_WINDOW_MIN", "DRIFT_REARM_MIN", "PRICE_DRIFT", "HISTORY_KEEP_MIN", "price_drift",
           "PRICE_WRAP", "TRUSTED_SOURCES", "TRIGGERS", "DIRECTION_WORDS", "ODDS_POLARITY", "alert_direction",
           "alerts_for", "headline_blackout", "latest_for", "load_state", "match_headline", "match_post",
           "match_trigger", "news_alerts",
           "odds_alerts", "odds_direction", "odds_polarity", "post_alerts", "price_shock", "read_alerts", "record",
           "save_state", "skipped_aligned", "supports", "text_direction", "trusted_source"]
