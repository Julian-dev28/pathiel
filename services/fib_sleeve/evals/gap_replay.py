"""Eval: replay the sleeve's REAL tick history against public Brent
candles, and measure the bug the replace-in-one-tick fix closes.

    python3 -m services.fib_sleeve.evals.gap_replay [--log PATH] [--coin COIN]

The bug (2026-11-17 07:51Z-08:59Z, see `README.md` "Live differs..." #3): a
tick cancelled a stale resting entry and returned; the replacement was
only placed on the NEXT tick, sometimes well over the usual 15 minutes
later (ticks were stalling too that day), leaving a real stretch of
wall-clock time with NOTHING of the sleeve's own resting on the book.
`tick.py` now cancels, confirms the cancel took, and places the
replacement in the SAME tick (`"replace_entry"`), one log line for both.

This eval does not re-run the fixed code against replayed ACCOUNT state
(the position/orders the sub-account actually held on 2026-11-16/17 are
gone; faking them would mean testing a mock, not reading the venue). It
reads what really happened - the sleeve's own `FIB-TICK` lines in
`/tmp/oil-loop/progress.log` - and asks two honest questions against REAL
public candles:

  "before"  from the real tick history alone: every stretch between one
            logged tick and the next where the action means nothing of
            the sleeve's own was resting (`cancel_entry`, `no_order`,
            `killed`, `blocked`, `refused`, `place_incomplete`) counts as
            a gap, in minutes. This is what actually happened - no
            simulation involved.
  "after"   the same history, except at every `cancel_entry` tick this
            eval calls the real, pure `rule.plan_entry` (no network
            beyond the candles already fetched, no key, no account read)
            on REAL Brent candles AS OF that exact timestamp, asking:
            would today's fixed code have named a valid replacement right
            then? If yes, the gap this fix closes collapses to zero (the
            replacement rests immediately, same tick). If the rule itself
            has nothing to place (the level is already spent, the weekend
            is open, ...), the fix does not manufacture an order that was
            never valid, and the gap stays exactly what it was - the fix
            only ever shrinks a gap, never invents a trade.
  "tagged"  for every "before" gap where a valid replacement existed, did
            Brent's own 15m candles trade through that level while
            nothing was resting there? That is this bug made concrete: a
            price touching a level with no order on it.

Pass (exit 0): the log parsed, candles were read, and "after" minutes is
never greater than "before" minutes, on any day. Paid: nothing. Network:
public candle reads only (two calls, no key, no account, no MCP tool).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from pathiel.models.types import Candle
from services.fib_sleeve import rule
from services.fib_sleeve.config import SleeveConfig

DEFAULT_LOG = "/tmp/oil-loop/progress.log"
DEFAULT_COIN = "xyz:BRENTOIL"
ASSUMED_MAX_LEVERAGE = 20.0   # the cap every fib_sleeve test and the live config both use for this coin

LINE_RE = re.compile(r"^FIB-TICK\s+(\S+)\s+(.*)$")
VARIANT_TAGS = ("range24", "zigzag")
TERMINAL_ACTIONS = ("STOPPED",)       # an operator annotation, not a tick decision: the timeline ends here

# Something of the sleeve's own is resting or protected - no gap.
RESTING_ACTIONS = {"resting", "place", "would_place", "replace_entry", "would_replace",
                   "hold", "ratchet", "close", "entry_filled", "cancel_failed"}
# Nothing of the sleeve's own is resting - a gap, until the next tick.
GAP_ACTIONS = {"cancel_entry", "no_order", "killed", "blocked", "refused", "place_incomplete", "disabled"}
# No decision was made (a read failed) - the resting state carries forward unchanged.
CARRY_FORWARD_ACTIONS = {"error"}


@dataclass
class Tick:
    ts_ms: int
    utc: str
    action: str
    why: str


@dataclass
class Gap:
    start_ms: int
    end_ms: int
    action: str
    why: str
    replacement: Optional[Dict[str, Any]] = field(default=None)   # set only for a cancel_entry gap


def _parse_ts(iso: str) -> int:
    """'2026-11-17T07:51Z' -> epoch ms, UTC."""
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def parse_log(path: str) -> List[Tick]:
    """Every `FIB-TICK` line, oldest first, up to (not including) a
    `STOPPED` operator annotation if one is present - after that the
    sleeve was not running, so there is no tick-to-tick gap to measure."""
    out: List[Tick] = []
    with open(path) as fh:
        for raw in fh:
            m = LINE_RE.match(raw.strip())
            if not m:
                continue
            iso, rest = m.group(1), m.group(2).split(" ")
            action, why_tokens = rest[0], rest[1:]
            if action in VARIANT_TAGS:
                action = why_tokens[0] if why_tokens else action
                why_tokens = why_tokens[1:]
            if action in TERMINAL_ACTIONS:
                break
            out.append(Tick(ts_ms=_parse_ts(iso), utc=iso, action=action, why=" ".join(why_tokens)))
    return sorted(out, key=lambda t: t.ts_ms)


def gaps(ticks: Sequence[Tick]) -> List[Gap]:
    """Every [tick, next tick) stretch where nothing of the sleeve's own
    was resting, from the REAL tick history alone - no simulation. The
    last tick never opens a gap (there is no "next" to bound it)."""
    out: List[Gap] = []
    resting = True   # unknown before the first tick; never overstate the bug by assuming a gap
    for i in range(len(ticks) - 1):
        t, nxt = ticks[i], ticks[i + 1]
        if t.action in RESTING_ACTIONS:
            resting = True
        elif t.action in GAP_ACTIONS:
            resting = False
        # CARRY_FORWARD_ACTIONS (error): resting is left exactly as it was
        if not resting:
            out.append(Gap(start_ms=t.ts_ms, end_ms=nxt.ts_ms, action=t.action, why=t.why))
    return out


def simulate_replacement(cfg: SleeveConfig, hours_all: Sequence[Candle], bars_all: Sequence[Candle],
                         ts_ms: int) -> Dict[str, Any]:
    """What the FIXED code's `rule.plan_entry` names right at `ts_ms`, on
    REAL closed candles as of that exact moment - pure, no network, no
    account. `resting=False`: nothing of the sleeve's own rests yet at
    `ts_ms` (the stale entry was just cancelled), exactly the state a
    fresh placement is judged from.

    `price`: the close of the last 15m bar already CLOSED as of `ts_ms`
    (`bars[-1].c`, the same list `plan_entry` itself reads) - not the
    close of whatever bar merely OPENED at or before `ts_ms`. A bar
    opening exactly at `ts_ms` is the one still forming at that instant;
    picking its (by replay time, long since known) close would leak
    30-or-more minutes of future price into "the price a live tick read
    right then", which is exactly the kind of lookahead this eval exists
    to avoid."""
    hours = rule.closed(hours_all, rule.HOUR_MS, ts_ms)
    bars = rule.closed(bars_all, rule.FIFTEEN_MS, ts_ms)
    if len(hours) < 2 * 14 or not bars_all:
        return {"ok": False, "reason": "insufficient_candle_history_for_replay"}
    if not bars:
        return {"ok": False, "reason": "no_price_for_replay"}
    return rule.plan_entry(cfg, hours, bars, bars[-1].c, ts_ms, ASSUMED_MAX_LEVERAGE, resting=False)


def tagged(bars_all: Sequence[Candle], start_ms: int, end_ms: int, entry_px: float, side: str) -> bool:
    """Did price trade through `entry_px` during [start_ms, end_ms) - the
    gap this fix closes, made concrete: a touch with no order resting."""
    window = [b for b in bars_all if start_ms <= b.t < end_ms]
    return any(b.l <= entry_px for b in window) if side == "long" else any(b.h >= entry_px for b in window)


def minutes_by_day(windows: Sequence[tuple]) -> Dict[str, float]:
    """(start_ms, end_ms) pairs -> minutes per UTC day (YYYY-MM-DD)."""
    out: Dict[str, float] = defaultdict(float)
    for start_ms, end_ms in windows:
        cur = start_ms
        while cur < end_ms:
            day_start = datetime.fromtimestamp(cur / 1000, tz=timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0)
            day_end_ms = int((day_start + timedelta(days=1)).timestamp() * 1000)
            chunk_end = min(end_ms, day_end_ms)
            out[day_start.strftime("%Y-%m-%d")] += (chunk_end - cur) / 60_000.0
            cur = chunk_end
    return dict(out)


def run(log_path: str, coin: str, fetch_candles: Any) -> Dict[str, Any]:
    cfg = SleeveConfig()   # the live default: range24, L382, atr1, T272, be50 - matches every real log line
    ticks = parse_log(log_path)
    if len(ticks) < 2:
        return {"error": f"fewer than 2 FIB-TICK lines in {log_path}; nothing to replay"}

    hours_all = sorted(fetch_candles(coin, "1h", rule.HOURS_WANTED), key=lambda b: b.t)
    bars_all = sorted(fetch_candles(coin, "15m", rule.BARS_WANTED), key=lambda b: b.t)
    if not hours_all or not bars_all:
        return {"error": "candle read failed: no 1h or 15m bars came back"}

    before_windows = [(g.start_ms, g.end_ms) for g in gaps(ticks)]
    all_gaps = gaps(ticks)

    cancel_events: List[Dict[str, Any]] = []
    after_windows: List[tuple] = []
    tagged_gaps: List[Dict[str, Any]] = []
    for g in all_gaps:
        if g.action != "cancel_entry":
            after_windows.append((g.start_ms, g.end_ms))
            continue
        plan = simulate_replacement(cfg, hours_all, bars_all, g.start_ms)
        would_replace = bool(plan.get("ok"))
        gap_minutes_before = (g.end_ms - g.start_ms) / 60_000.0
        was_tagged = False
        if would_replace:
            after_windows.append((g.start_ms, g.start_ms))   # the fix collapses this gap to zero
            was_tagged = tagged(bars_all, g.start_ms, g.end_ms, plan["entry"], plan["side"])
            if was_tagged:
                tagged_gaps.append({
                    "gap_start_utc": datetime.fromtimestamp(g.start_ms / 1000, tz=timezone.utc).isoformat(),
                    "gap_end_utc": datetime.fromtimestamp(g.end_ms / 1000, tz=timezone.utc).isoformat(),
                    "gap_minutes": round(gap_minutes_before, 1),
                    "would_have_rested": {"side": plan["side"], "entry": plan["entry"]},
                    "why_the_old_entry_was_cancelled": g.why,
                })
        else:
            after_windows.append((g.start_ms, g.end_ms))   # the fix would not have placed anything either
        cancel_events.append({
            "cancelled_at_utc": datetime.fromtimestamp(g.start_ms / 1000, tz=timezone.utc).isoformat(),
            "next_tick_utc": datetime.fromtimestamp(g.end_ms / 1000, tz=timezone.utc).isoformat(),
            "why_cancelled": g.why,
            "gap_minutes_before": round(gap_minutes_before, 1),
            "would_replace_same_tick": would_replace,
            "replacement": ({"side": plan["side"], "entry": plan["entry"], "stop": plan["stop"],
                            "target": plan["target"]} if would_replace else {"reason": plan.get("reason")}),
            "gap_minutes_after": round(0.0 if would_replace else gap_minutes_before, 1),
            "tagged": was_tagged if would_replace else None,
        })

    before_by_day = minutes_by_day(before_windows)
    after_by_day = minutes_by_day(after_windows)
    days = sorted(set(before_by_day) | set(after_by_day))
    by_day = [{"day": d, "before_minutes": round(before_by_day.get(d, 0.0), 1),
              "after_minutes": round(after_by_day.get(d, 0.0), 1)} for d in days]

    return {
        "log": log_path, "coin": coin, "cfg_variant": cfg.variant_name,
        "ticks_read": len(ticks), "span_utc": [ticks[0].utc, ticks[-1].utc],
        "gaps_before_total": len(all_gaps), "cancel_entry_events": len(cancel_events),
        "would_replace_count": sum(1 for e in cancel_events if e["would_replace_same_tick"]),
        "by_day": by_day,
        "total_before_minutes": round(sum(before_by_day.values()), 1),
        "total_after_minutes": round(sum(after_by_day.values()), 1),
        "cancel_entry_events_detail": cancel_events,
        "tagged_gaps": tagged_gaps,
    }


def check(result: Dict[str, Any]) -> List[str]:
    """The failures of one replay; [] is a pass. Pure."""
    if "error" in result:
        return [result["error"]]
    bad: List[str] = []
    for row in result["by_day"]:
        if row["after_minutes"] > row["before_minutes"] + 1e-6:
            bad.append(f"{row['day']}: after ({row['after_minutes']}m) > before ({row['before_minutes']}m)")
    return bad


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Replay the fib sleeve's real tick history for the replace-gap fix.")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--coin", default=DEFAULT_COIN)
    args = ap.parse_args(argv)
    from pathiel.client.hl_client import fetch_hl_candles
    result = run(args.log, args.coin, fetch_hl_candles)
    failures = check(result)
    print(json.dumps(result, indent=2, default=str))
    print(json.dumps({"eval": "fib_sleeve.gap_replay", "pass": not failures, "failures": failures}))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
