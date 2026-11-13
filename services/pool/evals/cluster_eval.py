"""Is the pool's rule for an independent trade sound?

    python -m services.pool.evals.cluster_eval [--seeds N]

Periodic lane: offline, seeded, about 40 seconds at the default 200 seeds. No
network, no LLM. It tests `contracts.pool.cluster_ids` (same coin and side,
entered within CLUSTER_SPAN_MS of the cluster's first entry: one bet) on the
question the scoreboard depends on: does a pool row say EDGE or LEAK when
there is nothing there?

The world: one coin, one side, a price that steps up or down one unit every
five minutes. Members look every 15 minutes and enter with a bracket (stop
one unit of risk away, target two), so every trade is worth -1 R or +2 R and,
with an even coin, exactly 0 R on average. Members who are in at the same
time ride the same path: that shared path is what makes their trades one bet.

Gates (all must pass; exit 1 otherwise):

  same_call     12 members entering within 10 minutes of each of 3 calls are
                3 clusters, in every seed
  chain_bound   a stream with no quiet hour (the 2026-11-12 review finding):
                no cluster spans more than CLUSTER_SPAN_MS and 30 days give at
                least 28 clusters, in every seed. Chained on the previous
                entry with a 60 minute gap, the same stream is 1 cluster.
  reference     the eval's own anchored rule equals contracts.pool.cluster_ids
  null          even coin, 12 members, 30 days, median hold about 4 hours:
                EDGE or LEAK in at most 10% of seeds (measured 5.5%)
  teeth         the same streams with every trade counted as its own bet:
                EDGE or LEAK in at least 25% of seeds (measured 49.5%), so the
                null gate is measuring something
  null_long     even coin, 60 days, median hold about 12 hours: at most 20%
                (measured 11%: positions held across clusters still leak)
  power         a coin that lands up 52% of the time (about 0.5 R a trade):
                EDGE in at least 80% of seeds (measured 92.5%)

Reported, not gated: the 60 minute window this design first used, on the
null streams (measured 33%).

Measured values are the 200 seed run of 2026-11-12.

Where it breaks: the walk has no gaps, no trend and one coin, and every
member trades the same bracket. Real members differ, which lowers the shared
part; real markets trend, which raises it. The gates say the rule is not
fooled by the plainest case of shared exposure. They do not say a pool row is
proof.
"""
from __future__ import annotations

import argparse
import random
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import contracts.pool as pool
from services.review import analysis as A
from services.review import stats as S

STEP_MS = 5 * 60_000
STEPS_PER_DAY = 288
TICK_STEPS = 3                   # members look every 15 minutes
COIN, SIDE = "xyz:BRENTOIL", "long"
TAIL_DAYS = 6                    # the walk runs on so every entered trade can close

NULL_MAX = 0.10
TEETH_MIN = 0.25
NULL_LONG_MAX = 0.20
POWER_MIN = 0.80
CHAIN_DAYS = 30
CHAIN_MIN_CLUSTERS = 28

Trade = Tuple[int, int, float]   # entry_ms, exit_ms, R


def walk(rng: random.Random, days: int, p_up: float = 0.5) -> List[int]:
    """Unit steps, so a bracket is hit exactly and E[R] is exactly 0 at p_up 0.5."""
    px = [0]
    for _ in range((days + TAIL_DAYS) * STEPS_PER_DAY):
        px.append(px[-1] + (1 if rng.random() < p_up else -1))
    return px


def bracket_trades(rng: random.Random, px: Sequence[int], members: int, days: int,
                   p_enter: float, stop_steps: int) -> List[Trade]:
    """Each member, when flat, enters at a tick with probability `p_enter`,
    stop `stop_steps` below, target twice that above. Sorted by entry."""
    out: List[Trade] = []
    last_entry = days * STEPS_PER_DAY
    for _ in range(members):
        i = 0
        while i < last_entry:
            if i % TICK_STEPS or rng.random() >= p_enter:
                i += 1
                continue
            j, r = i + 1, None
            while j < len(px):
                move = px[j] - px[i]
                if move <= -stop_steps:
                    r = -1.0
                    break
                if move >= 2 * stop_steps:
                    r = 2.0
                    break
                j += 1
            if r is None:        # the walk ended first: not a closed trade
                break
            out.append((i * STEP_MS, j * STEP_MS, r))
            i = j + 1
    return sorted(out)


