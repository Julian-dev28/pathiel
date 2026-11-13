"""Does the pool's OWN pipeline (records to clusters to rows to counts, scoreboard.build)
hold up on synthetic data? python -m services.pool.evals.pool_eval [--seeds N]

Periodic lane: offline, seeded, no network, no LLM. This tests the pipeline
in services/pool/grade.py and scoreboard.py: member filtering, clustering,
slicing and the published counts, all through `scoreboard.build` so every
produced doc is also checked against `pool.validate_scoreboard`.

It does NOT re-gate `contracts.pool.cluster_ids` (same coin and side,
entered within CLUSTER_SPAN_MS of the cluster's first entry: one bet) --
that rule is already proven by `python -m services.pool.evals.cluster_eval`
(shared-path noise, the no-quiet-hour chain, power). This eval calls the
same contract function through the pool's own wiring and checks the wiring
does not lose, duplicate or leak a trade along the way.

Gates (all must pass; exit 1 otherwise):

  copied_calls   12 members copying the same 3 calls, at least 3 days apart
                 on one coin and side (so 3 clusters): the pool "all" row is
                 EDGE or LEAK in 0 of `--seeds` runs. n is 3, always under
                 MIN_N_EDGE, so this also proves cluster_records does not
                 inflate n to 36 or 12.
  planted_edge   a planted edge of 0.4 R (sd 0.75 R) over 40 independent
                 clusters, each more than 24 hours after the one before on
                 its own coin and side, spread over the four pool coins and
                 both sides: EDGE in at least 80% of `--seeds` runs.
  pure_noise     the same shape, mean 0: EDGE or LEAK in at most 5%.
  delay          across `--seeds` random streams of trades straddling the
                 cutoff, `counts.closed_trades` always equals exactly the
                 trades that closed before the cutoff: 0 that closed at or
                 after it ever reach a count.
  leave          across `--seeds` streams with a second, left-out address
                 holding its own trades in `records_by_address` but absent
                 from `members`: its trades never reach a row or a count.
  every_doc_valid    every scoreboard this eval built (every gate above,
                 plus no_quiet_hour below) passes `pool.validate_scoreboard`.
  no_quiet_hour  12 members entering one coin and side every 5 to 55 minutes
                 for 30 days, through the whole pipeline:
                 counts.independent_trades between 28 and 31 in every one of
                 a smaller run of seeds (this gate is the expensive one: each
                 build grades roughly 17,000 synthetic trades).

Reported, not gated: the wall time of the no_quiet_hour runs, since it is
the one gate heavy enough to matter.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import contracts.pool as pool
from services.pool import scoreboard as SB
from services.review import stats as S

SEEDS = 200
NO_QUIET_HOUR_SEEDS = 10     # not tied to --seeds: each run grades ~17,000 trades

COPIED_MAX = 0.0
PLANTED_MIN = 0.80
NOISE_MAX = 0.05
PLANTED_MEAN_R = 0.4
PLANTED_SD_R = 0.75

AS_OF = pool.ms_from_iso("2026-12-01T00:00:00Z")


def _member(address: str, count_from_ms: int, kind: str = "member",
           signer_role: Optional[str] = "master") -> Dict[str, Any]:
    return {"address": address, "kind": kind, "signer_role": signer_role,
           "count_from": pool.iso_from_ms(count_from_ms), "count_from_ms": count_from_ms}


def _rec(coin: str, side: str, entry_ms: int, exit_ms: Optional[int], r_mult: Optional[float]) -> Dict[str, Any]:
    return {"coin": coin, "side": side, "entry_ms": entry_ms, "exit_ms": exit_ms, "r_mult": r_mult,
           "net_usd": r_mult if r_mult is not None else 0.0, "desk": pool.POOL_DESK_OF[coin],
           "weekend": False, "hour_utc": 12, "planned_r": 2.0, "range_pos": 0.5, "trend_20d_pct": 0.0,
           "luck_pct": None}


_VALID_DOCS: List[bool] = []     # every scoreboard any gate below built, for every_doc_valid


def _build(members: Sequence[Dict[str, Any]], records_by_address: Dict[str, List[Dict[str, Any]]],
          as_of_ms: int = AS_OF, left: int = 0) -> Dict[str, Any]:
    doc = SB.build(list(members), records_by_address, as_of_ms, left=left)
    _VALID_DOCS.append(pool.validate_scoreboard(doc) == [])
    return doc


# ── copied_calls ─────────────────────────────────────────────────────────

def _copied_calls_doc(rng: random.Random) -> Dict[str, Any]:
    t0 = AS_OF - 60 * pool.DAY_MS
    coin, side = "xyz:BRENTOIL", "long"
    members, records_by_address = [], {}
    for i in range(12):
        addr = f"0x{i:040x}"
        members.append(_member(addr, t0 - pool.DAY_MS))
        recs = []
        for call in range(3):
            entry = t0 + call * 3 * pool.DAY_MS + rng.randrange(0, 3 * 3_600_000)
            recs.append(_rec(coin, side, entry, entry + 3_600_000, rng.gauss(0.0, 0.5)))
        records_by_address[addr] = recs
    return _build(members, records_by_address)


def copied_calls(seeds: int) -> Tuple[int, int]:
    bad = 0
    for seed in range(seeds):
        rng = random.Random(S.sub_seed(S.SEED, f"pool-eval:copied:{seed}"))
        doc = _copied_calls_doc(rng)
        if doc["counts"]["independent_trades"] != 3:
            bad += 1
            continue
        if doc["pool"][0]["label"] in ("EDGE", "LEAK"):
            bad += 1
    return bad, seeds


# ── planted_edge / pure_noise ────────────────────────────────────────────

def _spread_entries(n: int, start_ms: int, gap_ms: int) -> List[Tuple[str, str, int]]:
    combos = [(c, s) for c in pool.POOL_COINS for s in ("long", "short")]
    per_combo = -(-n // len(combos))
    out = []
    for coin, side in combos:
        for k in range(per_combo):
            if len(out) >= n:
                break
            out.append((coin, side, start_ms + k * gap_ms))
    return out[:n]


def _independent_doc(rng: random.Random, mean_r: float, sd_r: float, n: int = 40) -> Dict[str, Any]:
    t0 = AS_OF - 90 * pool.DAY_MS
    members, records_by_address = [], {}
    for i, (coin, side, entry) in enumerate(_spread_entries(n, t0, 25 * pool.HOUR_MS)):
        addr = f"0x{i:040x}"
        members.append(_member(addr, t0 - pool.DAY_MS))
        records_by_address[addr] = [_rec(coin, side, entry, entry + 3_600_000, rng.gauss(mean_r, sd_r))]
    return _build(members, records_by_address)


def planted_edge(seeds: int, n: int = 40) -> Tuple[int, int]:
    hits = 0
    for seed in range(seeds):
        rng = random.Random(S.sub_seed(S.SEED, f"pool-eval:planted:{seed}:{n}"))
        doc = _independent_doc(rng, PLANTED_MEAN_R, PLANTED_SD_R, n=n)
        hits += doc["pool"][0]["label"] == "EDGE"
    return hits, seeds


def pure_noise(seeds: int, n: int = 40) -> Tuple[int, int]:
    bad = 0
    for seed in range(seeds):
        rng = random.Random(S.sub_seed(S.SEED, f"pool-eval:noise:{seed}:{n}"))
        doc = _independent_doc(rng, 0.0, PLANTED_SD_R, n=n)
        bad += doc["pool"][0]["label"] in ("EDGE", "LEAK")
    return bad, seeds


# ── delay ────────────────────────────────────────────────────────────────

def delay(seeds: int) -> Tuple[int, int]:
    bad = 0
    cutoff = pool.cutoff_ms(AS_OF)
    t0 = AS_OF - 60 * pool.DAY_MS
    addr = "0x" + "11" * 20
    member = _member(addr, t0 - pool.DAY_MS)
    for seed in range(seeds):
        rng = random.Random(S.sub_seed(S.SEED, f"pool-eval:delay:{seed}"))
        recs, before = [], 0
        for i in range(10):
            entry = t0 + i * pool.HOUR_MS
            exit_ms = max(entry + 60_000, cutoff + rng.randrange(-5 * pool.DAY_MS, 5 * pool.DAY_MS))
            recs.append(_rec("xyz:BRENTOIL", "long", entry, exit_ms, rng.gauss(0.0, 1.0)))
            before += exit_ms < cutoff
        doc = _build([member], {addr: recs})
        if doc["counts"]["closed_trades"] != before or doc["counts"]["graded_trades"] != before:
            bad += 1
    return bad, seeds


# ── leave ────────────────────────────────────────────────────────────────

def leave(seeds: int) -> Tuple[int, int]:
    bad = 0
    t0 = AS_OF - 60 * pool.DAY_MS
    active_addr, left_addr = "0x" + "aa" * 20, "0x" + "bb" * 20
    active_member = _member(active_addr, t0 - pool.DAY_MS)
    for seed in range(seeds):
        rng = random.Random(S.sub_seed(S.SEED, f"pool-eval:leave:{seed}"))
        n_active, n_left = rng.randrange(1, 5), rng.randrange(1, 5)
        active_recs = [_rec("xyz:BRENTOIL", "long", t0 + i * pool.HOUR_MS, t0 + i * pool.HOUR_MS + 3_600_000,
                            rng.gauss(0.0, 1.0)) for i in range(n_active)]
        left_recs = [_rec("xyz:SILVER", "short", t0 + i * pool.HOUR_MS, t0 + i * pool.HOUR_MS + 3_600_000,
                          rng.gauss(0.0, 1.0)) for i in range(n_left)]
        doc = _build([active_member], {active_addr: active_recs, left_addr: left_recs}, left=1)
        if any(r["address"] == left_addr for r in doc["members"]):
            bad += 1
        elif doc["counts"]["closed_trades"] != n_active:
            bad += 1
    return bad, seeds


# ── no_quiet_hour ────────────────────────────────────────────────────────

def no_quiet_hour(seeds: int, n_members: int = 12, days: int = 30) -> Tuple[int, int, float]:
    """`n_members` entering one coin and side every 5 to 55 minutes for
    `days` days. The expected cluster count scales with `days` alone (one
    cluster per roughly 24h15m, never with `n_members`): `[days - 2, days +
    1]`, which is exactly `[28, 31]` at the default `days=30` the gate
    specifies. Smaller `n_members` / `days` make this cheap for a gate-lane
    smoke test without changing the shape being tested."""
    t0 = AS_OF - 60 * pool.DAY_MS
    coin, side = "xyz:BRENTOIL", "long"
    lo, hi = max(1, days - 2), days + 1
    bad = 0
    started = time.time()
    for seed in range(seeds):
        rng = random.Random(S.sub_seed(S.SEED, f"pool-eval:quiet:{seed}:{n_members}:{days}"))
        members, records_by_address = [], {}
        for i in range(n_members):
            addr = f"0x{i:040x}"
            members.append(_member(addr, t0 - pool.DAY_MS))
            t = t0 + rng.randrange(0, 15 * 60_000)
            recs = []
            while t < t0 + days * pool.DAY_MS:
                recs.append(_rec(coin, side, t, t + 3_600_000, rng.gauss(0.0, 0.5)))
                t += rng.randrange(5 * 60_000, 55 * 60_000 + 1)
            records_by_address[addr] = recs
        doc = _build(members, records_by_address)
        n = doc["counts"]["independent_trades"]
        if n == 1 or not (lo <= n <= hi):
            bad += 1
    return bad, seeds, time.time() - started


def _row(check: str, got: Any, want: str, ok: bool) -> Dict[str, Any]:
    return {"check": check, "got": got, "want": want, "ok": ok}


def run(seeds: int = SEEDS, quiet_seeds: int = NO_QUIET_HOUR_SEEDS, quiet_members: int = 12,
       quiet_days: int = 30, independent_n: int = 40) -> List[Dict[str, Any]]:
    """The scorecard: one {check, got, want, ok} row per gate. `quiet_members`
    / `quiet_days` shrink the expensive no_quiet_hour gate, and
    `independent_n` the planted_edge / pure_noise cluster count, for a cheap
    smoke run; the periodic defaults (12, 30, 40) are exactly the gates the
    design doc specifies ([28, 31], 80%, 5%)."""
    rows: List[Dict[str, Any]] = []
    bad, n = copied_calls(seeds)
    rate = bad / n
    rows.append(_row("copied_calls", round(rate, 4), f"<= {COPIED_MAX}", rate <= COPIED_MAX))
    hits, n = planted_edge(seeds, n=independent_n)
    rate = hits / n
    rows.append(_row("planted_edge", round(rate, 4), f">= {PLANTED_MIN}", rate >= PLANTED_MIN))
    bad, n = pure_noise(seeds, n=independent_n)
    rate = bad / n
    rows.append(_row("pure_noise", round(rate, 4), f"<= {NOISE_MAX}", rate <= NOISE_MAX))
    bad, n = delay(seeds)
    rows.append(_row("delay", bad, "0 streams with a trade past the cutoff counted", bad == 0))
    bad, n = leave(seeds)
    rows.append(_row("leave", bad, "0 streams where a left address reached a count", bad == 0))
    bad, n, elapsed = no_quiet_hour(quiet_seeds, n_members=quiet_members, days=quiet_days)
    lo, hi = max(1, quiet_days - 2), quiet_days + 1
    rows.append(_row("no_quiet_hour", bad, f"0 of {quiet_seeds} outside [{lo}, {hi}] or == 1", bad == 0))
    rows.append(_row("no_quiet_hour.seconds", round(elapsed, 1), "reported, not gated", None))
    rows.append(_row("every_doc_valid", sum(1 for ok in _VALID_DOCS if not ok), "0 invalid scoreboards",
                     all(_VALID_DOCS)))
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m services.pool.evals.pool_eval",
                                 description="Does the pool's own pipeline hold up on synthetic data?")
    ap.add_argument("--seeds", type=int, default=SEEDS)
    ap.add_argument("--quiet-seeds", type=int, default=NO_QUIET_HOUR_SEEDS)
    ap.add_argument("--quiet-members", type=int, default=12)
    ap.add_argument("--quiet-days", type=int, default=30)
    ap.add_argument("--independent-n", type=int, default=40)
    args = ap.parse_args(argv)
    _VALID_DOCS.clear()
    rows = run(seeds=args.seeds, quiet_seeds=args.quiet_seeds, quiet_members=args.quiet_members,
              quiet_days=args.quiet_days, independent_n=args.independent_n)
    width = max(len(r["check"]) for r in rows)
    for r in rows:
        mark = "info" if r["ok"] is None else ("ok" if r["ok"] else "FAIL")
        print(f"{r['check']:<{width}}  got={r['got']!s:<8}  want {r['want']:<48}  {mark}")
    gated = [r for r in rows if r["ok"] is not None]
    passed = sum(1 for r in gated if r["ok"])
    print(f"POOL-EVAL seeds={args.seeds} quiet_seeds={args.quiet_seeds} passed={passed}/{len(gated)}")
    return 0 if passed == len(gated) else 1


if __name__ == "__main__":
    sys.exit(main())
