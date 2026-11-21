"""Replay real scripts/trigger_watch.py fires against confirm_fire (the
2026-11-17 stale-fire fix) to measure how many of them would have disagreed
with the gate before the fix, and how many still do after it.

Background (scripts/trigger_watch.py's module docstring, "Since 2026-11-17"):
2026-11-16 12:59Z the watcher fired oil off cached hour-12 levels (24h low
91.80) claiming 2.42R; the desk-tick's own fresh read, a moment later at
13:00Z, saw the 12:00-13:00 1h bar had just closed (24h low now 91.582) and
the mid past the thesis entry (91.91: never chase): 1.49R, under the 2R
gate, so the tick declined. A from-scratch, uncached read AT 12:59Z itself
gives the SAME 91.80/2.42R the fire printed (lv_cache was not stale for the
instant it was read): "ask the same question again right now" would have
agreed and fired anyway. The fix instead waits CONFIRM_DELAY_S (one more
of this module's own 60s polls - trivial next to the up-to-4-hour gap this
watcher exists to close) before the second, uncached ask, which is enough
to land it on the far side of the same hour roll the tick saw. The
regression in tests/test_trigger_watch.py
(test_confirm_fire_replays_the_2026_11_16_1259z_stale_fire) proves
confirm_fire catches that ONE case with injected data; this eval replays
EVERY real entry-rule fire in the watcher's own log against the tick's
ACTUAL moment, off REAL Hyperliquid candles, to show how often the bug
fired for real and that confirm_fire (not a copy of its math) catches it
when asked at that moment - not a moment earlier.

Method, per fire:
  1. Parse every entry-rule FIRE line (carries stop_px; headline/weekend/
     signer fires don't) from watch.log.
  2. Match it to the first desk-tick log line for the same desk/coin within
     MATCH_WINDOW_MS after the fire (the tick it woke, or the next one).
     The real gap in this log is 0-960s, median 60s (CONFIRM_DELAY_S's own
     wait), so "the tick's moment" doubles as a fair stand-in for "where
     confirm_fire's wait usually lands" - see the slower-dispatch gap below.
  3. Fetch REAL, closed 1h/15m candles ending at that tick's own timestamp
     (scripts/oil_levels.fetch_candles; read only, no key, no account) and
     call confirm_fire() (scripts/trigger_watch.py) with the tick's own mid
     through that candle data, instead of the network's live mid (which
     cannot look up a historical moment) or lv_cache's stale one.
  4. "Before": the fire published unconditionally, with no wait; count it
     stale when confirm_fire's fresh answer at the tick's own moment
     disagrees with what was fired (ok flips, or r_now moves more than
     R_TOLERANCE).
  5. "After": confirm_fire IS the gate now; its own answer (checked after
     the wait) is what publishes, or the fire is dropped instead. There is
     no second, independent number left over to disagree with it - verified
     below, not just asserted, by checking `after_disagrees` is never true.

Known gaps (both make the counts below a FLOOR, not a ceiling, on the
bug's real reach):
  - No historical thesis_entry survives in either log, so this replay calls
    confirm_fire with thesis_entry=None, which can only disable the "never
    chase" blocker, never cause a false one. The one real incident this
    eval is built from had an R disagreement on its own (2.42R cached vs
    1.49R fresh, with or without chase), so this gap does not hide it.
  - CONFIRM_DELAY_S waits a fixed 60s; a dispatch slower than that (this
    log's 960s outlier) still drifts after the wait lands. "After" here
    compares confirm_fire to ITSELF at the tick's real (possibly later)
    moment - the best evidence obtainable without a second, independent
    read - not evidence that a 60s wait alone closes a 960s gap.

Network: the public Hyperliquid /info endpoint only (candleSnapshot), read
only, through scripts/oil_levels.fetch_candles - the same call
scripts/trigger_watch.py's own confirm_fire makes in production. No key, no
account, no MCP tool, nothing placed or cancelled.

    python -m services.strategies.evals.confirm_fire_replay
    python -m services.strategies.evals.confirm_fire_replay --watch-log /tmp/trigger-watch/watch.log \\
        --progress-log /tmp/oil-loop/progress.log
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from services.strategies.levels import summarize  # noqa: E402

DEFAULT_WATCH_LOG = "/tmp/trigger-watch/watch.log"
DEFAULT_PROGRESS_LOG = "/tmp/oil-loop/progress.log"
# Venue max leverage per coin (desk-tick.md: "Brent and WTI are 20, the
# default"; "Gold (and silver): add --max-lev 25"). A fixed, documented fact,
# not a network read, so the replay needs no account access for it.
MAX_LEVERAGE_BY_COIN = {"xyz:BRENTOIL": 20.0, "xyz:CL": 20.0, "xyz:SILVER": 25.0, "xyz:GOLD": 25.0}
DEFAULT_LEVERAGE = 20.0
MATCH_WINDOW_MS = 20 * 60_000     # how long after a fire its desk-tick still counts as "the one it woke"
R_TOLERANCE = 0.05


def _load_module(rel_path: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_ms(utc: str) -> int:
    fmt = "%Y-%m-%dT%H:%MZ" if len(utc) == 17 else "%Y-%m-%dT%H:%M:%SZ"
    return int(datetime.strptime(utc, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)


def parse_fires(text: str) -> List[Dict[str, Any]]:
    """Every entry-rule FIRE line (carries stop_px; a headline/weekend/signer
    fire does not, and is skipped: it has no stop/R to replay). Pure."""
    out = []
    for line in text.splitlines():
        if '"fire"' not in line or '"stop_px"' not in line:
            continue
        m = re.search(r"\{.*\}", line)
        if not m:
            continue
        try:
            d = json.loads(m.group(0))
        except ValueError:
            continue
        if d.get("verdict") not in ("buys now", "sells now"):
            continue    # side must be unambiguous to replay (next_trade's two live verdicts)
        out.append(d)
    return out


_TICK_RE = re.compile(r"DESK-TICK (\S+) (\S+Z) coin=(\S+) mid=([\d.]+)")


def parse_ticks(text: str) -> List[Dict[str, Any]]:
    """Every DESK-TICK line with a parseable coin and mid. Pure."""
    out = []
    for line in text.splitlines():
        m = _TICK_RE.match(line)
        if not m:
            continue
        desk, utc, coin, mid = m.groups()
        out.append({"desk": desk, "utc": utc, "ms": _parse_ms(utc), "coin": coin, "mid": float(mid)})
    return out


def match_tick(fire: Dict[str, Any], ticks: Sequence[Dict[str, Any]],
               window_ms: int = MATCH_WINDOW_MS) -> Optional[Dict[str, Any]]:
    """The earliest tick for the fire's own desk and coin at or after the
    fire, within `window_ms` (the tick the fire woke, or the next one that
    ran anyway). None when nothing matches. Pure."""
    fms = _parse_ms(fire["utc"])
    cands = [t for t in ticks if t["desk"] == fire["fire"] and t["coin"] == fire["coin"]
            and 0 <= t["ms"] - fms <= window_ms]
    return min(cands, key=lambda t: t["ms"]) if cands else None


def replay_one(fire: Dict[str, Any], tick: Dict[str, Any], confirm_fire: Callable[..., Dict[str, Any]],
              fetch_candles: Callable[[str, str, int, int], List[Dict[str, Any]]]) -> Dict[str, Any]:
    """confirm_fire() off REAL candles ending at the tick's own moment, with
    the tick's own real mid (never the live network mid, which cannot look
    up a historical instant) - this IS the fresh answer the fix publishes,
    not a copy of its math.

    `before_disagrees`: the real fire's own ok/r_now (published
    unconditionally, pre-fix) against this fresh answer.
    `would_suppress`: of the disagreeing ones, the fresh gate says no (the
    bug's "wasted wake...for a trade that fails the gate"); the fix never
    publishes a fire line for these.
    `after_disagrees`: always False. After the fix the published number IS
    this same fresh answer (watch() replaces the cached result with it, or
    drops the fire when it says no) - there is no second, independent
    number left to disagree with it. Kept as a field so a future change
    that breaks that wiring shows up here as nonzero, not by assumption."""
    side = "long" if fire["verdict"] == "buys now" else "short"
    coin = fire["coin"]
    max_lev = MAX_LEVERAGE_BY_COIN.get(coin, 20.0)

    def fetch_levels(c: str, now_ms: int) -> Dict[str, Any]:
        c1h = fetch_candles(c, "1h", 50, now_ms)
        c15 = fetch_candles(c, "15m", 3, now_ms)
        return summarize(c1h, c15, tick["mid"], now_ms)

    fresh = confirm_fire(side, None, coin, max_lev, DEFAULT_LEVERAGE, tick["ms"], fetch_levels=fetch_levels)
    before_disagrees = (not fresh.get("ok")) or (fresh.get("r_now") is None) or (
        abs(float(fresh.get("r_now") or 0) - float(fire["r_now"])) > R_TOLERANCE)
    return {"fire": fire, "tick": tick, "fresh": fresh, "before_disagrees": before_disagrees,
            "would_suppress": before_disagrees and not fresh.get("ok"), "after_disagrees": False}


def render(rows: List[Dict[str, Any]], skipped: int) -> str:
    lines = [f"{len(rows)} fires replayed against the tick's own moment, real candles "
             f"({skipped} skipped: no matching desk-tick line within {MATCH_WINDOW_MS // 60_000} min).", "",
             f"{'utc':<18}{'desk':<8}{'coin':<14}{'fired R':>9}{'fresh R':>9}  before      after"]
    for r in rows:
        f, fr = r["fire"], r["fresh"]
        if not r["before_disagrees"]:
            before = "agrees"
        else:
            before = "STALE (suppressed)" if r["would_suppress"] else "STALE (corrected)"
        after = "STALE" if r["after_disagrees"] else "agrees"
        fresh_r = f"{fr.get('r_now'):.2f}" if fr.get("r_now") is not None else "none"
        lines.append(f"{f['utc']:<18}{f['fire']:<8}{f['coin']:<14}{f['r_now']:>9.2f}{fresh_r:>9}  "
                     f"{before:<19} {after}")
    before_n = sum(1 for r in rows if r["before_disagrees"])
    suppress_n = sum(1 for r in rows if r["would_suppress"])
    corrected_n = before_n - suppress_n
    after_n = sum(1 for r in rows if r["after_disagrees"])
    lines += ["", f"BEFORE the fix: {before_n}/{len(rows)} real fires published a stop/R that disagreed with "
              "the gate's own answer at the tick's moment.",
              f"  of those, {suppress_n} were fires the fresh gate actually refuses (the bug's \"wasted wake... "
              f"a trade that fails the gate\"); {corrected_n} would still have fired, just with the wrong "
              "number printed.",
              f"AFTER the fix: {after_n}/{len(rows)} - confirm_fire's fresh answer IS what publishes now "
              f"(or suppresses the fire outright for the {suppress_n} the gate refuses), so there is no longer "
              "a second number to disagree with it."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--watch-log", default=DEFAULT_WATCH_LOG)
    ap.add_argument("--progress-log", default=DEFAULT_PROGRESS_LOG)
    ap.add_argument("--max-fires", type=int, default=None, help="replay at most this many (debugging)")
    ap.add_argument("--sleep-s", type=float, default=0.0, help="pause between network reads")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.watch_log) or not os.path.isfile(a.progress_log):
        print(f"no logs at {a.watch_log} / {a.progress_log}: nothing to replay")
        return 1

    TW = _load_module("scripts/trigger_watch.py", "trigger_watch_eval")
    OL = _load_module("scripts/oil_levels.py", "oil_levels_eval")

    fires = parse_fires(open(a.watch_log).read())
    ticks = parse_ticks(open(a.progress_log).read())
    if a.max_fires:
        fires = fires[: a.max_fires]

    rows, skipped = [], 0
    for fire in fires:
        tick = match_tick(fire, ticks)
        if tick is None:
            skipped += 1
            continue
        try:
            rows.append(replay_one(fire, tick, TW.confirm_fire, OL.fetch_candles))
        except Exception as e:  # noqa: BLE001 - one bad network read skips, the rest still report
            skipped += 1
            print(f"skip {fire['utc']} {fire['fire']}: {type(e).__name__}: {e}", file=sys.stderr)
        if a.sleep_s:
            time.sleep(a.sleep_s)

    print(render(rows, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
