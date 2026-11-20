"""Wake the desk tick the minute a desk's entry rule passes. No LLM calls.

Operator, 2026-10-31: the loops went to every 4 hours for the weekend
("reduce loops for each to every 4 hours until Sunday evening"), which can
miss an entry by up to 4 hours. This watcher closes that gap for free: every
minute it reads each active desk's mid and asks the SAME function the Desk
page and the tick's rules use (services/strategies/next_trade.next_trade:
thesis side, never above a long's entry / below a short's, stop beyond the
24h range, target the far 24h edge minus 0.5%, 2R, the 20x rule) whether the
tick would trade right now. The first desk that says yes is printed as one
JSON line and the script EXITS, so the session that started it in the
background is notified and runs `/desk-tick <desk>`. The tick still makes
the decision and runs every safety check; this only decides WHEN it runs.

  python3 scripts/trigger_watch.py                 # all desks with an equity share
  python3 scripts/trigger_watch.py --once          # one pass, print, exit (debug)

Skips a desk that already holds a position or a resting entry (its tick and
its stop/target orders handle it), and a desk that fired in the last
--cooldown-min minutes (a tick that declined must not be re-woken every
minute). State and a per-minute log live under /tmp/trigger-watch/.
Read-only on the venue and the journal: it never places, cancels or writes
anything but its own log, cooldown file and headline state -- plus, since
2026-11-20, one line on the desk's OWN shared progress log
(services/strategies/desk_log.log_path(desk), same file the desk-tick agent
appends `DESK-TICK <desk> ...` lines to) for a fire that names one specific
desk: `WATCH <desk> <utc> ...` (`_append_watch_line`). That is the only
place this module's activity reaches the Desk page's per-desk Loop Log card
(`GET /api/desks/{id}/log`); this module's own watch.log is unaffected and
unfiltered either way.

Since 2026-11-05 (operator: "FIX THEM") every pass starts with the headline
step (services/intel/headline_watch.py): new matching news, a Polymarket
odds jump or a 1% / 1.2% mid move inside 15 minutes is recorded as an alert
and wakes the desk at once, position or not, as
{"fire": desk, "also": [...], "reason": "headline", ...}. The first run
ever (no headline state file) only learns the current headlines. Since the
same day the pass also reads Trump's posts (the trumpstruth.org archive of
Truth Social, every minute) and the official whitehouse.gov news feed (with
the news poll); Truth Social itself and X have no feed we may read, and
state.gov refuses our User-Agent (services/intel/sources/social.py). A
source new to the state file is learned on its first poll, not alerted on.

Since 2026-11-05 (operator: "weekend flat") the oil futures weekend
(services/strategies/weekend.py, Fri 16:00 to Sun 18:00 New York) wakes
the oil desk ONCE at the window open when it holds a position or a resting
entry, as {"fire": desk, "reason": "weekend_flat", ...}: the tick closes it
(desk-tick agent, scripts/weekend.py) instead of waiting up to 15 minutes
for the cron. The cooldown key `<desk>:weekend_flat` remembers the window
it fired for, so it never fires twice for one weekend. A flat desk inside
the window is skipped like any other blackout (place_bracket refuses).

Since 2026-11-09 the watcher also asks Hyperliquid, at start and then once
an hour, whether the API wallet that signs our orders is still approved
(services/accounts/signer_status.py). When it is not, every order would be
refused, so it exits with {"fire": desk, "reason": "signer", "detail": ...}
at most once per 6 hours; the loops window tells the operator instead of
running a tick.

Since 2026-11-06 (operator, 2026-11-05: "we SHOULDNT PAUSE FOR BULLISH
HEADLINES") the blackout check carries the thesis side: a headline alert
whose direction supports it (a bullish headline, odds move or price move
for a long) does not hold that side's entries, so the watcher still wakes
the tick on the entry rule; an alert against the side or of unknown
direction holds as before. The headline step itself is unchanged: every
alert wakes the tick and makes the thesis due, whatever its direction.

Since 2026-11-14 (operator: headline lag "from about 120s to seconds") a
Finlight stream (services/intel/finlight_watch.py, sources/finlight.py)
runs in a daemon thread when FINLIGHT_API_KEY is set: WebSocket first, REST
polling when the plan has none. A pushed headline that matches Stage 1
(PATTERNS plus data/oil_triggers.json) wakes this loop from its minute
sleep at once; the pass drains it through the SAME news_alerts / record /
fire path, with the Stage 2 score (event_score) and the book guard
(book_guard) riding along on the alert and in
$PATHIEL_STATE_DIR/intel/finlight_events.jsonl. No key, an import error or
a dead stream: the watcher runs on RSS exactly as before.

Since 2026-11-17 (operator: a months-old reprint and a retitled duplicate
each burned a slot of the day's MAX_ALERTS_PER_DAY on nothing): an undated
headline no longer counts as fresh, and a near-duplicate of a title already
alerted on (same wire story, a second outlet's headline) does not fire
twice -- both in services/intel/headline_watch.news_alerts, read its
docstring and MAX_AGE_MIN / NEWS_DEDUP_MIN_OVERLAP for the data behind
each. The dedup pool lives in hl_state (`recent_news`), saved to
headline_state.json on every pass like `seen`, so it survives the restart
every fire causes.

Also since 2026-11-17 (operator bug: a stale fire). 2026-11-16 12:59Z the watcher
fired oil at 2.42R (stop 91.05, off the cached hour-12 24h low 91.80); the
desk-tick's OWN fresh read, started a moment later, landed at 13:00Z, just
after the 12:00-13:00 1h bar closed and rolled the 24h low to 91.582 while
the mid climbed past the thesis's own entry (91.91: "never chase"). 1.49R,
under the 2R gate, so the tick declined.

Replayed with the real candles (tests/test_trigger_watch.py's regression,
and services/strategies/evals/confirm_fire_replay.py against every real
fire in this log), the lv_cache below was NOT wrong for the instant it was
read: a from-scratch read at 12:59Z gives the identical 91.80/2.42R the
fire printed, so asking the SAME question again at that same instant (no
wait) would have agreed and fired anyway - a zero-delay recheck is not the
fix. The fire and the tick read the market about a minute apart (the
agent-dispatch time to actually run a tick; the eval's 29 real fires show
0-960s, 60s the typical case), and oil's 24h range rolls exactly on the
hour, so that one minute was enough to move the ground under the fire.
`confirm_fire` cannot see the tick's own future moment, but it can stop
waiting as long as lv_cache does: when the cached-levels pass says a desk
would fire, the watcher now waits CONFIRM_DELAY_S (one more of its own
60s polls - trivial next to the up-to-4-hour gap this module exists to
close) and asks again with a brand-new, uncached levels+mid read
(`levels.summarize_coin(coin, now_ms=...)`, which bypasses both this
module's lv_cache and that function's own 75s cache,
services/strategies/levels.py:122) through the SAME `evaluate()`. For
12:59Z that wait alone lands the recheck at/past 13:00Z, on the other side
of the same hour roll the tick saw: confirmed against the venue's own
1-minute candles for this and the log's other recent fires. The watcher
fires on that second answer, never the first; a desk the fresh read no
longer likes is logged as declined on recheck, not fired. A dispatch
slower than one wait (the log's 960s outlier) still has residual drift
this cannot close - no fixed short wait can see further into the future
than it waits.

Since 2026-11-20 (operator bug: HYPE fired "buys now" through its own 120h
trend gate). 2026-11-20 12:02Z this module fired {"fire": "hype", "mid":
63.802, ..., "verdict": "buys now"} while the HYPE desk's 120h return sat
at about -5.8%, exactly the trend the gate (next_trade.trend_gate,
services/strategies/next_trade.py's HYPE_RULE) exists to veto on a long.
`evaluate()` never passed `next_trade()` its `closes_1h` / `trend_gate_span`
arguments, and `next_trade` applies no veto when either is missing (the
same "missing data blocks nothing" rule `trend_gate` itself follows) -
`scripts/oil_levels.py --next` passed both all along, so the Desk page and
the tick's own CLI already judged this correctly; only this watcher (and
`confirm_fire`, which calls the same `evaluate()`) had the gap. Fixed by
reading `levels["closes_1h"]` (already in every `summarize()` answer) and
`trend_gate_span_for(coin)` inside `evaluate()`, the same two facts
`oil_levels.next_order` reads, so every caller agrees. A second gap fed the
first: `levels.summarize_coin` always fetched 50 hours of 1h candles, one
short of the 121 the 120h gate needs to compute a return at all (`trend_gate`
reports "veto not applied" under that, not a block) - fixed in
`services/strategies/levels.hours_1h_for`, which both `summarize_coin` and
`oil_levels.py --next` now call, so their fetch windows cannot drift apart
either. Desks with no gate (`trend_gate_span_for` returns None: oil, sp500,
silver, gold) read unchanged: `next_trade` never looks at `closes_1h`
without a span, and `hours_1h_for` still returns 50 for them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_SCRIPTS))
sys.path.insert(0, _SCRIPTS)

STATE_DIR = "/tmp/trigger-watch"
LEVELS_REFRESH_S = 600      # the 24h range and ATR barely move inside 10 minutes
SLOT_REFRESH_S = 300        # positions/resting entries: the tick owns those anyway
SIGNER_REFRESH_S = 3600     # is the signing API wallet still approved? (2026-11-09)
SIGNER_REPEAT_S = 6 * 3600  # say it again this often while it stays broken
# One more of this module's own 60s polls before trusting a candidate fire
# (2026-11-17, "Since 2026-11-17" above): a zero-delay recheck agrees with
# the cached pass every time, since lv_cache was already fresh for the
# instant it was read. Waiting this long before the SECOND, uncached read
# is what actually lands on the other side of an hour roll.
CONFIRM_DELAY_S = 60.0


def evaluate(side: Optional[str], thesis_entry: Optional[float], mid: Optional[float],
             levels: Dict[str, Any], coin: str, coin_max_leverage: Optional[float],
             leverage: float, thesis_age_min: Optional[float] = None,
             entry_delay_min: Optional[float] = None) -> Dict[str, Any]:
    """Pure: would the tick trade `coin` at `mid` right now? Same answer as
    GET /api/desks/{id}/next, with the mid refreshed between level reads.
    `thesis_age_min` / `entry_delay_min`: the entry delay (next_trade.
    entry_delay; off while the config has no oil_entry_delay_min).
    `levels["closes_1h"]` (CLOSED 1h closes, oldest first; levels.summarize)
    plus `trend_gate_span_for(coin)` (None on every coin but HYPE) feed
    next_trade's trend gate the same way scripts/oil_levels.py's --next does
    (operator bug, 2026-11-20: this function never passed either, so the
    watcher fired HYPE longs straight through a 120h downtrend the gate
    exists to veto)."""
    from services.strategies.desk_leverage import entry_leverage
    from services.strategies.next_trade import (breakout_for, leverage_fit_for, next_trade, stop_buffer_for,
                                                target_offset_frac_for, trend_gate_span_for)
    r24 = levels.get("range_24h") or {}
    if side not in ("long", "short"):
        return {"ok": False, "reason": f"thesis is {side or 'missing'}"}
    if mid is None or r24.get("low") is None or r24.get("high") is None:
        return {"ok": False, "reason": "no mid or 24h range"}
    buf = stop_buffer_for(coin, levels.get("atr14_1h"), mid)
    if buf is None:
        return {"ok": False, "reason": "no ATR for the stop buffer"}
    out = next_trade(side, float(mid), float(r24["low"]), float(r24["high"]),
                     thesis_entry=thesis_entry, leverage=entry_leverage(coin, leverage),
                     coin_max_leverage=coin_max_leverage, stop_buffer=buf,
                     leverage_fit=leverage_fit_for(coin), offset_frac=target_offset_frac_for(coin),
                     breakout=levels.get("breakout_24h") if breakout_for(coin) else None,
                     thesis_age_min=thesis_age_min, entry_delay_min=entry_delay_min,
                     closes_1h=levels.get("closes_1h"), trend_gate_span=trend_gate_span_for(coin))
    return out


def confirm_fire(side: Optional[str], thesis_entry: Optional[float], coin: str,
                 coin_max_leverage: Optional[float], leverage: float, now_ms: int, *,
                 thesis_age_min: Optional[float] = None, entry_delay_min: Optional[float] = None,
                 fetch_levels: Optional[Callable[[str, int], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The last word before a fire: `evaluate()` again, off a brand-new
    levels+mid read taken AT `now_ms` instead of `lv_cache`'s snapshot.
    Pure (no sleeping here) - `watch()` is the one that waits CONFIRM_DELAY_S
    before calling this with a freshly-read `now_ms`; calling it immediately
    after the first check only reproduces that check (see the module
    docstring, "Since 2026-11-17": a zero-delay reread is not the fix).

    `fetch_levels` defaults to `services.strategies.levels.summarize_coin(coin,
    now_ms=now_ms)`, an explicit `now_ms` bypassing both `lv_cache` here and
    that function's own 75s cache (services/strategies/levels.py:122). Same
    `evaluate()`, no duplicated math; a test or an eval passes its own
    `fetch_levels` to replay a real incident without the live network."""
    from services.strategies import levels as _levels
    fetch_levels = fetch_levels or (lambda c, ms: _levels.summarize_coin(c, now_ms=ms))
    fresh = fetch_levels(coin, now_ms)
    return evaluate(side, thesis_entry, fresh.get("mid"), fresh, coin, coin_max_leverage, leverage,
                    thesis_age_min=thesis_age_min, entry_delay_min=entry_delay_min)


