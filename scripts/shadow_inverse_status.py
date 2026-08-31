#!/usr/bin/env python3
"""Grade the exact opposite side of recorded shadow-book signals.

This is a research-only counterfactual.  It changes no ledger records and
places no orders.  For each retained episode it keeps the original entry,
signal time, stop, horizon, and funding treatment, and changes only LONG to
SHORT or SHORT to LONG.  A passing result is a new hypothesis, not permission
to reverse a live book: liquidity, borrowability, and an independent forward
ledger still need to be checked.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_ENV = _REPO / ".env.local"
if _ENV.is_file():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _value = _line.partition("=")
            os.environ.setdefault(_key.strip(), _value.strip())

from pathia.agents import shadow_ledger as SL  # noqa: E402
from pathia.client.hl_client import fetch_funding_history, fetch_hl_candles  # noqa: E402


DEFAULT_BOOKS = [
    "premium_fade_short",
    "neg_funding_fade",
    "young_listings",
    "news_catalyst",
]


def inverse_side(side: str) -> str:
    """Return the tradeable opposite for a normalized ledger side."""
    if side == "long":
        return "short"
    if side == "short":
        return "long"
    raise ValueError(f"cannot invert unknown side {side!r}")


def inverse_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deep-copy records and flip only their side, never mutating the ledger."""
    out: List[Dict[str, Any]] = []
    for record in records:
        flipped = copy.deepcopy(record)
        flipped["side"] = inverse_side(str(flipped.get("side") or "long"))
        out.append(flipped)
    return out