def anchored(entries: Sequence[int], span_ms: int) -> List[int]:
    """The contract's rule for one coin and side, with the span as a parameter."""
    ids: List[int] = []
    current, anchor = -1, None
    for e in sorted(entries):
        if anchor is None or e - anchor > span_ms:
            current, anchor = current + 1, e
        ids.append(current)
    return ids


def chained(entries: Sequence[int], gap_ms: int) -> List[int]:
    """The rule this design first used: within `gap_ms` of the PREVIOUS entry."""
    ids: List[int] = []
    current, prev = -1, None
    for e in sorted(entries):
        if prev is None or e - prev > gap_ms:
            current += 1
        ids.append(current)
        prev = e
    return ids


def contract_ids(trades: Sequence[Trade]) -> List[int]:
    return pool.cluster_ids([(COIN, SIDE, e) for e, _, _ in trades])


def label(trades: Sequence[Trade], ids: Sequence[int], seed: int, n_boot: int, n_perm: int) -> str:
    """The 'all' row's label over one record per cluster (the mean R of its
    trades), exactly how the scoreboard build grades the pool."""
    by: Dict[int, List[float]] = {}
    for (_, _, r), cid in zip(trades, ids):
        by.setdefault(cid, []).append(r)
    records = [{"r_mult": sum(v) / len(v), "net_usd": sum(v) / len(v)} for v in by.values()]
    return A.slice_row(records, "all", "all", seed, n_boot, n_perm)["label"]


def verdict_rates(rules: Dict[str, Callable[[Sequence[Trade]], List[int]]], want: Sequence[str], *,
                  seeds: int, days: int, members: int, p_enter: float, stop_steps: int,
                  p_up: float, n_boot: int, n_perm: int, tag: str) -> Dict[str, float]:
    hits = {name: 0 for name in rules}
    for seed in range(seeds):
        rng = random.Random(S.sub_seed(S.SEED, f"cluster-eval:{tag}:{seed}"))
        trades = bracket_trades(rng, walk(rng, days, p_up), members, days, p_enter, stop_steps)
        for name, rule in rules.items():
            got = label(trades, rule(trades), S.sub_seed(S.SEED, f"cluster-eval:{tag}:{seed}:label"),
                        n_boot, n_perm)
            hits[name] += got in want
    return {name: hits[name] / seeds for name in rules}


def same_call(seeds: int) -> int:
    """Seeds in which 12 members on 3 calls are not exactly 3 clusters."""
    bad = 0
    for seed in range(seeds):
        rng = random.Random(S.sub_seed(S.SEED, f"cluster-eval:call:{seed}"))
        rows = [(COIN, SIDE, call * 3 * pool.DAY_MS + rng.randrange(0, 10 * 60_000))
                for call in range(3) for _ in range(12)]
        bad += len(set(pool.cluster_ids(rows))) != 3
    return bad


def chain(seeds: int) -> Dict[str, int]:
    """A stream with no quiet hour: 12 members, entries 5 to 55 minutes apart."""
    out = {"span_breaks": 0, "min_clusters": 10 ** 9, "max_chained_60m": 0, "reference_breaks": 0}
    for seed in range(seeds):
        rng = random.Random(S.sub_seed(S.SEED, f"cluster-eval:chain:{seed}"))
        entries: List[int] = []
        for _ in range(12):
            t = rng.randrange(0, 15 * 60_000)
            while t < CHAIN_DAYS * pool.DAY_MS:
                entries.append(t)
                t += rng.randrange(5 * 60_000, 55 * 60_000 + 1)
        entries.sort()
        ids = pool.cluster_ids([(COIN, SIDE, e) for e in entries])
        first: Dict[int, int] = {}
        for e, cid in zip(entries, ids):
            first.setdefault(cid, e)
            out["span_breaks"] += e - first[cid] > pool.CLUSTER_SPAN_MS
        out["min_clusters"] = min(out["min_clusters"], len(first))
        out["max_chained_60m"] = max(out["max_chained_60m"], len(set(chained(entries, pool.HOUR_MS))))
        out["reference_breaks"] += ids != anchored(entries, pool.CLUSTER_SPAN_MS)
    return out