def fetch_headlines(group: str) -> List[Dict[str, Any]]:
    """Targeted Google News RSS for `group` plus oilprice.com for oil. A feed
    that fails is skipped; the next poll tries again."""
    from urllib.parse import quote
    from services.intel import headline_watch as HW
    from services.intel import http_client
    from services.intel.sources.news import GOOGLE_NEWS_URL, OILPRICE_URL, parse_rss_items
    items: List[Dict[str, Any]] = []
    for q in HW.NEWS_QUERIES.get(group, []):
        text, err = http_client.fetch_text(f"{GOOGLE_NEWS_URL}?q={quote(q)}&hl=en-US&gl=US&ceid=US:en", timeout_s=8.0)
        if not err and text:
            try:
                items += parse_rss_items(text, split_source_from_title=True)
            except Exception:  # noqa: BLE001 - a broken feed is skipped
                pass
    if group == "oil":
        text, err = http_client.fetch_text(OILPRICE_URL, timeout_s=8.0)
        if not err and text:
            try:
                items += parse_rss_items(text, default_source="oilprice.com")
            except Exception:  # noqa: BLE001
                pass
    return items


def fetch_posts() -> List[Dict[str, Any]]:
    """Trump's newest posts from the trumpstruth.org archive (Truth Social and
    X have no feed we may read; services/intel/sources/social.py). A failed
    fetch is an empty list; the next poll tries again."""
    from services.intel.sources.social import fetch_trump_posts
    posts, _err = fetch_trump_posts()
    return posts


