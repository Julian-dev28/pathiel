#!/usr/bin/env python3
"""Paper (log-only) trader for the oil breakout entry variant
`a6_15m_k1.0_thesis_half` -- 2026-11-19, operator-approved 2-day PAPER test.

Context: `docs/strategy/BREAKOUT_ENTRY_TEST_2026-11-19.md` swept 64 variants
of `desk_tick_sim.Params.pullback_entry == "a6"` (a breakout/continuation
entry layered on top of the live oil rule). Exactly one variant,
`a6_15m_k1.0_thesis_half` (confirm on a closed 15m bar, stop = the broken 8h
level back inside by 1.0x ATR14, target the thesis's own first target, half
the desk's size, one fill per thesis), nominally cleared the adoption bar --
and the doc still rated it NOISE (it is the only one of 64 grid points that
passes; none of its 8 one-axis neighbors also do). The operator asked to see
what it WOULD have done live, next to the real desk, before deciding anything
further. This script is that: log only, no orders, no money.

What it does, every 15 minutes (one invocation = one paper tick):
  1. Reads the live oil thesis journal the live desk itself reads
     (`services.strategies.backtest.data.load_journaled_theses`, READ ONLY)
     and builds the SAME direction series the eval/backtest harness uses
     (`services.strategies.evals.desk_tick_backtest.journaled_direction`).
  2. Fetches live xyz:BRENTOIL 15m/1h candles through the repo's existing,
     public, key-less reader (`pathiel.client.hl_client.fetch_hl_candles`) --
     no disk cache, so every tick sees the real, current book.
  3. Replays `services.strategies.backtest.desk_tick_sim.simulate` -- the
     EXACT same function every eval in this family calls, never
     reimplemented here -- from the test's own `--start` through the latest
     CLOSED bar, using the EXACT `a6_15m_k1.0_thesis_half` Params
     (`A6_OVERRIDES` below, copied verbatim from
     `services.strategies.evals.breakout_entry_test.variants()`) built on
     `services.strategies.evals.live_params.live_brent_params()` -- the same
     function every "live" baseline in this repo is built from. Because the
     window only ever grows forward and the sim's own no-lookahead property
     holds (`desk_tick_sim.py`'s own module docstring, `test_no_lookahead`),
     replaying the whole window fresh every tick and diffing against the
     last tick's own state is exactly equivalent to, and no different from,
     a live step function -- see `build_run`/`diff_tick` below for exactly
     where that equivalence is used, and `tests/test_paper_breakout.py` for
     the gate test that proves this script's signal matches a direct
     `desk_tick_sim.simulate()` call bit for bit on the same bars.
  4. Hold at most one paper position, mechanics identical to the sim: taker
     entry fee + slippage, stop/target on 15m bars, the weekend flat
     (Fri 21:00Z winter / 20:00Z summer, `Params.weekend_off="flatten"`,
     already native to `live_brent_params()`), and the oil desk's scheduled
     event blackouts (weekly EIA report +/-2h, every impact>=3 scheduled
     macro event for Brent -- `event_blackout_windows()` below, fed into the
     sim's own static `Params.event_blackout_windows` gate, not
     reimplemented). The one thing NOT modeled: the live desk's HEADLINE
     blackout (`services.strategies.event_blackout.event_blackout`'s
     surprise-news overlay) -- it is inherently unknowable ahead of the
     moment a headline fires, so it cannot be precomputed into a static
     window the way the EIA/macro calendar can, and reproducing it live
     would mean running the same news-classification step the live desk
     uses, which needs an LLM call this script is barred from making. This
     is the SAME documented gap `BREAKOUT_ENTRY_TEST_2026-11-19.md`'s own
     eval baseline already carries (see its Caveats section) -- carried
     forward here, not newly introduced.
  5. Persists state (`breakout_a6_state.json`) and appends one JSON event
     per OPEN/CLOSE to `breakout_a6.jsonl`, both under
     `$PATHIEL_STATE_DIR/paper/`. Prints one line and appends it to
     `$PATHIEL_OIL_PROGRESS_LOG` (default `/tmp/oil-loop/progress.log`, the
     same file the live desk-tick loop writes to), of the form:

       PAPER-A6 <utc> mid=<mid> pos=<flat|side size@entry stop=.. tp=..> \\
           action=<NONE|OPEN|STOP|TARGET|FLAT> <reason>

  6. `--report`: trades, wins, net R, net $ (at the eval's $64 scale, half
     size on every a6 fill -- every $ figure here scales linearly with the
     real desk's actual equity, same convention the whole study used, since
     the real account balance lives under ~/.pathiel, which this script
     never opens), next to the real desk's own closed fills in the same
     window (`$PATHIEL_STATE_DIR/review/ledger.jsonl`, read only).
  7. `--start`/`--end` (default `start + 48h`): after `--end`, every
     invocation prints the final report and takes no further action --
     the replay window never extends past `--end`, so no new entry can ever
     be considered past it.

No pathiel MCP tools, no order-placing import, no hosted LLM call, no write
to anything under ~/.pathiel or .env.local. `tests/test_paper_breakout.py`
gates all of this, including a static-import check that this file never
imports (directly) a live order-placing symbol.

Usage (every 15 minutes, e.g. `/loop 15m`):

    PATHIEL_STATE_DIR=<pathia state dir> .venv/bin/python scripts/paper_breakout.py \\
        --start 2026-11-19T23:30:00Z

    .venv/bin/python scripts/paper_breakout.py --report
    .venv/bin/python scripts/paper_breakout.py --start 2026-11-19T23:30:00Z --dry-run   # read-only smoke test
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Run directly (`python3 scripts/paper_breakout.py`), same as every other
# script under scripts/ (e.g. oil_levels.py): the repo root is not on
# sys.path by default when a script is invoked this way, only its own
# directory is, so it has to be added by hand.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathiel.agents.atomic_io import append_json_line, append_line, read_json, write_json_atomic  # noqa: E402
from pathiel.client.hl_client import fetch_funding_history, fetch_hl_candles  # noqa: E402
from pathiel.models.types import Candle  # noqa: E402

from services.strategies.backtest import data as D  # noqa: E402
from services.strategies.backtest.desk_tick_sim import DirectionAt, Params, Run, Trade, simulate  # noqa: E402
from services.strategies.backtest.oil_calendar import eia_report_times  # noqa: E402
from services.strategies.event_blackout import AFTER_H as EIA_AFTER_H, BEFORE_H as EIA_BEFORE_H  # noqa: E402
from services.strategies.evals.desk_tick_backtest import journaled_direction  # noqa: E402
from services.strategies.evals.live_params import live_brent_params  # noqa: E402
from services.strategies.evals.no_break_real_fills import default_ledger, latest_rows  # noqa: E402
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, START, closed_only  # noqa: E402

COIN = "xyz:BRENTOIL"
HOUR_MS = D.HOUR_MS
DAY_MS = D.DAY_MS
TEST_WINDOW_MS = 48 * HOUR_MS

# The exact "a6_15m_k1.0_thesis_half" grid point from
# `services.strategies.evals.breakout_entry_test.variants()` -- copied
# verbatim (never re-derived) so this script cannot silently drift from the
# eval's own definition of the variant the operator approved for paper
# testing. See BREAKOUT_ENTRY_TEST_H section of breakout_entry_test.py's
# CONFIRMS/STOPS/TARGETS/SIZES grid: confirm="15m", stop="k1.0" (level_atr,
# k=1.0), target="thesis", size="half".
A6_OVERRIDES: Dict[str, Any] = {
    "pullback_entry": "a6",
    "pb_breakout_entry_h": 8,
    "pb_breakout_cap_one": True,
    "pb_breakout_confirm": "15m",
    "pb_breakout_stop_mode": "level_atr",
    "pb_breakout_stop_k": 1.0,
    "pb_target": "thesis",
    "pb_breakout_size_frac": 0.5,
}
VARIANT_NAME = "a6_15m_k1.0_thesis_half"

ACTIONS = ("NONE", "OPEN", "STOP", "TARGET", "FLAT")

DEFAULT_STATE: Dict[str, Any] = {"trades_closed": 0, "logged_opens": [], "refused_counts": {}}

CandleFetch = Callable[[str, str, int], Sequence[Candle]]
FundingFetch = Callable[[str, int, Optional[int]], Sequence[Dict[str, Any]]]


# ── time helpers (pure) ─────────────────────────────────────────────────────

def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> int:
    """ISO8601 UTC ("...Z" or "+00:00") -> epoch ms. A naive string (no
    offset) is read as UTC."""
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


# ── Params / the pure sim wrapper ───────────────────────────────────────────

def a6_params(starting_equity: float = START, **overrides: Any) -> Params:
    """The exact `a6_15m_k1.0_thesis_half` Params: `live_brent_params()` --
    the SAME function `breakout_entry_test.py`/`stop_cap_test.py` build
    their own "live" baseline from -- plus `A6_OVERRIDES`. `size_slippage`
    is on (the real `EXIT_SLIP_CURVE`, calibrated off the live xyz:BRENTOIL
    book, exactly as the 64-variant grid ran it). `**overrides` applies
    last, same convention as `live_brent_params` itself. Pure."""
    return live_brent_params(size_slippage=True, starting_equity=starting_equity,
                             **{**A6_OVERRIDES, **overrides})


def build_run(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at: DirectionAt, params: Params,
              funding: Optional[Sequence[Dict[str, Any]]], start_ms: int, end_ms: int) -> Run:
    """One `desk_tick_sim.simulate()` call over `[start_ms, end_ms)`: `c15`
    windowed to that range (the bars the sim ticks/decides on), `c1h` given
    up to `end_ms` with NO lower bound (structural levels -- 8h/24h range,
    ATR14 -- need closed history from before `start_ms`). This is the exact
    windowing convention every eval in this family already uses
    (`stop_cap_test._window`/`run_dataset`), not a new one. The ONLY place
    this module calls `simulate` -- `tests/test_paper_breakout.py`'s parity
    test asserts this wrapper and a direct `simulate()` call agree on the
    same bars. Pure given its inputs."""
    w15 = [c for c in c15 if start_ms <= c.t < end_ms]
    w1h = [c for c in c1h if c.t < end_ms]
    return simulate(w15, w1h, direction_at, params, funding)


def event_blackout_windows(coin: str, start_ms: int, end_ms: int) -> Tuple[Tuple[int, int], ...]:
    """The live oil desk's own SCHEDULED blackouts touching `[start_ms,
    end_ms)`: the weekly EIA petroleum report, +/- `event_blackout.BEFORE_H`/
    `AFTER_H` (the SAME pure calendar -- `oil_calendar.eia_report_times` --
    the live overlay, `services.strategies.event_blackout.eia_blackout`,
    reads), and every impact>=3 scheduled macro event for `coin` (FOMC, CPI,
    OPEC+, elections, ... -- `services.intel.scheduled_events.market_events`,
    the same pre-registered, data-file calendar the desk brief itself shows).
    Both are static and pure given the calendar, so they can be precomputed
    once per tick and handed to `Params.event_blackout_windows` -- the sim's
    own existing static-window gate, not a reimplementation of it. Does NOT
    include the live desk's HEADLINE blackout (surprise-news driven, see the
    module docstring). Pure given the calendar files on disk."""
    before_ms, after_ms = int(EIA_BEFORE_H * HOUR_MS), int(EIA_AFTER_H * HOUR_MS)
    windows: List[Tuple[int, int]] = []
    for report_ms, _why in eia_report_times(start_ms - after_ms - HOUR_MS, end_ms + before_ms + HOUR_MS):
        windows.append((report_ms - before_ms, report_ms + after_ms))
    from services.intel.scheduled_events import market_events
    for row in market_events(start_ms, end_ms, coin):
        win = row.get("window")
        if win:
            windows.append((int(win["start_ms"]), int(win["end_ms"])))
    return tuple(sorted(set(windows)))


# ── event classification + formatting (pure) ────────────────────────────────

def classify_exit(exit_reason: Optional[str]) -> str:
    """`Trade.exit_reason` -> one of the printed line's action tokens: STOP
    (a fixed stop or a profit-lock stop -- this variant never arms the lock,
    but the mapping stays total), TARGET, or FLAT for every other close
    (weekend, invalidation, direction flip, time stop, liquidation) -- a
    close that was not the position's own stop or target price. Pure."""
    if exit_reason in ("stop", "lock"):
        return "STOP"
    if exit_reason == "target":
        return "TARGET"
    return "FLAT"


def _kind_text(kind: Optional[str]) -> str:
    return "a6 breakout entry" if kind == "a6" else "live pullback-to-zone entry"


def _open_event(entry_ms: int, side: str, entry_px: float, size: float, stop_px: float, target_px: float,
                kind: Optional[str]) -> Dict[str, Any]:
    return {"event": "OPEN", "at_ms": int(entry_ms), "at_utc": _iso(entry_ms), "side": side,
            "entry_px": round(entry_px, 3), "size": round(size, 6), "stop_px": round(stop_px, 3),
            "target_px": round(target_px, 3), "entry_kind": kind or "live",
            "action": "OPEN", "reason": f"{_kind_text(kind)}, {side}"}


def _close_event(tr: Trade) -> Dict[str, Any]:
    action = classify_exit(tr.exit_reason)
    r_txt = f"{tr.r_multiple:+.2f}R" if tr.r_multiple is not None else "?R"
    reason = (f"{_kind_text(tr.entry_kind)} {tr.side} opened {_iso(tr.entry_time)}@{tr.entry_px:.3f}, "
             f"{tr.exit_reason} @ {tr.exit_px:.3f} ({r_txt}, ${tr.net_pnl_usd:+.2f})")
    return {"event": "CLOSE", "at_ms": int(tr.exit_time), "at_utc": _iso(tr.exit_time), "side": tr.side,
            "exit_px": round(tr.exit_px, 3) if tr.exit_px is not None else None, "exit_reason": tr.exit_reason,
            "r_multiple": round(tr.r_multiple, 3) if tr.r_multiple is not None else None,
            "net_usd": round(tr.net_pnl_usd, 2), "entry_kind": tr.entry_kind or "live",
            "action": action, "reason": reason}


def _idle_reason(prev_refused: Dict[str, int], cur_refused: Dict[str, int]) -> str:
    """Why nothing happened this tick, read off `Run.refused`'s own count
    (which bar-by-bar bookkeeping -- every flat tick with an active thesis
    in market-entry mode increments exactly one reason, `desk_tick_sim.
    simulate_steps` -- increments by exactly one key between two ticks),
    never guessed. Pure."""
    increased = sorted(k for k in cur_refused if cur_refused.get(k, 0) > prev_refused.get(k, 0))
    if increased:
        return "no setup: " + ", ".join(increased)
    return "no new closed bar / no thesis in force"


def diff_tick(prev_state: Dict[str, Any], run: Run) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str, str]:
    """Compare `run` (freshly replayed from the test's own start through
    this tick) against `prev_state` and return `(new_state, new_events,
    action, reason)`. `run.trades` only ever grows between ticks (the sim's
    no-lookahead property: a bar at or before `t` can never change once a
    later bar is appended), so a newly-closed trade is exactly
    `run.trades[prev_state['trades_closed']:]` -- no separate per-tick state
    machine needed. `logged_opens` (entry_ms already reported as OPEN,
    whether still open or already closed) makes OPEN idempotent across a
    missed/late invocation: a trade that opened AND closed inside one gap
    still gets both events, in order, exactly once. Pure."""
    trades = run.trades
    prev_closed = int(prev_state.get("trades_closed", 0))
    logged_opens = {int(x) for x in prev_state.get("logged_opens", [])}
    opened: List[Dict[str, Any]] = []
    closed: List[Dict[str, Any]] = []

    if run.open_position_at_end is not None:
        info, lv = run.open_position_at_end, run.open_position_levels
        entry_ms = int(lv["opened_at"])
        if entry_ms not in logged_opens:
            opened.append(_open_event(entry_ms, info["side"], info["entry_px"], info["size"],
                                      lv["stop_px"], lv["target_px"], lv.get("entry_kind")))
            logged_opens.add(entry_ms)

    for tr in trades[prev_closed:]:
        entry_ms = int(tr.entry_time)
        if entry_ms not in logged_opens:
            opened.append(_open_event(entry_ms, tr.side, tr.entry_px, tr.size, tr.stop_px, tr.target_px,
                                      tr.entry_kind))
            logged_opens.add(entry_ms)
        closed.append(_close_event(tr))

    events = sorted(opened + closed, key=lambda e: e["at_ms"])
    new_state = dict(prev_state)
    new_state["trades_closed"] = len(trades)
    new_state["logged_opens"] = sorted(logged_opens)
    new_state["refused_counts"] = dict(run.refused)

    if closed:
        action, reason = closed[-1]["action"], closed[-1]["reason"]
    elif opened:
        action, reason = "OPEN", opened[-1]["reason"]
    elif run.open_position_at_end is not None:
        action, reason = "NONE", "holding"
    else:
        action = "NONE"
        reason = _idle_reason(prev_state.get("refused_counts", {}) or {}, dict(run.refused))
    return new_state, events, action, reason


def describe_position(run: Run) -> str:
    info, lv = run.open_position_at_end, run.open_position_levels
    if info is None:
        return "flat"
    return (f"{info['side']} {info['size']:.4f}@{info['entry_px']:.3f} "
            f"stop={lv['stop_px']:.3f} tp={lv['target_px']:.3f}")


def format_tick_line(now_iso: str, mid: float, pos_desc: str, action: str, reason: str) -> str:
    return f"PAPER-A6 {now_iso} mid={mid:.4f} pos={pos_desc} action={action} {reason}"


# ── IO: state / paths / data (thin, injectable) ─────────────────────────────

def default_state_dir() -> str:
    return os.environ.get("PATHIEL_STATE_DIR") or os.path.expanduser("~/Documents/code/pathia/.state")


def default_theses_dir() -> str:
    env = os.environ.get("PATHIEL_OIL_THESES_DIR", "")
    if env:
        return env
    return os.path.join(default_state_dir(), "strategies", "oil", "theses")


def default_progress_log() -> str:
    return os.environ.get("PATHIEL_OIL_PROGRESS_LOG", "/tmp/oil-loop/progress.log")


def paper_paths(state_dir: Optional[str] = None) -> Tuple[str, str]:
    """(events_jsonl_path, state_json_path) under `<state_dir>/paper/`."""
    base = os.path.join(state_dir or default_state_dir(), "paper")
    return os.path.join(base, "breakout_a6.jsonl"), os.path.join(base, "breakout_a6_state.json")


def load_theses_direction(theses_dir: str) -> Tuple[DirectionAt, List[Any]]:
    theses = D.load_journaled_theses(theses_dir)
    return journaled_direction(theses), theses


def fetch_live_candles(coin: str, fetch: Optional[CandleFetch] = None, n15: int = 1000,
                       n1h: int = 400) -> Tuple[List[Candle], List[Candle]]:
    """15m x `n15` (1000 ~ 10.4 days: the 48h test window plus the a6 15m-
    confirm warmup plus margin) and 1h x `n1h` (400 ~ 16.6 days: ATR14/8h/
    24h range history well before any test's own --start) through the
    repo's existing live, public, key-less candle reader. No disk cache (a
    paper tick must see the real, current book every time)."""
    fetch = fetch or fetch_hl_candles
    return list(fetch(coin, "15m", n15)), list(fetch(coin, "1h", n1h))


def fetch_live_funding(coin: str, start_ms: int, end_ms: int, fetch: Optional[FundingFetch] = None
                       ) -> List[Dict[str, Any]]:
    fetch = fetch or fetch_funding_history
    return list(fetch(coin, start_ms, end_ms))


def load_state(state_path: str) -> Dict[str, Any]:
    return read_json(state_path, default=dict(DEFAULT_STATE))


def save_state(state_path: str, state: Dict[str, Any]) -> None:
    write_json_atomic(state_path, state, indent=1, sort_keys=True)


def append_events(events_path: str, events: Sequence[Dict[str, Any]]) -> None:
    for ev in events:
        append_json_line(events_path, ev)


def load_live_ledger_rows(state_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """The real desk's own closed trades, READ ONLY
    (`$PATHIEL_STATE_DIR/review/ledger.jsonl`, `services.review.ledger`'s
    append-only store), one row per trade id (the last line wins, same
    dedup `no_break_real_fills.latest_rows` already uses). [] when the
    ledger does not exist yet (nothing closed, or `services.review` has
    never run)."""
    path = default_ledger() if state_dir is None else os.path.join(state_dir, "review", "ledger.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path) as fh:
        return latest_rows(fh)


# ── reporting (pure given data) ──────────────────────────────────────────────

def summarize_paper(run: Run) -> Dict[str, Any]:
    trades = run.trades
    wins = [t for t in trades if t.net_pnl_usd > 0]
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    a6 = [t for t in trades if t.entry_kind == "a6"]
    live_rule = [t for t in trades if t.entry_kind != "a6"]
    return {
        "trades": len(trades), "wins": len(wins),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "net_r": round(sum(rs), 2) if rs else 0.0,
        "net_usd": round(sum(t.net_pnl_usd for t in trades), 2),
        "a6_trades": len(a6), "a6_net_usd": round(sum(t.net_pnl_usd for t in a6), 2),
        "live_rule_trades": len(live_rule), "live_rule_net_usd": round(sum(t.net_pnl_usd for t in live_rule), 2),
        "liquidated": run.liquidated,
        "open_position": run.open_position_at_end,
    }


def summarize_live_ledger(rows: Sequence[Dict[str, Any]], coin: str, start_ms: int, end_ms: int) -> Dict[str, Any]:
    matched = [r for r in rows if r.get("coin") == coin and start_ms <= int(r.get("entry_ms", -1)) < end_ms]
    wins = [r for r in matched if (r.get("net_usd") or 0) > 0]
    return {
        "coin": coin, "trades": len(matched), "wins": len(wins),
        "win_rate": round(len(wins) / len(matched), 3) if matched else None,
        "net_usd": round(sum(r.get("net_usd") or 0.0 for r in matched), 2),
        "net_r": round(sum(r.get("r_mult") or 0.0 for r in matched), 2),
    }


def render_report(run: Run, start_ms: int, end_ms: int, now_ms: int, live_summary: Dict[str, Any],
                  variant_name: str = VARIANT_NAME) -> str:
    p = summarize_paper(run)
    lines = [
        f"PAPER-A6 report {_iso(start_ms)} -> {_iso(end_ms)} (now {_iso(now_ms)})",
        f"variant: {variant_name}  ($64 eval-scale equity, half desk size on a6 fills -- "
        f"every $ figure scales linearly with the real desk's own equity, same convention "
        f"BREAKOUT_ENTRY_TEST_2026-11-19.md used throughout)",
        "",
        f"paper (this variant):  trades={p['trades']}  wins={p['wins']}  win_rate={p['win_rate']}  "
        f"net_r={p['net_r']}  net_usd={p['net_usd']:+.2f}",
        f"  by source:  live-rule fills={p['live_rule_trades']} (${p['live_rule_net_usd']:+.2f})  "
        f"a6-only fills={p['a6_trades']} (${p['a6_net_usd']:+.2f})",
    ]
    if run.liquidated:
        lines.append("  LIQUIDATED during this window")
    if p["open_position"]:
        o = p["open_position"]
        lines.append(f"  still open: {o['side']} {o['size']:.4f}@{o['entry_px']:.3f}, "
                     f"marked {o['mark_px']:.3f}, unrealized ${o['unrealized_pnl_usd']:+.2f} (not counted above)")
    lines += [
        "",
        f"live desk (same window, {live_summary['coin']}, from the review ledger):  "
        f"trades={live_summary['trades']}  wins={live_summary['wins']}  win_rate={live_summary['win_rate']}  "
        f"net_r={live_summary['net_r']}  net_usd={live_summary['net_usd']:+.2f}",
        "",
        f"delta (paper net$ - live net$): {p['net_usd'] - live_summary['net_usd']:+.2f}",
    ]
    return "\n".join(lines)


# ── orchestration ─────────────────────────────────────────────────────────

def run_tick(*, start_ms: int, end_ms: int, now_ms: int, coin: str = COIN, theses_dir: Optional[str] = None,
            state_path: Optional[str] = None, events_path: Optional[str] = None,
            params: Optional[Params] = None, fetch_candles: Optional[CandleFetch] = None,
            fetch_funding: Optional[FundingFetch] = None, persist: bool = True) -> Dict[str, Any]:
    """One paper tick. Fetches live data, replays the a6 variant from
    `start_ms` through `min(now_ms, end_ms)`, diffs against persisted state,
    and (when `persist`) writes the new state + any new events. Returns a
    dict with the printed line, the new events, the new state and the `Run`
    -- `main()`'s thin CLI wrapper, and every test, call this directly."""
    theses_dir = theses_dir or default_theses_dir()
    window_end = min(now_ms, end_ms)

    direction_at, _theses = load_theses_direction(theses_dir)
    c15_raw, c1h_raw = fetch_live_candles(coin, fetch_candles)
    c15 = closed_only(c15_raw, FIFTEEN_MS, now_ms)
    c1h = closed_only(c1h_raw, HOUR_MS, now_ms)
    funding = fetch_live_funding(coin, start_ms, window_end, fetch_funding)

    p = params or a6_params(event_blackout_windows=event_blackout_windows(coin, start_ms, end_ms))
    run = build_run(c15, c1h, direction_at, p, funding, start_ms, window_end)

    prev_state = load_state(state_path) if state_path else dict(DEFAULT_STATE)
    new_state, events, action, reason = diff_tick(prev_state, run)
    new_state.update({"start_ms": start_ms, "end_ms": end_ms, "coin": coin, "last_run_at": _iso(now_ms),
                      "last_tick_bar_ms": c15[-1].t if c15 else None})

    pos_desc = describe_position(run)
    mid = c15[-1].c if c15 else (c1h[-1].c if c1h else 0.0)
    line = format_tick_line(_iso(now_ms), mid, pos_desc, action, reason)
    ended = now_ms >= end_ms

    if persist and state_path and events_path:
        append_events(events_path, events)
        save_state(state_path, new_state)

    return {"line": line, "events": events, "state": new_state, "run": run, "action": action, "reason": reason,
           "ended": ended, "mid": mid, "pos": pos_desc}


def run_report(*, start_ms: int, end_ms: int, now_ms: int, coin: str = COIN, theses_dir: Optional[str] = None,
               state_dir: Optional[str] = None, params: Optional[Params] = None,
               fetch_candles: Optional[CandleFetch] = None, fetch_funding: Optional[FundingFetch] = None,
               ledger_rows: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    theses_dir = theses_dir or default_theses_dir()
    window_end = min(now_ms, end_ms)
    direction_at, _theses = load_theses_direction(theses_dir)
    c15_raw, c1h_raw = fetch_live_candles(coin, fetch_candles)
    c15 = closed_only(c15_raw, FIFTEEN_MS, now_ms)
    c1h = closed_only(c1h_raw, HOUR_MS, now_ms)
    funding = fetch_live_funding(coin, start_ms, window_end, fetch_funding)
    p = params or a6_params(event_blackout_windows=event_blackout_windows(coin, start_ms, end_ms))
    run = build_run(c15, c1h, direction_at, p, funding, start_ms, window_end)
    rows = load_live_ledger_rows(state_dir) if ledger_rows is None else ledger_rows
    live_summary = summarize_live_ledger(rows, coin, start_ms, end_ms)
    return render_report(run, start_ms, end_ms, now_ms, live_summary)


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", help="ISO8601 UTC; required on the first tick, else read from state")
    ap.add_argument("--end", help="ISO8601 UTC; default --start + 48h")
    ap.add_argument("--now", help="override wall-clock 'now' (ISO8601 UTC) -- for testing only")
    ap.add_argument("--coin", default=COIN)
    ap.add_argument("--theses-dir", default=None)
    ap.add_argument("--state-dir", default=None, help="override $PATHIEL_STATE_DIR for this run")
    ap.add_argument("--progress-log", default=None, help="override $PATHIEL_OIL_PROGRESS_LOG")
    ap.add_argument("--report", action="store_true", help="print the summary report and exit; never trades")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and print one tick; never writes state, the jsonl log or progress.log")
    a = ap.parse_args(argv)

    now_ms = parse_iso(a.now) if a.now else int(time.time() * 1000)
    state_dir = a.state_dir or default_state_dir()
    events_path, state_path = paper_paths(state_dir)
    theses_dir = a.theses_dir or default_theses_dir()
    progress_log = a.progress_log or default_progress_log()

    prev_state = {} if a.dry_run else load_state(state_path)
    start_ms = parse_iso(a.start) if a.start else prev_state.get("start_ms")
    if start_ms is None:
        print("paper_breakout: --start is required (ISO8601 UTC, e.g. 2026-11-19T23:30:00Z) -- "
              "no prior state found at " + state_path, file=sys.stderr)
        return 2
    if (a.start and not a.report and not a.dry_run and prev_state.get("start_ms") not in (None, start_ms)):
        print(f"paper_breakout: this paper test already started at {_iso(prev_state['start_ms'])}; "
              f"refusing to restart at {_iso(start_ms)}. Use --state-dir for a separate test.", file=sys.stderr)
        return 2
    end_ms = parse_iso(a.end) if a.end else (prev_state.get("end_ms") or (start_ms + TEST_WINDOW_MS))

    if a.report:
        print(run_report(start_ms=start_ms, end_ms=end_ms, now_ms=now_ms, coin=a.coin, theses_dir=theses_dir,
                         state_dir=state_dir))
        return 0

    result = run_tick(start_ms=start_ms, end_ms=end_ms, now_ms=now_ms, coin=a.coin, theses_dir=theses_dir,
                      state_path=None if a.dry_run else state_path, events_path=None if a.dry_run else events_path,
                      persist=not a.dry_run)
    print(result["line"], flush=True)
    if not a.dry_run:
        append_line(progress_log, result["line"])

    if result["ended"]:
        print(run_report(start_ms=start_ms, end_ms=end_ms, now_ms=now_ms, coin=a.coin, theses_dir=theses_dir,
                         state_dir=state_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