def _required_candle_counts(records: Iterable[Dict[str, Any]], now_ms: int) -> Dict[Tuple[str, str], int]:
    """Fetch each coin/interval once, with enough history for its oldest signal."""
    needs: Dict[Tuple[str, str], int] = {}
    for record in records:
        signal_t = int(record.get("signal_bar_t") or 0)
        horizon = float(record.get("horizon_days") or 0.0)
        if not signal_t or horizon <= 0:
            continue
        interval, bar_ms, bars = SL.grade_interval(horizon)
        age = max(0, (now_ms - signal_t) // bar_ms)
        key = (str(record.get("coin") or ""), interval)
        needs[key] = max(needs.get(key, 0), int(age + bars + 8))
    return needs


def _make_fetchers(records: List[Dict[str, Any]], now_ms: int):
    """Create cached public-data readers compatible with ``grade_records``."""
    candle_needs = _required_candle_counts(records, now_ms)
    candle_cache: Dict[Tuple[str, str], List[Any]] = {}
    funding_ranges: Dict[str, Tuple[int, int]] = {}
    for record in records:
        coin = str(record.get("coin") or "")
        start = int(record.get("signal_bar_t") or 0)
        horizon = float(record.get("horizon_days") or 0.0)
        if not coin or not start or horizon <= 0:
            continue
        end = start + SL.resolve_after_ms(horizon)
        previous = funding_ranges.get(coin)
        funding_ranges[coin] = (start, end) if previous is None else (min(previous[0], start), max(previous[1], end))
    funding_cache: Dict[str, List[Dict[str, Any]]] = {}

    def fetch_fwd(coin: str, signal_t: int, _bars: int, interval: str) -> List[Any]:
        key = (str(coin), interval)
        if key not in candle_cache:
            candle_cache[key] = fetch_hl_candles(key[0], interval, candle_needs.get(key, _bars + 8))
        return [bar for bar in candle_cache[key] if int(getattr(bar, "t", 0)) > int(signal_t)]

    def fetch_funding(coin: str, _start: int, _end: int) -> List[Dict[str, Any]]:
        coin = str(coin)
        if coin not in funding_cache:
            start, end = funding_ranges.get(coin, (_start, _end))
            funding_cache[coin] = fetch_funding_history(coin, start, end)
        return funding_cache[coin]

    return fetch_fwd, fetch_funding, candle_cache


_COST_12BPS = 0.0012
_MAX_ENTRIES_PER_EPISODE = 3000


def matched_null(detail: List[Dict[str, Any]],
                 candle_cache: Dict[Tuple[str, str], List[Any]],
                 draws: int, rng: random.Random) -> Optional[Dict[str, Any]]:
    """Same-coin random-time null (SWARM-RULES): for every graded episode,
    simulate the SAME side/stop/horizon entered at every candle in the coin's
    fetched window, then bootstrap `draws` portfolio means picking one random
    entry per episode. Price-only both sides — funding is absent from the null,
    so comparing against the funding-inclusive mean would fake excess. A raw
    positive book mean in a trending window is beta until it beats this."""
    per_episode: List[List[float]] = []
    skipped = 0
    obs: List[float] = []
    for d in detail:
        bars = candle_cache.get((str(d.get("coin")), str(d.get("interval"))), [])
        side = str(d.get("side"))
        stop_pct = float(d.get("stop_pct") or 0.0)
        n_bars = int(d.get("n_bars") or 0)
        last_entry = len(bars) - n_bars - 1
        rets: List[float] = []
        if n_bars > 0 and last_entry >= 1:
            idxs = range(0, last_entry)
            if last_entry > _MAX_ENTRIES_PER_EPISODE:
                idxs = rng.sample(range(0, last_entry), _MAX_ENTRIES_PER_EPISODE)
            for i in idxs:
                entry_px = float(getattr(bars[i], "c", 0.0) or 0.0)
                if entry_px <= 0:
                    continue
                sim = SL.simulate_exit(side, entry_px, bars[i + 1:], stop_pct, n_bars)
                if sim is not None:
                    rets.append(sim[0] - _COST_12BPS)
        if not rets:
            skipped += 1
            continue
        per_episode.append(rets)
        obs.append(float(d.get("price_pct") or 0.0) / 100.0 - _COST_12BPS)
    if not per_episode:
        return None
    obs_mean = statistics.mean(obs)
    null_means = [statistics.mean(rng.choice(rets) for rets in per_episode)
                  for _ in range(draws)]
    ge = sum(1 for m in null_means if m >= obs_mean)
    return {
        "n_episodes": len(per_episode),
        "skipped": skipped,
        "draws": draws,
        "obs_mean_price12_pct": round(100 * obs_mean, 4),
        "null_mean_price12_pct": round(100 * statistics.mean(null_means), 4),
        "excess_pct": round(100 * (obs_mean - statistics.mean(null_means)), 4),
        "mc_p": round((1 + ge) / (draws + 1), 4),
    }


def grade_inverse(book: str, now_ms: int,
                  meta_filters: Optional[Dict[str, Any]] = None,
                  null_draws: int = 0, seed: int = 7) -> Dict[str, Any]:
    """Return the exact opposite-side forward grade for one ledger book."""
    original = SL.filter_by_meta(SL.load(book), meta_filters or {})
    inverted, pre_deduped = SL.dedup_episodes(inverse_records(original))
    fetch_fwd, fetch_funding, candle_cache = _make_fetchers(inverted, now_ms)
    grade = SL.grade_records(inverted, fetch_fwd, now_ms=now_ms,
                             fetch_funding=fetch_funding, dedup=False)
    grade["deduped"] = pre_deduped
    out = {"book": book, "records": len(original), "inverse_grade": grade}
    if null_draws > 0 and grade.get("n"):
        out["null"] = matched_null(grade.get("detail") or [], candle_cache,
                                   null_draws, random.Random(seed))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", action="append", default=[], help="ledger book to invert (repeatable)")
    parser.add_argument("--json", action="store_true", help="include per-episode details")
    parser.add_argument("--meta", action="append", default=[], metavar="KEY=VALUE",
                        help="filter ledger records by meta before inverting, e.g. --meta breaking=true")
    parser.add_argument("--null", type=int, default=2000, metavar="DRAWS",
                        help="matched same-coin random-time null draws (0 disables; default 2000)")
    parser.add_argument("--seed", type=int, default=7, help="null RNG seed")
    args = parser.parse_args()
    meta_filters = SL.parse_meta_filters(args.meta)
    now_ms = int(time.time() * 1000)
    books = args.book or DEFAULT_BOOKS
    report = [grade_inverse(book, now_ms, meta_filters=meta_filters,
                            null_draws=args.null, seed=args.seed) for book in books]
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0
    flt = f" [meta filter: {meta_filters}]" if meta_filters else ""
    print(f"# opposite-side shadow-ledger audit (read-only){flt}")
    print(f"{'book':<24} {'raw':>4} {'n':>4} {'@12bps':>10} {'halves':>20} {'excess':>8} {'mc_p':>6}   verdict")
    print("-" * 110)
    for row in report:
        grade = row["inverse_grade"]
        m12 = grade.get("slip12", {}).get("mean_pct")
        halves = grade.get("oos_12bps", {})
        h1, h2 = halves.get("first"), halves.get("second")
        verdict = grade.get("verdict", {})
        null = row.get("null") or {}
        m12_s = f"{m12:+.3f}%" if m12 is not None else "-"
        halves_s = f"{h1:+.3f}/{h2:+.3f}" if h1 is not None and h2 is not None else "-"
        excess_pct = null.get("excess_pct")
        mc_p = null.get("mc_p")
        excess_s = f"{excess_pct:+.2f}%" if excess_pct is not None else "-"
        mc_p_s = f"{mc_p:.4f}" if mc_p is not None else "-"
        print(f"{row['book']:<24} {row['records']:>4} {grade.get('n', 0):>4} "
              f"{m12_s:>10} "
              f"{halves_s:>20} "
              f"{excess_s:>8} "
              f"{mc_p_s:>6}   "
              f"{verdict.get('label', '?')}: {verdict.get('why', '')}")
    if any(row.get("null") for row in report):
        print("\n# excess = inverse mean MINUS same-coin random-time mean (price-only @12bps both sides).")
        print("# A verdict without positive excess at mc_p<0.05 is tape beta, not an inverse edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