def fetch_official() -> List[Dict[str, Any]]:
    """The official feeds (whitehouse.gov news; social.OFFICIAL_FEEDS), one
    list of headline rows like fetch_headlines. A feed that fails is skipped."""
    from services.intel.sources.social import fetch_official_feeds
    items, _errs = fetch_official_feeds()
    return items


def fetch_odds(desk: str) -> List[Dict[str, Any]]:
    """Polymarket markets for the desk (the intel source the thesis reads)."""
    from services.intel.sources.predictions import fetch_polymarket
    from services.strategies.desks import get_spec
    rows, _err = fetch_polymarket(get_spec(desk))
    return [{"id": r.id, "question": r.question, "probability": r.probability, "url": r.url,
             "closes_ms": int(r.closes_at.timestamp() * 1000) if r.closes_at else None} for r in rows]


def headline_pass(targets: List[Tuple[str, str, str]], now_s: float, state: Dict[str, Any],
                  history: Dict[str, List[Tuple[int, float]]], mids: Dict[str, float], *,
                  fetch_news: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
                  fetch_odds_fn: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
                  fetch_posts_fn: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                  fetch_official_fn: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                  prime: bool = False) -> List[Tuple[str, Dict[str, Any]]]:
    """One pass of the headline check (services/intel/headline_watch.py).
    targets: (desk, coin, group). Returns (desk, alert) pairs. Mutates
    `state` (seen keys, odds, last polls, last shock per coin, primed
    sources) and `history` ((ms, mid) per coin, trimmed to the shock
    window). `prime`: the first run ever only learns what is already out
    there. A source added later (posts, the official feeds since 2026-11-05)
    is primed on its own first poll: `state["primed_sources"]` names the
    sources already learned, so a watcher restarted on an older state file
    learns the feed instead of alerting on every post already out. The
    fetchers default to this module's at call time, so a test can replace them."""
    from services.intel import headline_watch as HW
    fetch_news = fetch_news or fetch_headlines
    fetch_odds_fn = fetch_odds_fn or fetch_odds
    fetch_posts_fn = fetch_posts_fn or fetch_posts
    fetch_official_fn = fetch_official_fn or fetch_official
    now_ms = int(now_s * 1000)
    out: List[Tuple[str, Dict[str, Any]]] = []
    primed = set(state.get("primed_sources") or [])
    last_shock = state.setdefault("last_shock_ms", {})
    for desk, coin, group in targets:
        mid = mids.get(coin)
        if mid:
            h = history.setdefault(coin, [])
            shock = HW.price_shock(coin, h, now_ms, mid)
            drift = HW.price_drift(coin, h, now_ms, mid)
            h.append((now_ms, mid))
            del h[:-HW.HISTORY_KEEP_MIN]
            # one alert per shock, not one a minute while the move stays in the window
            armed = now_ms - int(last_shock.get(coin) or 0) >= HW.PRICE_REARM_MIN * HW.MIN_MS
            if shock and armed:
                last_shock[coin] = now_ms
                shock.setdefault("group", group)
                out.append((desk, shock))
            # a steady 4h repricing (price_drift): once per coin per drift window
            last_drift = state.setdefault("last_drift_ms", {})
            if drift and not shock and now_ms - int(last_drift.get(coin) or 0) >= HW.DRIFT_REARM_MIN * HW.MIN_MS:
                last_drift[coin] = now_ms
                drift.setdefault("group", group)
                out.append((desk, drift))
    if now_s - float(state.get("last_posts_s") or 0) >= HW.POSTS_POLL_S:
        state["last_posts_s"] = now_s
        seen = set(state.get("seen") or [])
        try:
            posts = fetch_posts_fn()
        except Exception:  # noqa: BLE001 - the archive is best effort
            posts = []
        learn_only = prime or "posts" not in primed
        for desk, _coin, group in targets:
            hits = HW.post_alerts(group, posts, seen, now_ms)
            if not learn_only:
                out += [(desk, a) for a in hits]
        primed.add("posts")
        state["seen"] = list(seen)
    if now_s - float(state.get("last_news_s") or 0) >= HW.NEWS_POLL_S:
        state["last_news_s"] = now_s
        seen = set(state.get("seen") or [])
        # the near-duplicate pool (HW.NEWS_DEDUP_MIN_OVERLAP): persisted in
        # hl_state next to `seen`, so a title already alerted on survives
        # the restart every fire causes, and Finlight (finlight_watch.
        # finlight_pass, same `state`) shares the same pool as the RSS feed.
        recent = state.setdefault("recent_news", [])
        prev_odds = state.setdefault("odds", {})
        try:
            official_items = fetch_official_fn()      # one fetch, every group reads it
        except Exception:  # noqa: BLE001
            official_items = []
        official_learn_only = prime or "official" not in primed
        for desk, _coin, group in targets:
            try:
                news = HW.news_alerts(group, fetch_news(group), seen, now_ms, recent=recent)
            except Exception:  # noqa: BLE001 - news is best effort; price shocks still work
                news = []
            try:
                official = HW.news_alerts(group, official_items, seen, now_ms, shared=True, recent=recent)
            except Exception:  # noqa: BLE001
                official = []
            try:
                odds = HW.odds_alerts(group, fetch_odds_fn(desk), prev_odds, now_ms)
            except Exception:  # noqa: BLE001
                odds = []
            if not prime:
                out += [(desk, a) for a in news + odds]
                if not official_learn_only:
                    out += [(desk, a) for a in official]
        primed.add("official")
        state["seen"] = list(seen)
    state["primed_sources"] = sorted(primed)
    return out