def _row(check: str, got: Any, want: str, ok: Optional[bool]) -> Dict[str, Any]:
    return {"check": check, "got": got, "want": want, "ok": ok}


def run(seeds: int = 200, n_boot: int = 1000, n_perm: int = 1000) -> List[Dict[str, Any]]:
    """The scorecard: one {check, got, want, ok} row per gate; ok None is a
    reported number with no gate."""
    verdict = ("EDGE", "LEAK")
    rows: List[Dict[str, Any]] = []
    bad_calls = same_call(seeds)
    rows.append(_row("same_call", bad_calls, "0 seeds off 3 clusters", bad_calls == 0))
    ch = chain(seeds)
    rows.append(_row("chain_bound.span", ch["span_breaks"], "0 entries past the span",
                     ch["span_breaks"] == 0))
    rows.append(_row("chain_bound.clusters", ch["min_clusters"], f">= {CHAIN_MIN_CLUSTERS} in {CHAIN_DAYS} days",
                     ch["min_clusters"] >= CHAIN_MIN_CLUSTERS))
    rows.append(_row("chain_bound.chained_60m", ch["max_chained_60m"], "1 (the rule this replaces)",
                     ch["max_chained_60m"] == 1))
    rows.append(_row("reference", ch["reference_breaks"], "0 seeds where the eval's rule differs",
                     ch["reference_breaks"] == 0))

    common = {"seeds": seeds, "n_boot": n_boot, "n_perm": n_perm}
    null = verdict_rates(
        {"contract": contract_ids,
         "each_trade": lambda t: list(range(len(t))),
         "window_60m": lambda t: anchored([e for e, _, _ in t], pool.HOUR_MS)},
        verdict, days=30, members=12, p_enter=0.05, stop_steps=6, p_up=0.5, tag="null", **common)
    rows.append(_row("null", round(null["contract"], 4), f"<= {NULL_MAX}", null["contract"] <= NULL_MAX))
    rows.append(_row("teeth", round(null["each_trade"], 4), f">= {TEETH_MIN}", null["each_trade"] >= TEETH_MIN))
    rows.append(_row("window_60m (reported)", round(null["window_60m"], 4), "no gate", None))
    long_hold = verdict_rates({"contract": contract_ids}, verdict, days=60, members=12, p_enter=0.02,
                              stop_steps=10, p_up=0.5, tag="null_long", **common)
    rows.append(_row("null_long", round(long_hold["contract"], 4), f"<= {NULL_LONG_MAX}",
                     long_hold["contract"] <= NULL_LONG_MAX))
    power = verdict_rates({"contract": contract_ids}, ("EDGE",), days=30, members=12, p_enter=0.05,
                          stop_steps=6, p_up=0.52, tag="power", **common)
    rows.append(_row("power", round(power["contract"], 4), f">= {POWER_MIN}", power["contract"] >= POWER_MIN))
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m services.pool.evals.cluster_eval",
                                 description="Is the pool's rule for an independent trade sound?")
    ap.add_argument("--seeds", type=int, default=200)
    args = ap.parse_args(argv)
    rows = run(seeds=args.seeds)
    width = max(len(r["check"]) for r in rows)
    for r in rows:
        mark = "info" if r["ok"] is None else ("ok" if r["ok"] else "FAIL")
        print(f"{r['check']:<{width}}  got={r['got']!s:<8}  want {r['want']:<38}  {mark}")
    gated = [r for r in rows if r["ok"] is not None]
    passed = sum(1 for r in gated if r["ok"])
    print(f"CLUSTER-EVAL span_hours={pool.CLUSTER_SPAN_MS // pool.HOUR_MS} seeds={args.seeds} "
          f"passed={passed}/{len(gated)}")
    return 0 if passed == len(gated) else 1


if __name__ == "__main__":
    sys.exit(main())
