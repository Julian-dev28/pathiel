#!/usr/bin/env python3
"""Replay harness: one thesis per day, price data only, for a desk over a
window of history -- so there are enough AI-written theses to test the
AI-informed backtest variants (services/strategies/backtest/desk_tick_
sim.py's Params.ai_levels / conviction_leverage) against. The 9 real
journaled oil theses (2026-10-18 to 10-30) are too few on their own to
trust a variant's numbers.

Walks backward from `--days` days ago to yesterday, one as-of time per
day (`--as-of-hour` UTC, default 16:00), and for each day D:

  1. builds the desk's DeskAnalytics AS OF D with a candle fetcher
     (`truncating_fetcher`) that only ever returns candles whose bar
     CLOSED at or before D -- no lookahead, proven directly by
     services/thesis/tests/test_replay.py::test_truncating_fetcher_*.
     Venue metadata (funding, max leverage, ...) is fetched once, live,
     and reused for every day (it does not have a historical time
     series to replay); the account read is stubbed to a fixed assumed
     equity, never the real account.
  2. runs the REAL thesis generation path (services.thesis.api.
     write_thesis: prompt assembly, one services.llm.complete call
     pinned to --model, the validator, and its repair/retry round)
     against that analytics, with NO intel packet (price data only --
     every written thesis's usage row says so) and no prior thesis
     (every day is a fresh, cold call -- the replay asks what the model
     would have called that day from the market alone, not an
     incremental update off a thesis it never actually wrote).
  3. writes the resulting Thesis as JSON, in the exact shape the live
     journal uses (Thesis.model_dump(mode="json")), directly under
     `--out-dir`, so services.strategies.backtest.data.
     load_journaled_theses(--out-dir) reads it as a journaled thesis.

Resumable: a day whose thesis file already exists (by its deterministic
id, services.thesis.assemble.make_thesis_id) is skipped without spending
a call -- rerun after a crash/interrupt picks up where it left off.
Every day, written or skipped, gets one row in `<out-dir>/../summary.json`
(cost, tokens, model, validator outcome, skip reason).

NEVER touches the live desk's own journal: PATHIEL_STATE_DIR is pointed
at a fresh temp dir for the whole run (nothing the pipeline this harness
calls can write under ~/Documents/code/pathia/.state), and `--out-dir` is
asserted to not be inside the live state dir before anything is written.

Costs real money: one Claude Code CLI call per day, on `--model` (default
"opus" -- NEVER "fable": the operator is near the weekly Fable quota and
the live desk needs Fable for its own rewrites). Not wired into pytest.

    .venv/bin/python -m services.thesis.evals.replay --days 2     # smoke: 2 LLM calls
    .venv/bin/python -m services.thesis.evals.replay --days 30    # the full replay
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from pathiel.agents.atomic_io import write_json_atomic
from pathiel.models.types import Candle

from contracts.strategies.models import AccountSnapshot, DeskSpec

logger = logging.getLogger(__name__)

DEFAULT_DESK_ID = "oil"
DEFAULT_DAYS = 30
DEFAULT_AS_OF_HOUR = 16     # UTC
DEFAULT_MODEL = "opus"      # never "fable" -- see module docstring

# Resolved at call time (default-arg time, inside main()), never frozen at
# import -- a test can set PATHIEL_STATE_DIR / pass explicit paths without
# fighting a module-level constant.
LIVE_STATE_DIR = os.path.join(os.path.expanduser("~"), "Documents", "code", "pathia", ".state")


def default_out_dir(desk_id: str) -> str:
    return os.path.join("/tmp", "thesis-replay", desk_id, "theses")


def default_cache_dir(desk_id: str) -> str:
    return os.path.join("/tmp", "thesis-replay", desk_id, "candle-cache")


def default_summary_path(out_dir: str) -> str:
    return os.path.join(os.path.dirname(os.path.normpath(out_dir)), "summary.json")


def assert_not_live_state_dir(path: str) -> None:
    """Raise if `path` IS the live desk's state dir or anything under it.
    Both sides are resolved to an absolute, symlink-free path first, so a
    relative --out-dir or a symlinked temp dir can't slip past a naive
    string compare."""
    real = os.path.realpath(path)
    real_live = os.path.realpath(LIVE_STATE_DIR)
    if real == real_live or real.startswith(real_live + os.sep):
        raise ValueError(
            f"refusing to write under the live desk's state dir ({LIVE_STATE_DIR}); "
            f"this path resolved to {real}")


# ── no-lookahead candle fetcher ─────────────────────────────────────────────

def _interval_ms(interval: str) -> int:
    """How long one bar of `interval` is open before it closes. Matches
    services/strategies/analytics.py's HOUR_MS convention, generalized to
    every interval build_analytics requests ("1d" for every instrument,
    "1h" for primaries)."""
    if not interval or interval[-1] not in "dhm":
        raise ValueError(f"unknown candle interval {interval!r}")
    n = int(interval[:-1]) if interval[:-1] else 1
    unit_ms = {"d": 86_400_000, "h": 3_600_000, "m": 60_000}[interval[-1]]
    return n * unit_ms


def truncating_fetcher(all_candles: Dict[str, Dict[str, Sequence[Candle]]], now: datetime):
    """A services.strategies.analytics.CandleFetcher that only ever
    returns candles whose bar CLOSED at or before `now` -- the no-
    lookahead contract an as-of-D replay needs from build_analytics(...,
    now=now). `all_candles` is {coin: {interval: [Candle, ...]}}, already
    fetched up to the real present ONCE outside this function; this does
    no I/O of its own, just filters + takes the most recent `count`.

    A bar's close time is its open time (`c.t`) plus the interval's own
    duration -- the same rule analytics.py's `_hourly_closed` applies to
    1h bars, generalized here to every interval build_analytics
    requests."""
    now_ms = int(now.timestamp() * 1000)

    def fetch(coin: str, interval: str, count: int) -> List[Candle]:
        bars = all_candles.get(coin, {}).get(interval, [])
        dur_ms = _interval_ms(interval)
        closed = sorted((c for c in bars if c.t + dur_ms <= now_ms), key=lambda c: c.t)
        return closed[-count:] if count else closed

    return fetch


# ── one full day's worth of raw candles, fetched once and reused ───────────

def fetch_all_candles(spec: DeskSpec, cache_dir: str, days: int,
                      fetch_cached=None) -> Dict[str, Dict[str, List[Candle]]]:
    """Every instrument coin's full daily history, and every PRIMARY
    coin's hourly history far enough back to cover the whole replay
    window -- fetched ONCE (disk-cached under `cache_dir`, so a resumed
    run never re-fetches) and reused for every day D through
    `truncating_fetcher`. `fetch_cached` defaults to services.strategies.
    backtest.data.fetch_candles_cached (the live, public candleSnapshot
    read); injectable for tests."""
    from services.strategies import analytics as analytics_mod
    from services.strategies.backtest import data as D

    fetch_cached = fetch_cached or D.fetch_candles_cached
    coins = [i.coin for i in spec.instruments]
    primaries = [i.coin for i in spec.instruments if i.role == "primary"]
    out: Dict[str, Dict[str, List[Candle]]] = {c: {} for c in coins}
    for coin in coins:
        out[coin]["1d"] = list(fetch_cached(coin, "1d", analytics_mod.DAILY_CANDLE_COUNT, cache_dir))
    # HOURLY_CANDLE_COUNT (60) is all build_analytics ever asks for in one
    # call; the replay needs that many CLOSED bars available on the
    # EARLIEST day too, so the raw fetch reaches back `days` further still,
    # plus the same warm-up pad analytics.py's own HOURLY_CANDLE_COUNT implies.
    from services.strategies.analytics import HOURLY_CANDLE_COUNT
    hourly_count = (days + 5) * 24 + HOURLY_CANDLE_COUNT
    for coin in primaries:
        out[coin]["1h"] = list(fetch_cached(coin, "1h", hourly_count, cache_dir))
    return out


def stub_account_fetcher() -> AccountSnapshot:
    """Never the real account: a fixed assumed equity, the same fallback
    build_analytics itself uses when no account is configured."""
    from services.strategies.analytics import DEFAULT_ASSUMED_EQUITY_USD
    return AccountSnapshot(equity_usd=DEFAULT_ASSUMED_EQUITY_USD, source="assumed")


# ── the replay window ────────────────────────────────────────────────────────

def replay_window(days: int, as_of_hour: int, end: Optional[datetime] = None) -> List[datetime]:
    """`days` UTC as-of timestamps, oldest first: yesterday back through
    `days` days ago, each at `as_of_hour`:00 UTC. Never includes "today"
    (today's candles may still be forming at any hour we'd pick)."""
    end = end or datetime.now(timezone.utc)
    end_date = end.date()
    return [datetime(end_date.year, end_date.month, end_date.day, as_of_hour, tzinfo=timezone.utc)
           - timedelta(days=i) for i in range(days, 0, -1)]


# ── one day ──────────────────────────────────────────────────────────────────

def existing_thesis_path(desk_id: str, out_dir: str, day: datetime) -> Optional[str]:
    """The path a thesis for `day` would already be written to, if a
    previous run already wrote it (resumability) -- else None. Computed
    from the SAME deterministic id write_thesis's own assemble.py derives
    from (desk_id, now), so this never has to guess a filename."""
    from services.thesis.assemble import make_thesis_id

    path = os.path.join(out_dir, f"{make_thesis_id(desk_id, day)}.json")
    return path if os.path.exists(path) else None


def replay_day(spec: DeskSpec, all_candles: Dict[str, Dict[str, Sequence[Candle]]],
              meta_fetcher: Callable[[], Dict[str, Any]], day: datetime, model: str, out_dir: str,
              *, write_thesis_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """Build analytics as of `day`, write one thesis through the real
    generation path, save it to `out_dir`, and return its summary row.
    Skips (no LLM call) if that day's thesis already exists. `write_thesis_fn`
    defaults to services.thesis.api.write_thesis; injectable so the gate
    test can run this whole function with a stub and no network/LLM."""
    existing = existing_thesis_path(spec.id, out_dir, day)
    if existing is not None:
        return {"day": day.isoformat(), "path": existing, "skipped": True, "reason": "already_written"}

    from services.strategies import analytics as analytics_mod

    if write_thesis_fn is None:
        from services.thesis.api import write_thesis as write_thesis_fn

    fetcher = truncating_fetcher(all_candles, day)
    analytics = analytics_mod.build_analytics(
        spec, candle_fetcher=fetcher, meta_fetcher=meta_fetcher,
        account_fetcher=stub_account_fetcher, now=day)
    thesis = write_thesis_fn(spec, analytics, None, None, model=model, now=day)

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{thesis.id}.json")
    write_json_atomic(path, thesis.model_dump(mode="json"))

    usage = thesis.usage
    return {
        "day": day.isoformat(), "thesis_id": thesis.id, "path": path, "skipped": False,
        "side": thesis.call.side, "instrument": thesis.call.instrument,
        "entry_px": thesis.call.entry_px, "stop_px": thesis.call.stop_px,
        "confidence": thesis.call.confidence,
        "model": usage.model if usage else None,
        "input_tokens": usage.input_tokens if usage else None,
        "output_tokens": usage.output_tokens if usage else None,
        "cost_usd": usage.cost_usd if usage else None,
        "duration_s": usage.duration_s if usage else None,
        "validator_outcome": "degraded" if (usage and usage.skipped_reason) else "ok",
        "degraded_reason": usage.skipped_reason if usage else None,
    }


# ── __main__ ─────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--desk", default=DEFAULT_DESK_ID)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--as-of-hour", type=int, default=DEFAULT_AS_OF_HOUR,
                    help="UTC hour of day to replay each day's thesis at")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help='LLM model alias for every call this run makes (default "opus"; never "fable")')
    ap.add_argument("--out-dir", default=None, help="default /tmp/thesis-replay/<desk>/theses")
    ap.add_argument("--cache-dir", default=None, help="default /tmp/thesis-replay/<desk>/candle-cache")
    ap.add_argument("--summary", default=None, help="default <out-dir>/../summary.json")
    args = ap.parse_args(argv)

    if args.model == "fable":
        print('FATAL: --model "fable" is banned for this harness (the operator is near the '
             "weekly Fable quota and the live desk needs it for its own rewrites). Use opus.")
        return 2

    out_dir = args.out_dir or default_out_dir(args.desk)
    cache_dir = args.cache_dir or default_cache_dir(args.desk)
    summary_path = args.summary or default_summary_path(out_dir)
    assert_not_live_state_dir(out_dir)
    assert_not_live_state_dir(cache_dir)

    # Isolate every write the pipeline underneath this harness makes
    # (services/llm's usage.jsonl, a validator rejection dump) to a fresh
    # temp dir for this run -- never the live desk's ~/Documents/code/
    # pathia/.state. Resolved at CALL time by every module that reads it
    # (services.llm.config.state_dir, services.thesis.api's rejection
    # path), so setting it here, before any of that code runs, is enough.
    state_dir = tempfile.mkdtemp(prefix="pathiel-thesis-replay-")
    os.environ["PATHIEL_STATE_DIR"] = state_dir
    assert_not_live_state_dir(state_dir)   # paranoia: mkdtemp should never collide, but prove it

    from services.strategies import analytics as analytics_mod
    from services.strategies.desks import get_spec

    spec = get_spec(args.desk)
    if spec is None:
        print(f"FATAL: unknown desk {args.desk!r}")
        return 2

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    print(f"[replay] desk={args.desk} days={args.days} model={args.model} "
         f"out_dir={out_dir} cache_dir={cache_dir} state_dir={state_dir}")
    print("[replay] price data only: no intel packet is built or passed to any day's thesis")

    all_candles = fetch_all_candles(spec, cache_dir, args.days)
    meta_once = analytics_mod.default_meta_fetcher()
    meta_fetcher = lambda: meta_once  # noqa: E731 - fetched once, reused every day on purpose

    days = replay_window(args.days, args.as_of_hour)
    summary: List[Dict[str, Any]] = []
    total_cost = 0.0
    for i, day in enumerate(days, 1):
        row = replay_day(spec, all_candles, meta_fetcher, day, args.model, out_dir)
        summary.append(row)
        total_cost += row.get("cost_usd") or 0.0
        if row.get("skipped"):
            print(f"[replay] {i}/{len(days)} {day.date()} SKIP (already written: {row['path']})")
        else:
            cost_txt = f"${row['cost_usd']:.4f}" if row.get("cost_usd") is not None else "n/a"
            print(f"[replay] {i}/{len(days)} {day.date()} {row['validator_outcome']:<8} "
                 f"side={row['side']} cost={cost_txt} -> {row['path']}")

    os.makedirs(os.path.dirname(summary_path) or ".", exist_ok=True)
    with open(summary_path, "w") as fh:
        json.dump({"desk": args.desk, "days": args.days, "model": args.model,
                  "out_dir": out_dir, "total_cost_usd": round(total_cost, 4), "rows": summary},
                 fh, indent=2, default=str)
    n_written = sum(1 for r in summary if not r.get("skipped"))
    n_degraded = sum(1 for r in summary if r.get("validator_outcome") == "degraded")
    print(f"\n[replay] done: {n_written} written, {len(summary) - n_written} skipped, "
         f"{n_degraded} degraded, total cost ${total_cost:.4f}")
    print(f"[replay] summary: {summary_path}")
    print(f"[replay] theses dir (for data.load_journaled_theses): {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