def weekend_fire(desk: str, coin: str, blackout: Dict[str, Any], fired: Dict[str, Any], now_s: float,
                 slot: Callable[[], str]) -> Optional[Dict[str, Any]]:
    """The one weekend_flat fire line for `desk`, or None. Fires when the
    blackout says `flatten`, `fired` has not recorded this window's
    `start_ms` under `<desk>:weekend_flat`, and the desk holds a position or
    a resting entry (`slot()`, '' when flat). Records the window in `fired`
    (the caller writes it) so one weekend wakes the desk once. A slot read
    that fails is treated as flat: the cron tick closes within 15 minutes."""
    key = f"{desk}:weekend_flat"
    start = int(blackout.get("start_ms") or 0)
    if int(fired.get(key) or 0) == start:
        return None
    try:   # the operator's one-weekend hold (services/strategies/weekend.hold_override)
        from pathiel.agents.config_store import read_agent_config
        from services.strategies.weekend import hold_override
        if hold_override(start, read_agent_config() or {}):
            return None
    except Exception:  # noqa: BLE001 - unreadable config: the default (flat) holds
        pass
    try:
        held = slot()
    except Exception:  # noqa: BLE001 - one bad read; the next minute tries again
        return None
    if not held:
        return None
    fired[key] = start
    return {"fire": desk, "coin": coin, "reason": "weekend_flat", "utc": _utc(), "held": held,
            "until_utc": blackout.get("until_utc"), "note": blackout.get("reason")}


