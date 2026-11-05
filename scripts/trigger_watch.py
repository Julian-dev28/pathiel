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
anything but its own log, cooldown file and headline state.

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


def evaluate(side: Optional[str], thesis_entry: Optional[float], mid: Optional[float],
             levels: Dict[str, Any], coin: str, coin_max_leverage: Optional[float],
             leverage: float) -> Dict[str, Any]:
    """Pure: would the tick trade `coin` at `mid` right now? Same answer as
    GET /api/desks/{id}/next, with the mid refreshed between level reads."""
    from services.strategies.next_trade import breakout_for, leverage_fit_for, next_trade, stop_buffer_for
    r24 = levels.get("range_24h") or {}
    if side not in ("long", "short"):
        return {"ok": False, "reason": f"thesis is {side or 'missing'}"}
    if mid is None or r24.get("low") is None or r24.get("high") is None:
        return {"ok": False, "reason": "no mid or 24h range"}
    buf = stop_buffer_for(coin, levels.get("atr14_1h"), mid)
    if buf is None:
        return {"ok": False, "reason": "no ATR for the stop buffer"}
    out = next_trade(side, float(mid), float(r24["low"]), float(r24["high"]),
                     thesis_entry=thesis_entry, leverage=leverage,
                     coin_max_leverage=coin_max_leverage, stop_buffer=buf,
                     leverage_fit=leverage_fit_for(coin),
                     breakout=levels.get("breakout_24h") if breakout_for(coin) else None)
    return out


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
    return [{"id": r.id, "question": r.question, "probability": r.probability, "url": r.url} for r in rows]


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
            h.append((now_ms, mid))
            del h[:-(HW.PRICE_WINDOW_MIN + 2)]
            # one alert per shock, not one a minute while the move stays in the window
            armed = now_ms - int(last_shock.get(coin) or 0) >= HW.PRICE_REARM_MIN * HW.MIN_MS
            if shock and armed:
                last_shock[coin] = now_ms
                shock.setdefault("group", group)
                out.append((desk, shock))
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
        prev_odds = state.setdefault("odds", {})
        try:
            official_items = fetch_official_fn()      # one fetch, every group reads it
        except Exception:  # noqa: BLE001
            official_items = []
        official_learn_only = prime or "official" not in primed
        for desk, _coin, group in targets:
            try:
                news = HW.news_alerts(group, fetch_news(group), seen, now_ms)
            except Exception:  # noqa: BLE001 - news is best effort; price shocks still work
                news = []
            try:
                official = HW.news_alerts(group, official_items, seen, now_ms, shared=True)
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
    try:
        held = slot()
    except Exception:  # noqa: BLE001 - one bad read; the next minute tries again
        return None
    if not held:
        return None
    fired[key] = start
    return {"fire": desk, "coin": coin, "reason": "weekend_flat", "utc": _utc(), "held": held,
            "until_utc": blackout.get("until_utc"), "note": blackout.get("reason")}


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

    while True:
        now = clock()
        cfg = read_agent_config()
        shares = desk_share.desk_shares(cfg)
        desks: List[str] = list(shares) or desk_share.active_desks(cfg)
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
        hl_state["hist"] = price_hist
        HW.save_state(hl_state)
        parts.append("headlines: primed (first run, nothing alerts)" if prime
                     else f"headlines: {len(hits)} hit(s)" if hits else "headlines: quiet")
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
            blackout = event_blackout(coin, int(now * 1000))
            if blackout and blackout.get("flatten"):
                # weekend flat: a held position or resting entry is closed by the tick, once per window
                line = weekend_fire(desk, coin, blackout, fired, now, lambda: _slot(desk_slot, desk))
                if line:
                    _write_json(fired_path, fired)
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
            res = evaluate(t.call.side if t else None, t.call.entry_px if t else None, mid,
                           lv[1], coin, coin_max[coin], lev)
            if res.get("ok"):
                fired[desk] = now
                _write_json(fired_path, fired)
                line = {"fire": desk, "coin": coin, "utc": _utc(), "mid": mid,
                        "stop_px": res.get("stop_px"), "tp_px": res.get("tp_px"),
                        "r_now": res.get("r_now"), "verdict": res.get("verdict"),
                        "source": res.get("source")}
                _log(f"{_utc()} FIRE {json.dumps(line)}")
                print(json.dumps(line), flush=True)
                return 0
            parts.append(f"{desk}: mid {mid} {res.get('verdict') or res.get('reason')}")
        _log(f"{_utc()} " + " | ".join(parts))
        if once:
            print(" | ".join(parts), flush=True)
            return 0
        sleep(60)


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
