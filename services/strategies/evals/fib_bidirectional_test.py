"""Bidirectional fib sleeve test, 2026-11-16. Research only. Never touches
services/fib_sleeve, any live config, or any pathiel MCP tool.

Operator's question (2026-11-16, Brent fell 94.11 -> 89.82 near-straight
line; the live sleeve's with-swing short at the 38.2% bounce never filled,
5 placements, 0 fills): should the sleeve also rest a counter-trend order
-- a LONG below a down-swing low, a SHORT above an up-swing high -- so a
trend that never retraces still has a resting order that can fill, first
fill cancels the other (OCO)?

PRE-REGISTRATION (written before any run; do not edit after looking at
results -- a correction goes in a dated note, not a silent rewrite).

Hypotheses, four, economically motivated, under 100 variants total:

  H1  Counter-trend entry at a fib EXTENSION beyond the swing's own
      extreme (-23.6% / -38.2% / -61.8% of the swing's size past the low
      on a down-swing, past the high on an up-swing): a resting limit that
      fills if the move keeps going past where the with-swing order would
      have rested. Stop 1 ATR further, or beyond the next deeper
      extension; target a modest 23.6% or 38.2% retracement back (not a
      full round trip). Mirrors `services.fib_sleeve.rule`'s own math
      (`fib_retrace_test.fib_price` / `target_price` / `stop_price`), just
      on the OTHER side of the extreme.
  H2  Overextension filter: take the counter leg only when the swing
      itself is stretched, size >= k x ATR14, k in {3, 4, 5}. Mean
      reversion should live in stretched moves, not every swing.
  H3  Positioning filter: Hyperliquid funding z-score (trailing 24h mean
      vs trailing 30d distribution, `backtest.funding_signal`, the same
      definition the repo's funding-extreme research already uses) extreme
      AGAINST the move at the swing: for a down-swing, the crowded
      (funding-paying) side is LONG despite the decline (z >= threshold,
      the "crowded long" signature); for an up-swing, crowded SHORT
      despite the rally (z <= -threshold). PRIOR EVIDENCE AGAINST: this
      repo's own `FUNDING_EXTREME_TEST_2026-11-03.md` already ran the
      closest analogue on xyz:BRENTOIL itself and refuted it -- z >= 2.0
      (crowded long) fires 3 independent events in 244 days, z <= -2.0
      fires 11; "the crowded-long signal this rule needs essentially
      never happens on Brent." H3 is pre-registered expecting NOISE for
      exactly that reason; it is tested anyway because the operator asked
      the direct question and a documented NOISE beats an unasked one.
  H4  The full bidirectional sleeve: the LIVE with-swing rule (sleeve
      defaults, `services.fib_sleeve.config.SleeveConfig`: 38.2% retrace,
      1-ATR stop, -27.2% extension target, be50 ratchet) plus the best
      principled counter leg from H1/H2/H3, both resting at once, one
      position at a time, first fill cancels the other (OCO). Compared to
      the live with-swing rule ALONE on the same windows.

Grid (pre-registered, small, no fishing): 3 extension levels x 2 target
retracements x 4 overextension settings (none, k3, k4, k5) = 24 H1/H2
variants, stop fixed at "atr1" and ratchet at "be50" (the live sleeve's own
choices -- "next"-stop and ratchet="off" are checked only on the single
reference variant as a sensitivity note, not a second grid). Plus 4 H3
variants (funding threshold 1.5 / 2.0 crossed with k_filter none / 4) and 3
H4 OCO variants (the reference counter leg, the reference + k4, the
reference + funding 2.0). 24 + 2 + 4 + 3 = 33 variants x 2 risk sizes = 66
judged rows. The reference counter leg, named here before any result:
`ext382_atr1_T382_be50` (38.2% extension, 1 ATR stop, 38.2% retrace
target, break-even ratchet, no filter) -- the middle of every axis, same
convention as `fib_retrace_test.REFERENCE`.

Datasets, fill model, costs, sizing: identical to
`services.strategies.evals.fib_retrace_test` -- same candle/funding
loaders, same measured xyz fee (`intraday_5m_test.measured_fees`), same
realistic fill (a limit fills on a trade-through by one tick, maker; a
gapped resting order fills at the open; a stop is a taker fill with 3 bps
slippage), same `desk_tick_sim.risk_capped_notional` sizing at 6% and 1.2%
risk, same weekend flat, same live-long-book "beside" comparison plus an
"alone" reading -- and alone is the PRIMARY reading here, because the fib
sleeve runs on its own sub-account and never actually competes with the
oil desk for a slot (unlike the fib_retrace_test families, which inherited
the "beside live" framing from the desk's own long book for continuity
with that doc's bar; this doc reports both, verdict methodology matched to
precedent, headline prose keyed to alone).

Validation, pre-registered:

  - Fixed holdout: the last ~30% of each 208-day set, by calendar time,
    never used to pick a variant -- only checked after.
  - Walk-forward: both halves of the primary and of each 208-day set.
  - Random-level baseline: the SAME filters and the SAME stop/target
    RULES, entry at a uniformly random extension ratio in [0.15, 0.70]
    (comfortably covering the tested 0.236-0.618 band) instead of the
    fixed fib ratio, 200 draws, seeded. The real variant must beat the
    draws' distribution; percentile reported.
  - Multiple-testing honesty: 66 judged rows reported plainly. A
    max-statistic "reality check" over the 24-variant H1/H2 grid: 50 draws
    of 24 independently-randomized-entry variants each, the best of each
    draw's 24 forms a null distribution for "the best of 24 by chance";
    the real grid's own best is checked against that distribution, not
    against the single-variant baseline (a stricter bar).
  - Cost stress: taker fee, maker fee and slippage doubled; the chosen
    variant must survive.
  - Cross-asset: must not lose on WTI.
  - Regime split: Kaufman efficiency ratio per UTC day on 1h closes
    (|net change| / sum of |hourly changes|, 1.0 = pure trend, near 0 =
    chop); trades bucketed by the entry day's ratio, >= 0.5 trending.
  - Today's tape (2026-11-16) replayed under the chosen variant.

Adoption bar (pre-registered, matches `fib_retrace_test`'s own for
continuity): `short_ladder_test.judge` (beats live on every dataset with a
live book, drawdown no worse, both halves of the primary, every grid
neighbour positive -- "plateau" -- survives dropping the best day) with
10+ trades on the primary PLUS this test's own leg (net above zero ALONE
on every long-history dataset), PLUS above the random baseline's 95th
percentile, PLUS survives the cost stress. Under 10 trades: LEANING or
NOISE, never PASS. "Nothing adopted" is a fine answer and is, honestly,
the odds-on outcome given H3's prior and the sparse event counts a single
swing anchor on two instruments can produce.

    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.fib_bidirectional_test \\
        --cache-dir <dir> --out-dir <dir>

    # a fast smoke run: brent_journal and brent_15m_50d only, 6 variants
    ... --quick

Exit 0 when a variant passes the bar, 1 when none does, 2 with no journal.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import random
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle
from services.fib_sleeve.config import SleeveConfig
from services.strategies.backtest import data as D
from services.strategies.backtest.funding_signal import funding_z_at, funding_zscore_series
from services.strategies.evals import fib_retrace_test as F
from services.strategies.evals import intraday_5m_test as I5
from services.strategies.evals import short_ladder_test as SL
from services.strategies.evals.desk_tick_backtest import journaled_direction
from services.strategies.evals.stop_cap_test import _iso, closed_only
from services.strategies.evals.trend_gate_test import base_params, side_only

EPS = F.EPS
HOUR_MS, FIFTEEN_MS, DAY_MS = F.HOUR_MS, F.FIFTEEN_MS, F.DAY_MS
LIVE = SleeveConfig()                       # the live sleeve's defaults: level .382, stop atr1, target .272, be50

# ── the grid (pre-registered) ───────────────────────────────────────────────

EXT_LEVELS = (0.236, 0.382, 0.618)          # counter entry: this far past the swing's own extreme
EXT_LADDER = EXT_LEVELS + (1.0,)            # the next deeper extension after 0.618 is a full extra swing-length
COUNTER_STOPS = ("atr1", "next")
COUNTER_TARGETS = (0.236, 0.382)            # retracement back toward the swing's origin
COUNTER_RATCHETS = ("off", "be50")
K_FILTERS: Tuple[Optional[float], ...] = (None, 3.0, 4.0, 5.0)   # H2: swing.size >= k x ATR
FUND_THRESHOLDS = (1.5, 2.0)                # H3: |funding z| at or beyond this, against the move
REFERENCE_EXT, REFERENCE_STOP, REFERENCE_TGT, REFERENCE_RATCHET = 0.382, "atr1", 0.382, "be50"
RANDOM_LO, RANDOM_HI = 0.15, 0.70           # the random-level baseline's extension band
RANDOM_DRAWS = 200
RANDOM_SEED = 42
REALITY_DRAWS = 50                          # the max-statistic reality check (over the 24-variant grid)
REALITY_SEED = 43
BOOT_DRAWS = 2000
BOOT_SEED = 7
ER_TREND_THRESHOLD = 0.5                    # Kaufman efficiency ratio: >= this is "trending"


# ── level math (pure): the counter leg's own, built from fib_retrace_test's ─

def next_ext_level(r: float) -> float:
    """The next deeper extension on the ladder 23.6 / 38.2 / 61.8 / 100%."""
    for x in EXT_LADDER:
        if x > r + EPS:
            return x
    return EXT_LADDER[-1]


def counter_stop_price(rule: str, swing: F.Swing, ext_r: float, atr: float) -> float:
    """"atr1": the counter entry (`fib_retrace_test.target_price`, an
    extension beyond the swing's extreme) pushed 1 ATR further in the
    swing's OWN direction (the adverse direction for the counter trade).
    "next": 0.1 ATR beyond the NEXT deeper extension. Mirrors
    `fib_retrace_test.stop_price`'s "beyond the next level" shape, just on
    the continuation side of the extreme instead of the retrace side."""
    if rule == "atr1":
        return F.target_price(swing, ext_r) + swing.side * atr
    if rule == "next":
        nxt = next_ext_level(ext_r)
        return F.target_price(swing, nxt) + swing.side * F.STOP_BUF_ATR * atr
    raise ValueError(f"unknown counter stop rule {rule!r}")


def continuation_series(t: Sequence[int], h: Sequence[float], l: Sequence[float],
                        swings: Sequence[Optional[F.Swing]]) -> List[Optional[float]]:
    """Per tick i: the furthest price WITH the swing beyond its extreme
    since the swing began (None when there is none) -- the mirror of
    `fib_retrace_test.retrace_series` (which tracks the deepest price
    AGAINST the swing). A down-swing's continuation is its lowest low
    since the low; an up-swing's its highest high since the high. Governs
    whether a counter-trend extension level has already been traded
    through before this tick ("level already spent"), the same way
    `retrace_series` governs the with-swing level. Closed data only."""
    out: List[Optional[float]] = []
    key, cur, ext_i = None, None, -1
    for i, s in enumerate(swings):
        if s is None:
            key, cur = None, None
            out.append(None)
            continue
        if s.key != key:
            key, ext_i, cur = s.key, F._extreme_index(t, h, l, s), None
            for j in range(ext_i + 1, i):
                v = h[j] if s.side > 0 else l[j]
                cur = v if cur is None else (max(cur, v) if s.side > 0 else min(cur, v))
        elif i - 1 > ext_i:
            v = h[i - 1] if s.side > 0 else l[i - 1]
            cur = v if cur is None else (max(cur, v) if s.side > 0 else min(cur, v))
        out.append(cur)
    return out


# ── a leg (pure) ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LegSpec:
    level: float     # with: the retracement ratio; counter: the extension ratio
    stop: str
    target: float
    ratchet: str


def with_levels(swing: F.Swing, spec: LegSpec, atr: float) -> Tuple[int, float, float, float]:
    """(trade side, entry, stop, target) for the WITH-swing leg -- exactly
    `fib_retrace_test`'s own math, called, not re-derived."""
    level = F.fib_price(swing.origin, swing.extreme, spec.level)
    stop = F.stop_price(spec.stop, swing, spec.level, atr)
    target = F.target_price(swing, spec.target)
    return swing.side, level, stop, target


def counter_levels(swing: F.Swing, spec: LegSpec, atr: float) -> Tuple[int, float, float, float]:
    """(trade side, entry, stop, target) for the COUNTER-trend leg: entry
    at an extension beyond the swing's extreme (`target_price`, the SAME
    formula `fib_retrace_test` uses for a with-swing continuation target,
    here used as an entry on the other side), the trade side opposite the
    swing's own, stop pushed further in the swing's own direction, target a
    retracement back toward the origin (`fib_price`, the SAME formula the
    with-swing leg uses for ITS entry, here used as the counter leg's
    target)."""
    level = F.target_price(swing, spec.level)
    stop = counter_stop_price(spec.stop, swing, spec.level, atr)
    target = F.fib_price(swing.origin, swing.extreme, spec.target)
    return -swing.side, level, stop, target


def _fill_candidate(ctx: F.Ctx, i: int, side: int, level: float, stop: float, target: float,
                    series: Sequence[Optional[float]], p: Any, min_r: float, armed: set, swing_key: Any
                    ) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Does a resting limit at `level` fill on bar i, for a trade of `side`?
    (candidate-or-None, permanently-dead-for-this-swing). Identical shape
    to the "limit" branch of `fib_retrace_test.walk` -- a trade-through by
    one tick fills at the level (maker), a gap fills at the open, a level
    the `series` says is already spent or a gap nobody armed is not a
    fill, and a gate refusal on a gapped bar kills the leg for this swing
    (the same bar's conditions can only get worse from a plain re-check).
    `side` carries both WITH (= swing.side) and COUNTER (= -swing.side)
    legs through one piece of fill logic; only the caller's choice of
    `level`/`stop`/`target`/`series` differs between them."""
    r = series[i]
    ext = ctx.l[i] if side > 0 else ctx.h[i]
    gapped = side * (ctx.o[i] - level) <= 0
    if (r is not None and side * (r - level) <= -p.tick_size + EPS) or (gapped and swing_key not in armed):
        return None, False
    px = ctx.o[i] if gapped else level
    why, plan_r, lev = F.entry_gate(px, stop, target, side, p, min_r)
    if why:
        return None, gapped
    armed.add(swing_key)
    if not gapped and side * (level - ext) < p.tick_size - EPS:
        return None, False
    return {"side": side, "fill": px, "ref": px, "stop": stop, "target": target, "fee_in": p.maker_fee,
            "lev": lev, "planned_r": plan_r, "level_px": level}, False


def manage_trade(ctx: F.Ctx, p: Any, side: int, j: int, fill: float, stop0: float, target: float,
                 ratchet_rule: Optional[Tuple[float, str]], swing: F.Swing, hi_i: int,
                 live: Optional[I5.Live] = None) -> Dict[str, Any]:
    """Manage one open trade from its fill bar `j` to its exit: exactly
    `fib_retrace_test.walk`'s own per-bar loop (the ratchet read off closed
    bars, funding on the hour, a market close when the live long enters or
    the weekend starts, then the stop -- a bar that opens beyond it fills
    at its open, can fire on the fill bar -- then the target, never on the
    fill bar), extracted so both the with-leg and the counter leg (alone
    or together, OCO) exit the same way the live rule's own backtest
    does."""
    tick, base, taker = p.tick_size, p.slippage, p.taker_fee
    o, h, l, c, t = ctx.o, ctx.h, ctx.l, ctx.c, ctx.t
    cur, moved, best = stop0, False, None
    fund, reason, x_exit, fee_out = 0.0, "open", hi_i, 0.0
    exit_ref = exit_px = None
    for x in range(j, hi_i + 1):
        if x > j:
            if ratchet_rule is not None and not moved:
                new = F.ratchet_stop(ratchet_rule, side, fill, target, best, swing)
                if new is not None and side * (new - cur) > EPS and side * (c[x - 1] - new) > tick:
                    cur, moved = new, True
            if t[x] % HOUR_MS == 0:
                fund += side * ctx.fund.get(t[x], 0.0) * o[x]
            why = ("long_entry" if live is not None and live.long_entry[x]
                   else "weekend" if ctx.weekend[x] else None)
            if why:
                exit_ref = o[x]
                exit_px = exit_ref * (1.0 - side * F.market_slip(ctx, x, base))
                reason, x_exit, fee_out = why, x, taker
                break
        near = l[x] if side > 0 else h[x]
        if side * (near - cur) <= 0:
            exit_ref = min(cur, o[x]) if side > 0 else max(cur, o[x])
            exit_px = exit_ref * (1.0 - side * base)
            reason, x_exit, fee_out = ("ratchet" if moved else "stop"), x, taker
            break
        far = h[x] if side > 0 else l[x]
        if x > j:
            if side * (far - target) >= tick - EPS:
                exit_ref = exit_px = target
                reason, x_exit, fee_out = "target", x, p.maker_fee
                break
            best = far if best is None else (max(best, far) if side > 0 else min(best, far))
    if exit_px is None:
        exit_ref = exit_px = c[hi_i]
    return {"x": x_exit, "reason": reason, "exit_ref": exit_ref, "exit_px": exit_px, "fee_out": fee_out,
            "stop_final": cur, "fund_unit": fund, "moved": moved}


# ── the walker ─────────────────────────────────────────────────────────────

FundGate = Callable[[int, int], bool]       # (t_ms, swing_side) -> eligible


def walk_sleeve(ctx: F.Ctx, p: Any, lo_i: int, hi_i: int, with_spec: Optional[LegSpec] = None,
                counter_spec: Optional[LegSpec] = None, min_r: float = 0.0,
                live: Optional[I5.Live] = None, k_filter: Optional[float] = None,
                fund_gate: Optional[FundGate] = None, cont: Optional[Sequence[Optional[float]]] = None,
                swing_name: str = "range24") -> Dict[str, Any]:
    """One position at a time over bars [lo_i, hi_i]. With only `with_spec`:
    the live with-swing rule alone (a parity check against
    `fib_retrace_test.walk` -- see test_fib_bidirectional.py). With only
    `counter_spec`: the counter-trend leg alone (H1/H2/H3). With both: the
    bidirectional OCO sleeve (H4) -- at each swing, whichever leg's level
    is touched first fills; the other is never placed (cancelled by
    construction, since an order that was never armed is never offered
    again once the swing is done). When both legs' levels are touched in
    the SAME bar (the bar's range spans both), the one whose level sits
    closer to that bar's OPEN is taken as having triggered first -- intrabar
    order is unknowable from OHLC, and this is the explicit, documented
    convention (see the module doc's "where this would break")."""
    sw = ctx.swings[swing_name]
    ret = ctx.ret[swing_name]
    cont = cont if cont is not None else continuation_series(ctx.t, ctx.h, ctx.l, sw)
    with_armed: set = set()
    with_dead: set = set()
    counter_armed: set = set()
    counter_dead: set = set()
    done: set = set()
    paths: List[Dict[str, Any]] = []
    i = lo_i
    while i <= hi_i:
        s = sw[i]
        if (s is None or s.key in done or ctx.weekend[i] or ctx.reopen[i]
                or (live is not None and live.long_open[i])):
            i += 1
            continue
        atr = ctx.atr[i]
        if not atr:
            i += 1
            continue
        cands: Dict[str, Dict[str, Any]] = {}
        if with_spec is not None and s.key not in with_dead:
            side, level, stop, target = with_levels(s, with_spec, atr)
            cand, dead = _fill_candidate(ctx, i, side, level, stop, target, ret, p, min_r, with_armed, s.key)
            if dead:
                with_dead.add(s.key)
            if cand is not None:
                cands["with"] = {**cand, "level_r": with_spec.level, "ratchet": with_spec.ratchet}
        eligible_counter = (counter_spec is not None and s.key not in counter_dead
                            and (k_filter is None or s.size >= k_filter * atr - EPS)
                            and (fund_gate is None or fund_gate(ctx.t[i], s.side)))
        if eligible_counter:
            side, level, stop, target = counter_levels(s, counter_spec, atr)
            cand, dead = _fill_candidate(ctx, i, side, level, stop, target, cont, p, min_r, counter_armed, s.key)
            if dead:
                counter_dead.add(s.key)
            if cand is not None:
                cands["counter"] = {**cand, "level_r": counter_spec.level, "ratchet": counter_spec.ratchet}
        if not cands:
            i += 1
            continue
        chosen = (min(cands, key=lambda k: abs(ctx.o[i] - cands[k]["level_px"])) if len(cands) > 1
                  else next(iter(cands)))
        c = cands[chosen]
        done.add(s.key)
        res = manage_trade(ctx, p, c["side"], i, c["fill"], c["stop"], c["target"],
                           F.RATCHET_RULES[c["ratchet"]], s, hi_i, live)
        paths.append({"i": i, "x": res["x"], "side": c["side"], "ref": c["ref"], "fill": c["fill"],
                      "stop": c["stop"], "stop_final": res["stop_final"], "target": c["target"],
                      "exit_ref": res["exit_ref"], "exit_px": res["exit_px"], "reason": res["reason"],
                      "fund_unit": res["fund_unit"], "fee_in": c["fee_in"], "fee_out": res["fee_out"],
                      "lev": c["lev"], "planned_r": c["planned_r"], "level": c["level_r"], "moved": res["moved"],
                      "leg": chosen})
        if res["reason"] == "open":
            break
        i = res["x"] + 1
    return {"paths": paths, "refused": {}, "armed": len(with_armed) + len(counter_armed)}


# ── funding gate (H3) ────────────────────────────────────────────────────────

def fund_gate_factory(times: Sequence[int], zs: Sequence[float], threshold: float) -> FundGate:
    """True when the funding z-score at (or last known before) `t_ms` is
    extreme AGAINST `swing_side`: for a down-swing (side<0), a crowded-long
    reading (z >= threshold, longs still paying despite the decline); for
    an up-swing, crowded-short (z <= -threshold). Causal
    (`funding_signal.funding_z_at`: the most recent z at or before t)."""
    def gate(t_ms: int, swing_side: int) -> bool:
        z = funding_z_at(times, zs, t_ms)
        if z is None:
            return False
        return z >= threshold if swing_side < 0 else z <= -threshold
    return gate


def z_series(ctx: F.Ctx) -> Tuple[List[int], List[float]]:
    funding_sorted = sorted(ctx.fund.items())
    zmap = funding_zscore_series(funding_sorted)
    times = sorted(zmap)
    return times, [zmap[t] for t in times]


# ── variants (pure) ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Variant:
    kind: str                                   # "counter" (H1/H2/H3 alone) | "oco" (H4)
    ext: float = REFERENCE_EXT
    stop: str = REFERENCE_STOP
    tgt: float = REFERENCE_TGT
    ratchet: str = REFERENCE_RATCHET
    k_filter: Optional[float] = None
    fund_filter: Optional[float] = None

    @property
    def name(self) -> str:
        kf = f"_k{self.k_filter:g}" if self.k_filter else ""
        ff = f"_fz{self.fund_filter:g}" if self.fund_filter else ""
        base = f"ext{self.ext * 1000:.0f}_{self.stop}_T{self.tgt * 1000:.0f}_{self.ratchet}{kf}{ff}"
        return f"oco_{base}" if self.kind == "oco" else f"counter_{base}"

    @property
    def family(self) -> str:
        if self.kind == "oco":
            return "H4"
        tags = ["H1"]
        if self.k_filter is not None:
            tags.append("H2")
        if self.fund_filter is not None:
            tags.append("H3")
        return "+".join(tags)

    def counter_spec(self) -> LegSpec:
        return LegSpec(level=self.ext, stop=self.stop, target=self.tgt, ratchet=self.ratchet)

    def with_spec(self) -> Optional[LegSpec]:
        if self.kind != "oco":
            return None
        return LegSpec(level=LIVE.level, stop=LIVE.stop, target=LIVE.target, ratchet=LIVE.ratchet)


REFERENCE = Variant("counter")


def h1_h2_grid() -> List[Variant]:
    """The pre-registered 24: 3 extensions x 2 targets x 4 overextension
    settings, stop and ratchet fixed at the live sleeve's own choices."""
    return [Variant("counter", ext=e, tgt=t, k_filter=k) for e in EXT_LEVELS for t in COUNTER_TARGETS
            for k in K_FILTERS]


def h3_variants() -> List[Variant]:
    """4: funding threshold x {no overextension filter, k4}."""
    return [Variant("counter", fund_filter=fz, k_filter=k) for fz in FUND_THRESHOLDS for k in (None, 4.0)]


def sensitivity_variants() -> List[Variant]:
    """2 hand-picked checks on the reference variant, not a second grid:
    the "next"-level stop, and the ratchet off."""
    return [dataclasses.replace(REFERENCE, stop="next"), dataclasses.replace(REFERENCE, ratchet="off")]


def h4_variants() -> List[Variant]:
    """3: the reference counter leg beside the live with-swing rule, plus
    the reference + k4, plus the reference + funding 2.0 -- the same leg
    H1/H2/H3 found most defensible, not re-optimized for H4."""
    return [Variant("oco"), Variant("oco", k_filter=4.0), Variant("oco", fund_filter=2.0)]


def variants() -> List[Variant]:
    out = h1_h2_grid() + h3_variants() + sensitivity_variants() + h4_variants()
    assert len({v.name for v in out}) == len(out), "duplicate variant name"
    return out


def neighbours(v: Variant) -> List[Variant]:
    """One step along each ordered axis the grid actually varies (the
    extension, the target, the overextension filter) -- the plateau leg of
    `judge`. A variant outside the 24-point grid (H3's filter picks, the
    two sensitivity checks, H4's OCO rows) has no neighbours in this run:
    its plateau leg is honestly unsatisfiable, so it can be LEANING at best,
    never PASS -- a hand-picked point earns no plateau claim."""
    out = [dataclasses.replace(v, ext=x) for x in F._shift(EXT_LEVELS, v.ext)]
    out += [dataclasses.replace(v, tgt=x) for x in F._shift(COUNTER_TARGETS, v.tgt)]
    ks = [k for k in K_FILTERS if k is not None]
    if v.k_filter is not None:
        out += [dataclasses.replace(v, k_filter=x) for x in F._shift(ks, v.k_filter)]
    return out


# ── caches (per Ctx, memoized by identity) ───────────────────────────────────

def _cont_cache() -> Callable[[F.Ctx], Sequence[Optional[float]]]:
    cache: Dict[int, Sequence[Optional[float]]] = {}

    def get(ctx: F.Ctx) -> Sequence[Optional[float]]:
        key = id(ctx)
        if key not in cache:
            cache[key] = continuation_series(ctx.t, ctx.h, ctx.l, ctx.swings["range24"])
        return cache[key]
    return get


def _z_cache() -> Callable[[F.Ctx], Tuple[List[int], List[float]]]:
    cache: Dict[int, Tuple[List[int], List[float]]] = {}

    def get(ctx: F.Ctx) -> Tuple[List[int], List[float]]:
        key = id(ctx)
        if key not in cache:
            cache[key] = z_series(ctx)
        return cache[key]
    return get


# ── evaluation (mirrors fib_retrace_test.run_variant/evaluate) ──────────────

def run_variant(v: Variant, ds: F.Dataset, cont_cache: Callable[[F.Ctx], Sequence[Optional[float]]],
                zcache: Callable[[F.Ctx], Tuple[List[int], List[float]]], curves: bool = True,
                min_r: float = 0.0, keep: bool = False) -> Dict[str, Dict[str, Any]]:
    ctx, bp = ds.ctx, ds.params
    start = bp.starting_equity
    cont = cont_cache(ctx)
    with_spec, counter_spec = v.with_spec(), v.counter_spec()
    fund_gate = None
    if v.fund_filter is not None:
        times, zs = zcache(ctx)
        fund_gate = fund_gate_factory(times, zs, v.fund_filter)
    alone = walk_sleeve(ctx, bp, ds.lo_i, ds.hi_i, with_spec, counter_spec, min_r, None, v.k_filter, fund_gate, cont)
    comb = (walk_sleeve(ctx, bp, ds.lo_i, ds.hi_i, with_spec, counter_spec, min_r, ds.live, v.k_filter, fund_gate,
                        cont) if ds.live is not None else None)
    out: Dict[str, Dict[str, Any]] = {}
    for label, risk in F.RISKS:
        ta = F.size_paths(ctx, alone["paths"], bp, risk, start)
        if curves:
            flat = [start] * (ds.hi_i - ds.lo_i + 1)
            sa = I5.summarize(ta, I5.pnl_curve(ctx, ta, ds.lo_i, ds.hi_i), flat, alone["refused"])
        else:
            sa = I5.summarize(ta)
        sa["armed"] = alone["armed"]
        row: Dict[str, Any] = {"alone": sa}
        if comb is not None:
            tc = F.size_paths(ctx, comb["paths"], bp, risk, start, ds.live)
            if curves:
                sc = I5.summarize(tc, I5.pnl_curve(ctx, tc, ds.lo_i, ds.hi_i), ds.live.eq[ds.lo_i:ds.hi_i + 1],
                                  comb["refused"])
                row["collisions"] = SL.collisions(ta, ds.intervals or [], ds.end_ms)
                row["drop_best_day"] = SL.drop_best_day(tc)
            else:
                sc = I5.summarize(tc)
            row.update({"combined": sc, "live": ds.live_sum, "combined_dd_pct": sc.get("max_dd_pct"),
                        "combined_total_usd": round(ds.live_sum["net_usd"] + sc["net_usd"], 2)})
            if keep:
                row["trades_combined"] = tc
        if keep:
            row["trades_alone"] = ta
        out[label] = row
    return out


def evaluate(vs: Sequence[Variant], datasets: Dict[str, F.Dataset], halves: Sequence[F.Dataset],
            long_halves: Dict[str, Sequence[F.Dataset]], min_r: float = 0.0,
            log: Optional[Callable[[str], None]] = None) -> Dict[str, Dict[str, Any]]:
    cont_cache, zcache = _cont_cache(), _z_cache()
    rows: Dict[str, Dict[str, Any]] = {}
    t0 = time.time()
    for idx, v in enumerate(vs, 1):
        per = {name: run_variant(v, ds, cont_cache, zcache, True, min_r) for name, ds in datasets.items()}
        hv = [run_variant(v, hds, cont_cache, zcache, False, min_r) for hds in halves]
        lh = {name: [run_variant(v, hds, cont_cache, zcache, False, min_r) for hds in pair]
              for name, pair in long_halves.items()}
        for label, risk in F.RISKS:
            dsr = {name: per[name][label] for name in datasets}
            prim = dsr.get(F.PRIMARY, {})
            rows[f"{v.name}@{label}"] = {
                "name": v.name, "risk": label, "risk_frac": risk, "family": v.family, "variant": v,
                "datasets": dsr, "halves": [x[label]["combined"]["net_usd"] for x in hv],
                "halves_n": [x[label]["combined"]["n"] for x in hv],
                "long_halves": {name: [x[label]["alone"]["net_usd"] for x in pair] for name, pair in lh.items()},
                "long_halves_n": {name: [x[label]["alone"]["n"] for x in pair] for name, pair in lh.items()},
                "drop_best_day": prim.get("drop_best_day", {"pass": False, "rest_usd": 0.0})}
        if log is not None and (idx % max(1, len(vs) // 10) == 0 or idx == len(vs)):
            eta = (time.time() - t0) / idx * (len(vs) - idx)
            log(f"{idx}/{len(vs)} variants, about {eta:.0f}s left")
    by_name = {v.name: v for v in vs}
    for v in vs:
        nbr_names = [nb.name for nb in neighbours(v) if nb.name in by_name]
        for label, _ in F.RISKS:
            row = rows[f"{v.name}@{label}"]
            nets = [rows[f"{n}@{label}"]["datasets"][F.PRIMARY]["combined"]["net_usd"]
                    if F.PRIMARY in rows[f"{n}@{label}"]["datasets"] else None for n in nbr_names]
            row["neighbour_nets"] = nets
            row["judge"] = F.judge(row, nets)
            row["beats_all"] = all(d["combined"]["net_usd"] > EPS for d in row["datasets"].values()
                                   if "combined" in d)
            row["alone_holds"] = F.alone_holds(row)
    return rows


# ── random-level baseline and the reality check ─────────────────────────────

def random_level_variant(v: Variant, rng: random.Random) -> Variant:
    return dataclasses.replace(v, ext=rng.uniform(RANDOM_LO, RANDOM_HI))


def random_baseline(v: Variant, ds: F.Dataset, cont_cache: Callable[[F.Ctx], Sequence[Optional[float]]],
                    zcache: Callable[[F.Ctx], Tuple[List[int], List[float]]], draws: int = RANDOM_DRAWS,
                    seed: int = RANDOM_SEED, risk_label: str = "full") -> Dict[str, Any]:
    """`draws` same-filter, same-stop-rule, same-target-rule variants with a
    RANDOM extension ratio in [RANDOM_LO, RANDOM_HI] instead of `v.ext`, run
    alone on `ds`. Reports the real variant's net $ against the draws'
    distribution: percentile, mean, p95. Deterministic for a given seed."""
    rng = random.Random(seed)
    nets: List[float] = []
    for _ in range(draws):
        rv = random_level_variant(v, rng)
        row = run_variant(rv, ds, cont_cache, zcache, curves=False)[risk_label]
        nets.append(row["alone"]["net_usd"])
    observed = run_variant(v, ds, cont_cache, zcache, curves=False)[risk_label]["alone"]["net_usd"]
    nets_sorted = sorted(nets)
    pct = sum(1 for x in nets_sorted if x <= observed) / len(nets_sorted) * 100.0
    return {"observed_net_usd": round(observed, 2), "draws": draws, "seed": seed,
            "mean": round(statistics.mean(nets_sorted), 2), "median": round(statistics.median(nets_sorted), 2),
            "p95": round(nets_sorted[int(0.95 * (len(nets_sorted) - 1))], 2),
            "percentile_of_observed": round(pct, 1)}


def reality_check(grid: Sequence[Variant], ds: F.Dataset, cont_cache: Callable[[F.Ctx], Sequence[Optional[float]]],
                  zcache: Callable[[F.Ctx], Tuple[List[int], List[float]]], observed_best: float,
                  draws: int = REALITY_DRAWS, seed: int = REALITY_SEED, risk_label: str = "full") -> Dict[str, Any]:
    """The multiple-testing-honest check: `draws` times, independently
    randomize EVERY variant in `grid` (same filters/stop/target rules as
    each, random entry), run all of them, keep the draw's own best net $.
    That is the null distribution of "the best of N variants by chance
    alone". The real grid's own best (`observed_best`) is checked against
    THIS distribution, not the single-variant baseline -- a stricter,
    Bonferroni-style bar in one number: a reported percentile over 95
    means the real grid's winner is not explained by having tried N."""
    rng = random.Random(seed)
    null_max: List[float] = []
    for _ in range(draws):
        nets = []
        for v in grid:
            rv = random_level_variant(v, rng)
            row = run_variant(rv, ds, cont_cache, zcache, curves=False)[risk_label]
            nets.append(row["alone"]["net_usd"])
        null_max.append(max(nets))
    null_sorted = sorted(null_max)
    pct = sum(1 for x in null_sorted if x <= observed_best) / len(null_sorted) * 100.0
    return {"observed_best_usd": round(observed_best, 2), "draws": draws, "grid_size": len(grid), "seed": seed,
            "null_mean": round(statistics.mean(null_sorted), 2), "null_p95": round(null_sorted[int(0.95 * (len(
                null_sorted) - 1))], 2), "percentile_of_observed": round(pct, 1)}


def bootstrap_mean_r_ci(trades: Sequence[Dict[str, Any]], draws: int = BOOT_DRAWS, seed: int = BOOT_SEED
                        ) -> Dict[str, Any]:
    """95% bootstrap CI on the mean R per trade: resample the trade list
    with replacement `draws` times, percentile interval. None fields when
    there are no R-bearing trades."""
    rs = [t["r"] for t in trades if t.get("r") is not None]
    if not rs:
        return {"n": 0, "mean_r": None, "ci_low": None, "ci_high": None, "draws": draws}
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        sample = [rs[rng.randrange(len(rs))] for _ in range(len(rs))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(0.975 * (len(means) - 1))]
    return {"n": len(rs), "mean_r": round(sum(rs) / len(rs), 3), "ci_low": round(lo, 3), "ci_high": round(hi, 3),
            "draws": draws}


# ── cost stress, regime split, today's tape ─────────────────────────────────

def cost_stressed_params(p: Any) -> Any:
    """Double the taker fee, maker fee and slippage -- the method's cost
    stress. Everything else (leverage, sizing, tick size) untouched."""
    return dataclasses.replace(p, taker_fee=p.taker_fee * 2.0, maker_fee=p.maker_fee * 2.0,
                               slippage=p.slippage * 2.0)


def daily_efficiency_ratio(c1h: Sequence[Candle]) -> Dict[str, Optional[float]]:
    """Kaufman efficiency ratio per UTC calendar day from 1h closes:
    |net change over the day| / sum(|hourly close-to-close changes|). 1.0 =
    every hour moved the same direction (pure trend); near 0 = chop. None
    for a day with under 2 closes."""
    by_day: Dict[str, List[float]] = {}
    for b in sorted(c1h, key=lambda c: c.t):
        day = time.strftime("%Y-%m-%d", time.gmtime(b.t / 1000.0))
        by_day.setdefault(day, []).append(b.c)
    out: Dict[str, Optional[float]] = {}
    for day, closes in by_day.items():
        if len(closes) < 2:
            out[day] = None
            continue
        total = sum(abs(closes[k] - closes[k - 1]) for k in range(1, len(closes)))
        out[day] = abs(closes[-1] - closes[0]) / total if total > EPS else None
    return out


def regime_split(trades: Sequence[Dict[str, Any]], er_by_day: Dict[str, Optional[float]],
                 threshold: float = ER_TREND_THRESHOLD) -> Dict[str, Any]:
    """Trades bucketed by the entry day's efficiency ratio: n, win rate,
    avg R, net $ per bucket (trending / ranging / unknown)."""
    buckets: Dict[str, List[Dict[str, Any]]] = {"trending": [], "ranging": [], "unknown": []}
    for t in trades:
        day = (t.get("entry") or "")[:10]
        er = er_by_day.get(day)
        key = "unknown" if er is None else ("trending" if er >= threshold else "ranging")
        buckets[key].append(t)
    out = {}
    for key, ts in buckets.items():
        rs = [t["r"] for t in ts if t.get("r") is not None]
        out[key] = {"n": len(ts), "wins": sum(1 for t in ts if t["net_usd"] > 0),
                    "net_usd": round(sum(t["net_usd"] for t in ts), 2),
                    "avg_r": round(sum(rs) / len(rs), 3) if rs else None}
    return out


def trades_on_day(trades: Sequence[Dict[str, Any]], day: str) -> List[Dict[str, Any]]:
    return [t for t in trades if (t.get("entry") or "").startswith(day)]


def holdout_cut(start_ms: int, end_ms: int, frac: float = 0.7, step_ms: int = HOUR_MS) -> int:
    """The boundary of the fixed chronological holdout: `frac` of the way
    from `start_ms` to `end_ms` (by calendar time, not by trade count),
    rounded down to a `step_ms` boundary so it lands on a closed bar. The
    holdout is [this, end_ms) and is never used while picking a variant --
    only to check the chosen one afterward. Pure."""
    cut = start_ms + int((end_ms - start_ms) * frac)
    return cut - cut % step_ms


# ── data (mirrors fib_retrace_test.main's loading) ──────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import load_coin_candles

    state = os.environ.get("PATHIEL_STATE_DIR", "")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="Exit 0: a variant passes. 1: none does. 2: no journal.")
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR") or (
        os.path.join(state, "strategies", "oil", "theses") if state else ""),
        help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses",
                    help="READ ONLY: the 30-thesis replay set; brent_replay30 is skipped when missing")
    ap.add_argument("--ledger", default=os.path.join(state, "review", "ledger.jsonl") if state else "",
                    help="READ ONLY: the review's trade ledger, for the measured fee")
    ap.add_argument("--cache-dir", default="/tmp/fib-bidir/cache")
    ap.add_argument("--out-dir", default="/tmp/fib-bidir")
    ap.add_argument("--start-equity", type=float, default=F.START)
    ap.add_argument("--min-r", type=float, default=0.0)
    ap.add_argument("--quick", action="store_true",
                    help="a fast smoke run: brent_journal and brent_15m_50d only, 6 variants, no WTI, no extras")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} fib-bidir: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    def load(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        long_1h = D.fetch_candles_cached(coin, "1h", 5000, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        c15 = closed_only(got["c15"], FIFTEEN_MS, asof)
        c1h = closed_only(I5.merge_candles(long_1h, got["c1h"]), HOUR_MS, asof)
        end = c15[-1].t + FIFTEEN_MS
        end_h = min(c1h[-1].t + HOUR_MS, end - end % HOUR_MS)
        c1h = [b for b in c1h if b.t + HOUR_MS <= end_h]
        return c15, c1h, end, end_h, F.load_funding(coin, c1h[0].t, end, a.cache_dir)

    theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if not theses:
        log(f"no theses at {a.oil_theses_dir!r}: nothing to judge")
        return 2
    replay = [] if a.quick else (D.load_journaled_theses(a.oil_replay_dir) if a.oil_replay_dir else [])
    fees = I5.measured_fees(a.ledger)
    repo = base_params(a.start_equity, F.RISK_FULL)
    bp = dataclasses.replace(repo, taker_fee=fees["taker"], maker_fee=fees["maker"])
    merged = F.merge_theses(replay, theses)

    datasets: Dict[str, F.Dataset] = {}
    meta: Dict[str, Any] = {}

    def add(name: str, ctx: F.Ctx, th_at, start: int, end: int, live_args=None) -> F.Dataset:
        run = None
        if live_args is not None:
            c15, c1h, dir_at, fund = live_args
            run = SL.prepare(c15, c1h, dir_at, th_at, bp, fund, start, end)
        ds = F.make_dataset(name, ctx, F.thesis_sides(ctx, th_at), bp, start, end, run)
        datasets[name] = ds
        meta[name] = {"window": [_iso(start), _iso(end)], "days": round((end - start) / DAY_MS, 1),
                      "bars": ds.hi_i - ds.lo_i + 1, "bar": "15m" if ctx.step == FIFTEEN_MS else "1h"}
        return ds

    b15, b1h, b_end, b_end_h, b_fund = load(F.BRENT)
    bctx = F.build_ctx(b15, b1h, b_fund, FIFTEEN_MS)
    bctx_h = F.build_ctx(b1h, b1h, b_fund, HOUR_MS)
    start = max(min(th.created_at_ms for th in theses), b15[0].t)
    start += -start % FIFTEEN_MS
    dir_at, th_at = journaled_direction(theses), SL.thesis_lookup(theses)
    add(F.PRIMARY, bctx, th_at, start, b_end, (b15, b1h, dir_at, b_fund))
    mid = start + (b_end - start) // 2
    mid -= mid % FIFTEEN_MS
    halves = []
    for k, (s, e) in enumerate(((start, mid), (mid, b_end)), 1):
        run = SL.prepare(b15, b1h, dir_at, th_at, bp, b_fund, s, e)
        halves.append(F.make_dataset(f"half{k}", bctx, datasets[F.PRIMARY].thesis, bp, s, e, run))
    if replay:
        r_start = max(min(th.created_at_ms for th in replay), b15[0].t)
        r_start += -r_start % FIFTEEN_MS
        r_end = min(b_end, max(th.created_at_ms for th in replay) + DAY_MS)
        r_end -= r_end % FIFTEEN_MS
        add("brent_replay30", bctx, SL.thesis_lookup(replay), r_start, r_end,
            (b15, b1h, journaled_direction(replay), b_fund))
    m_at = SL.thesis_lookup(merged)
    add("brent_15m_50d", bctx, m_at, b15[0].t + F.WARMUP_15M_MS, b_end)
    if not a.quick:
        add("brent_1h_208d", bctx_h, m_at, b1h[0].t + F.WARMUP_1H_MS, b_end_h)
        w15, w1h, w_end, w_end_h, w_fund = load(F.WTI)
        wctx = F.build_ctx(w15, w1h, w_fund, FIFTEEN_MS)
        wctx_h = F.build_ctx(w1h, w1h, w_fund, HOUR_MS)
        w_start, w_stop = max(start, w15[0].t), min(b_end, w_end)
        add("wti_journal", wctx, SL.thesis_lookup(theses, SL.price_ratio(theses, b1h, w1h)), w_start, w_stop,
            (w15, w1h, side_only(dir_at), w_fund))
        wm_at = SL.thesis_lookup(merged, SL.price_ratio(merged, b1h, w1h))
        add("wti_15m_50d", wctx, wm_at, w15[0].t + F.WARMUP_15M_MS, w_end)
        add("wti_1h_208d", wctx_h, wm_at, w1h[0].t + F.WARMUP_1H_MS, w_end_h)

    long_halves: Dict[str, List[F.Dataset]] = {}
    holdout: Dict[str, Any] = {}
    for name in ("brent_1h_208d", "wti_1h_208d"):
        if name in datasets:
            d = datasets[name]
            m = d.start_ms + (d.end_ms - d.start_ms) // 2
            m -= m % HOUR_MS
            long_halves[name] = [F.make_dataset(f"{name}_h{k}", d.ctx, d.thesis, bp, s, e)
                                 for k, (s, e) in enumerate(((d.start_ms, m), (m, d.end_ms)), 1)]
            # the fixed holdout: the last ~30% by calendar time, never touched while choosing a variant
            cut = holdout_cut(d.start_ms, d.end_ms)
            holdout[name] = F.make_dataset(f"{name}_holdout", d.ctx, d.thesis, bp, cut, d.end_ms)

    vs = variants() if not a.quick else [REFERENCE, dataclasses.replace(REFERENCE, k_filter=4.0),
                                         dataclasses.replace(REFERENCE, fund_filter=2.0),
                                         Variant("oco"), dataclasses.replace(REFERENCE, ratchet="off"),
                                         dataclasses.replace(REFERENCE, stop="next")]
    names = list(datasets)
    log(f"{len(theses)} journal theses, {len(replay)} replay; {len(vs)} variants x {len(F.RISKS)} risk sizes on "
        f"{names}; fee {fees}; min_r {a.min_r}; quick={a.quick}")
    log(f"follow with: tail -f {os.path.join(a.out_dir, 'progress.log')}")
    rows = evaluate(vs, datasets, halves, long_halves, a.min_r, log)

    cont_cache, zcache = _cont_cache(), _z_cache()
    h1h2 = h1_h2_grid()
    ref_row = rows[f"{REFERENCE.name}@full"]
    ref_net_primary = ref_row["datasets"].get(F.PRIMARY, {}).get("alone", {}).get("net_usd", 0.0)

    log("random baseline (200 draws, brent_journal, alone, full risk)...")
    rbase = random_baseline(REFERENCE, datasets[F.PRIMARY], cont_cache, zcache) if F.PRIMARY in datasets else None

    reality = None
    if not a.quick:
        best_h1h2 = max(h1h2, key=lambda v: rows[f"{v.name}@full"]["datasets"][F.PRIMARY]["alone"]["net_usd"]
                        if F.PRIMARY in rows[f"{v.name}@full"]["datasets"] else -1e18)
        best_net = rows[f"{best_h1h2.name}@full"]["datasets"][F.PRIMARY]["alone"]["net_usd"]
        log("reality check (50 draws x 24 variants, brent_journal, alone, full risk)...")
        reality = reality_check(h1h2, datasets[F.PRIMARY], cont_cache, zcache, best_net)

    oco_row_full = run_variant(h4_variants()[0], datasets[F.PRIMARY], cont_cache, zcache, True, a.min_r, keep=True)[
        "full"] if F.PRIMARY in datasets else None
    boot = bootstrap_mean_r_ci(oco_row_full["trades_alone"]) if oco_row_full else {"n": 0}

    cost_stress = {}
    if F.PRIMARY in datasets:
        stressed_bp = cost_stressed_params(bp)
        stressed_ds = dataclasses.replace(datasets[F.PRIMARY], params=stressed_bp)
        base_row = run_variant(h4_variants()[0], stressed_ds, cont_cache, zcache, True, a.min_r)["full"]
        cost_stress["oco_reference"] = {"alone_net_usd": base_row["alone"]["net_usd"],
                                        "alone_n": base_row["alone"]["n"]}
        base_row_counter = run_variant(REFERENCE, stressed_ds, cont_cache, zcache, True, a.min_r)["full"]
        cost_stress["counter_reference"] = {"alone_net_usd": base_row_counter["alone"]["net_usd"],
                                            "alone_n": base_row_counter["alone"]["n"]}

    regime = None
    if oco_row_full:
        er = daily_efficiency_ratio(b1h)
        regime = regime_split(oco_row_full["trades_alone"], er)

    today = None
    if oco_row_full:
        today = {"alone": trades_on_day(oco_row_full["trades_alone"], "2026-11-16"),
                 "beside_live": trades_on_day(oco_row_full.get("trades_combined", []), "2026-11-16")}

    holdout_results = {}
    for name, ds in holdout.items():
        r = run_variant(REFERENCE, ds, cont_cache, zcache, curves=False)["full"]
        holdout_results[name] = {"n": r["alone"]["n"], "net_usd": r["alone"]["net_usd"],
                                 "win_rate": r["alone"]["win_rate"]}

    full = [r for r in rows.values() if r["risk"] == "full"]
    passing = sorted({r["name"] for r in full if r["judge"]["verdict"] == "PASS"})
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "start_equity": a.start_equity, "min_r": a.min_r, "quick": a.quick, "datasets": meta,
        "fees": fees, "n_variants": len(vs), "n_judged_rows": len(rows),
        "verdicts": {label: dict(Counter(r["judge"]["verdict"] for r in rows.values() if r["risk"] == label))
                     for label, _ in F.RISKS},
        "passing": passing, "reference_variant": REFERENCE.name, "reference_primary_alone_net_usd": ref_net_primary,
        "random_baseline": rbase, "reality_check": reality, "bootstrap_mean_r_ci_oco_reference": boot,
        "cost_stress_2x": cost_stress, "regime_split_oco_reference": regime, "todays_tape_2026_11_16": today,
        "fixed_holdout_last_30pct": holdout_results,
        "rows": {k: {kk: vv for kk, vv in v.items() if kk != "variant"} for k, v in rows.items()},
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)

    csv_path = os.path.join(a.out_dir, "variants.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        head = ["variant", "risk", "family", "verdict"]
        for ds in names:
            modes = ("alone", "combined") if ds in F.BESIDE else ("alone",)
            head += [f"{ds}_{m}_{k}" for m in modes for k in ("n", "wins", "net_usd", "avg_r", "max_dd_pct")]
        w.writerow(head + ["alone_holds"])
        for k in sorted(rows):
            r = rows[k]
            line = [r["name"], r["risk"], r["family"], r["judge"]["verdict"]]
            for ds in names:
                d = r["datasets"][ds]
                for m in (("alone", "combined") if ds in F.BESIDE else ("alone",)):
                    if m not in d:
                        line += [None] * 5
                        continue
                    s = d[m]
                    line += [s["n"], s["wins"], s["net_usd"], s["avg_r"], s.get("max_dd_pct")]
            w.writerow(line + [r["alone_holds"]])

    print(f"\n# Fib bidirectional: {len(vs)} variants x {len(F.RISKS)} risk sizes, {report['seconds']}s")
    print(f"fee {fees}")
    print(f"verdicts {report['verdicts']}; passing: {passing or 'none'}")
    print(f"reference ({REFERENCE.name}) alone on {F.PRIMARY}: ${ref_net_primary}")
    if rbase:
        print(f"random baseline: observed ${rbase['observed_net_usd']} at the "
              f"{rbase['percentile_of_observed']}th percentile of {rbase['draws']} draws (p95 ${rbase['p95']})")
    if reality:
        print(f"reality check (best of {reality['grid_size']} vs best-of-{reality['grid_size']}-by-chance): "
              f"${reality['observed_best_usd']} at the {reality['percentile_of_observed']}th percentile")
    print(f"bootstrap mean R (H4 reference, alone, {F.PRIMARY}): {boot}")
    print(f"cost stress (2x fees+slippage): {cost_stress}")
    print(f"regime split (H4 reference, alone): {regime}")
    print(f"today's tape 2026-11-16: {len(today['alone']) if today else 0} alone trade(s), "
          f"{len(today['beside_live']) if today else 0} beside live")
    print(f"fixed holdout (last 30% of each 208d set): {holdout_results}")
    log(f"done in {time.time() - t0:.0f}s; passing: {passing or 'none'}")
    print(f"\nreport: {out_path}\nvariants: {csv_path}")
    return 0 if passing else 1


if __name__ == "__main__":
    raise SystemExit(main())