def signer_fire(status: Dict[str, Any], fired: Dict[str, Any], now_s: float,
                desk: str) -> Optional[Dict[str, Any]]:
    """The fire line for a signing key Hyperliquid no longer accepts, or None.

    2026-11-09: the signing API wallet dropped off the account and every
    place_bracket failed for 11 hours while the ticks logged NONE. ok=False
    only (ok=None is a failed read, not a revoked key); once per
    SIGNER_REPEAT_S, remembered in `fired["signer"]`. Mutates `fired`. Pure
    otherwise."""
    if status.get("ok") is not False:
        return None
    last = fired.get("signer")
    if last is not None and now_s - float(last) < SIGNER_REPEAT_S:
        return None
    fired["signer"] = now_s
    return {"fire": desk, "reason": "signer", "utc": _utc(),
            "signer": status.get("signer"), "detail": status.get("reason")}


def cooled_down(fired: Dict[str, float], desk: str, now_s: float, cooldown_s: float) -> bool:
    """True when `desk` never fired or fired at least `cooldown_s` ago."""
    if desk not in fired:
        return True
    return now_s - float(fired[desk]) >= cooldown_s


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _log(line: str) -> None:
    with open(os.path.join(STATE_DIR, "watch.log"), "a") as fh:
        fh.write(line + "\n")


def _append_watch_line(desk: str, text: str) -> None:
    """Also note a desk-specific wake-up on THAT DESK'S OWN shared progress
    log (services/strategies/desk_log.log_path(desk) -- the same file the
    desk-tick agent appends `DESK-TICK <desk> ...` lines to, read by
    `GET /api/desks/{id}/log`), prefixed `WATCH <desk> <utc> <text>` so the
    Desk page's Loop Log card can show when the watcher woke this desk
    between its own ticks, not only this module's separate watch.log
    (2026-11-20, Desk page multi-desk build). Called only for a fire that
    names ONE specific desk (a new entry, a weekend-flat close, a headline
    alert) -- never for the signer fire, which names a desk only as a
    representative and is not that desk's own event.

    Best-effort and desk_log-filter-shaped on purpose: desk_log.
    filter_desk_lines keeps a line only when its prefix names the exact
    desk asked for, so a malformed `desk` here would simply never show up
    rather than corrupt another desk's card. A write failure (the loop
    directory missing, a read-only filesystem) must never crash the
    watcher or block the fire it is reporting on, so it is swallowed like
    `_log` is not: the fire itself still prints and still exits 0."""
    try:
        from services.strategies import desk_log
        path = desk_log.log_path(desk)
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(f"WATCH {desk} {_utc()} {text}\n")
    except OSError:
        pass


def watch(*, once: bool, cooldown_min: float, sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.time) -> int:
    import _state_env
    _state_env.load_env_local(os.path.dirname(_SCRIPTS))
    from pathiel.agents.config_store import read_agent_config
    from pathiel.client.exchange import get_max_leverage
    from services.strategies import desk_share, journal, levels
    from services.strategies.desks import get_spec
    from services.strategies.event_blackout import event_blackout
    import desk_slot

    os.makedirs(STATE_DIR, exist_ok=True)
    fired_path = os.path.join(STATE_DIR, "fired.json")
    lv_cache: Dict[str, Any] = {}
    slot_cache: Dict[str, Any] = {}
    coin_max: Dict[str, Optional[float]] = {}
    from services.intel import headline_watch as HW
    from services.intel.scheduled_events import COIN_GROUPS
    hl_state = HW.load_state()
    prime = hl_state is None          # first run ever: learn the feed, alert on nothing
    hl_state = hl_state or {}
    # the mid history survives a restart (the watcher exits on every fire)
    price_hist: Dict[str, List[Tuple[int, float]]] = {
        c: [(int(t), float(m)) for t, m in v] for c, v in (hl_state.get("hist") or {}).items()}

    from services.accounts import signer_status
    signer_checked = 0.0
    finlight = _start_finlight()

    while True:
        now = clock()
        cfg = read_agent_config()
        shares = desk_share.desk_shares(cfg)
        desks: List[str] = list(shares) or desk_share.active_desks(cfg)
        if desks and now - signer_checked >= SIGNER_REFRESH_S:
            signer_checked = now
            fired = _read_json(fired_path)
            line = signer_fire(signer_status.live(max_age_s=0), fired, now, desks[0])
            if line:
                _write_json(fired_path, fired)
                _log(f"{_utc()} FIRE {json.dumps(line)}")
                print(json.dumps(line), flush=True)
                return 0
        lev = float((cfg.get("risk_profile") or {}).get("max_leverage") or 20)
        fired = _read_json(fired_path)
        parts = []
        # Surprise headlines first (operator 2026-11-05 "FIX THEM"): a hit wakes
        # the desk's tick even in a position or a blackout and records the alert
        # (thesis_due rewrites; event_blackout holds new entries 30 min).
        targets = []
        for desk in desks:
            spec = get_spec(desk)
            primary = next((i.coin for i in spec.instruments if i.role == "primary"), spec.instruments[0].coin)
            group = next((g for g, cs in COIN_GROUPS.items() if primary in cs), None)
            if group:
                targets.append((desk, primary, group))
        mids: Dict[str, float] = {}
        for _d, c, _g in targets:
            try:
                mids[c] = float(levels.fetch_mid(c))
            except Exception:  # noqa: BLE001 - no mid this minute
                pass
        hits = headline_pass(targets, now, hl_state, price_hist, mids, prime=prime)
        hits += _finlight_pass(finlight, targets, now, hl_state, prime)
        hl_state["hist"] = price_hist
        HW.save_state(hl_state)
        parts.append("headlines: primed (first run, nothing alerts)" if prime
                     else f"headlines: {len(hits)} hit(s)" if hits else "headlines: quiet")
        if finlight is not None:
            parts.append(_finlight_status(finlight))
        prime = False
        if hits:
            kept = HW.record([a for _d, a in hits])
            wake = [d for d, a in hits if a in kept and cooled_down(fired, f"{d}:headline", now, cooldown_min * 60)]
            wake = list(dict.fromkeys(wake))
            if wake:
                for d in wake:
                    fired[f"{d}:headline"] = now
                _write_json(fired_path, fired)
                first = next(a for d, a in hits if d == wake[0])
                line = {"fire": wake[0], "also": wake[1:], "reason": "headline", "utc": _utc(),
                        "coin": first.get("coin") or next(c for d, c, _g in targets if d == wake[0]),
                        "label": first.get("label"), "headline": first.get("title")}
                for d in wake:
                    _append_watch_line(d, f"action=WAKE headline: {first.get('title') or first.get('label') or 'alert'}")
                _log(f"{_utc()} FIRE {json.dumps(line)}")
                print(json.dumps(line), flush=True)
                return 0
            parts[-1] += " (capped or cooling down)"
        for desk in desks:
            t = journal.latest_thesis(desk)
            spec = get_spec(desk)
            primary = next((i.coin for i in spec.instruments if i.role == "primary"), spec.instruments[0].coin)
            coin = (t.call.instrument if t is not None else None) or primary
            if coin != primary:
                parts.append(f"{desk}: thesis on {coin}, not {primary}")
                continue
            # the thesis side: a headline alert that supports it does not hold its entries (2026-11-06)
            side = t.call.side if t is not None and t.call.side in ("long", "short") else None
            blackout = event_blackout(coin, int(now * 1000), side=side)
            if blackout and blackout.get("flatten"):
                # weekend flat: a held position or resting entry is closed by the tick, once per window
                line = weekend_fire(desk, coin, blackout, fired, now, lambda: _slot(desk_slot, desk))
                if line:
                    _write_json(fired_path, fired)
                    _append_watch_line(desk, f"coin={coin} action=WAKE weekend flat until {line.get('until_utc')}")
                    _log(f"{_utc()} FIRE {json.dumps(line)}")
                    print(json.dumps(line), flush=True)
                    return 0
            if blackout:   # place_bracket would refuse anyway; don't wake a tick for nothing
                parts.append(f"{desk}: {blackout['reason']}")
                continue
            if not cooled_down(fired, desk, now, cooldown_min * 60):
                parts.append(f"{desk}: cooling down")
                continue
            try:
                held = slot_cache.get(desk)
                if held is None or now - held[0] > SLOT_REFRESH_S:
                    out = _slot(desk_slot, desk)
                    slot_cache[desk] = held = (now, out)
                if held[1]:
                    parts.append(f"{desk}: {held[1]} (tick handles it)")
                    continue
                lv = lv_cache.get(coin)
                # also re-read on a new hour: the breakout reads the last CLOSED 1h bar
                if lv is None or now - lv[0] > LEVELS_REFRESH_S or int(lv[0] // 3600) != int(now // 3600):
                    lv_cache[coin] = lv = (now, levels.summarize_coin(coin))
                if coin not in coin_max:
                    coin_max[coin] = float(get_max_leverage(coin))
                mid = mids[coin] if coin in mids else levels.fetch_mid(coin)   # read once a minute
            except Exception as e:  # noqa: BLE001 - one bad read waits for the next minute
                parts.append(f"{desk}: read failed ({str(e)[:80]})")
                continue
            from services.strategies.next_trade import entry_delay_for, thesis_age_minutes
            side = t.call.side if t else None
            thesis_entry = t.call.entry_px if t else None
            age_min = thesis_age_minutes(getattr(t, "created_at", None)) if t else None
            delay_min = entry_delay_for(coin)
            res = evaluate(side, thesis_entry, mid, lv[1], coin, coin_max[coin], lev,
                           thesis_age_min=age_min, entry_delay_min=delay_min)
            if res.get("ok"):
                # the cached-levels pass likes it; wait one more poll and ask
                # the SAME gate again off a brand-new read before committing
                # (2026-11-17, stale-fire fix: module docstring "Since
                # 2026-11-17" - asking again with no wait only reproduces
                # this same answer, since lv_cache was already fresh for now)
                sleep(CONFIRM_DELAY_S)
                confirm = confirm_fire(side, thesis_entry, coin, coin_max[coin], lev, int(clock() * 1000),
                                       thesis_age_min=age_min, entry_delay_min=delay_min)
                if not confirm.get("ok"):
                    why = "; ".join(confirm.get("blockers") or []) or confirm.get("verdict") \
                        or confirm.get("reason") or "declined"
                    parts.append(f"{desk}: mid {mid} {res.get('r_now')}R on cached levels, "
                                 f"but the fresh gate declined: {why}")
                    continue
                res, mid = confirm, confirm.get("mid", mid)
                fired[desk] = now
                _write_json(fired_path, fired)
                line = {"fire": desk, "coin": coin, "utc": _utc(), "mid": mid,
                        "stop_px": res.get("stop_px"), "tp_px": res.get("tp_px"),
                        "r_now": res.get("r_now"), "verdict": res.get("verdict"),
                        "source": res.get("source")}
                _append_watch_line(desk, f"coin={coin} mid={mid} action=WAKE entry rule passed, "
                                         f"{res.get('r_now')}R, run /desk-tick {desk}")
                _log(f"{_utc()} FIRE {json.dumps(line)}")
                print(json.dumps(line), flush=True)
                return 0
            parts.append(f"{desk}: mid {mid} {res.get('verdict') or res.get('reason')}")
        _log(f"{_utc()} " + " | ".join(parts))
        if once:
            print(" | ".join(parts), flush=True)
            return 0
        _wait(finlight, sleep, 60)


def _start_finlight() -> Any:
    """The Finlight runner, or None: no key, or anything at all failed."""
    try:
        from services.intel.finlight_watch import start_runner
        return start_runner()
    except Exception as e:  # noqa: BLE001 - the stream is optional; RSS still runs
        _log(f"{_utc()} finlight not started: {type(e).__name__}")
        return None


def _finlight_pass(runner: Any, targets: List[Tuple[str, str, str]], now_s: float, state: Dict[str, Any],
                   prime: bool) -> List[Tuple[str, Dict[str, Any]]]:
    if runner is None:
        return []
    try:
        from services.intel.finlight_watch import finlight_pass
        return finlight_pass(runner, targets, now_s, state, prime=prime)
    except Exception as e:  # noqa: BLE001
        _log(f"{_utc()} finlight pass failed: {type(e).__name__}")
        return []


def _finlight_status(runner: Any) -> str:
    try:
        return runner.status_line()
    except Exception:  # noqa: BLE001
        return "finlight: status unavailable"


def _wait(runner: Any, sleep: Callable[[float], None], seconds: float) -> None:
    """The minute between passes. With a Finlight runner the wait is cut
    short by a Stage 1 hit (runner.wait); without one, plain sleep."""
    if runner is None:
        sleep(seconds)
        return
    try:
        runner.wait(seconds)
    except Exception:  # noqa: BLE001
        sleep(seconds)


def _slot(desk_slot_mod: Any, desk: str) -> str:
    """'' when the desk holds nothing, else what it holds."""
    from services.accounts import positions as acc_pos
    from services.accounts.view import get_effective_active_account
    from services.strategies import desk_share
    acct = get_effective_active_account()
    if acct is None:
        raise RuntimeError("no active account")
    user = acct.main_address
    raws = [acc_pos._fetch_raw(user, d) for d in acc_pos._dexes_with_margin(user)]
    v = desk_slot_mod.slot_view(desk_share.desk_coins(desk), raws, own_slot=True)
    return f"holds {v['kind']} on {v['holder']}" if v.get("mine") else ""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--cooldown-min", type=float, default=30.0)
    a = ap.parse_args(argv)
    return watch(once=a.once, cooldown_min=a.cooldown_min)


if __name__ == "__main__":
    raise SystemExit(main())
