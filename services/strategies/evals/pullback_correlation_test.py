"""Pullback entry and cross-desk correlation guard test, 2026-11-02
(docs/strategy/PULLBACK_AND_CORRELATION_TEST_2026-11-02.md).

Operator, 2026-11-02: "YES" to testing both fixes after the oil thesis
turned short at 08:42Z and Brent fell 109 to 106.19 with the desk flat (the
live entry rule only sells near the 24h high), and the silver short was
stopped in the same minutes as silver jumped.

Fix A, pullback / breakdown entry (desk_tick_sim `Params.pullback_entry`):
when the live setup is refused as a chase or under 2R, try a setup on
structure closer to price. a1 stop beyond the last N 1h bars' extreme;
a2 market on a 1h retest of a broken 24h edge; a3 a resting limit k x ATR14
off the last 1h extreme, cancelled after N hours; a4 the thesis's own
levels while it is fresh. Run on Brent (the bar) and silver.

Fix B, correlation guard across desks (portfolio `CorrGuard`): the sign of
the oil/silver 1h return correlation (overall and on |Brent 1h| >= 1%)
says which pair of positions is two opposite bets; the guard blocks
(mult 0) or scales (0.25 / 0.5 / 0.75) the second desk's entry when the
other desk holds a clashing side. B3 (same driver in both theses) is
scored from a keyword scan of the theses' own text.

Live rule: desk_tick_sim.live_params() with size slippage (Brent), funding,
the 15% risk cap; silver on the live ATR stop with its 0.8% floor. $225.

Adopt bar (the regime test's): beats live on most datasets with a geo-mean
of 1 or more; beats live on more datasets than sitting out does; a plateau
(every grid neighbour also passes the first rule); mean max drawdown no
worse; every stop inside half the way to liquidation at the fill.

    python -m services.strategies.evals.pullback_correlation_test \\
        --oil-theses-dir <journal>/oil/theses --metals-theses-dir <journal>/metals/theses

Writes /tmp/pullback-corr/report.json, a_variants.csv, b_variants.csv,
correlation.csv, nov2.json, progress.log. Public candles and funding only
(cached under --cache-dir); journals are read only. No LLM calls.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Params, Run
from services.strategies.backtest.portfolio import (CorrGuard, Desk, pearson, portfolio_stats,
                                                    simulate_portfolio)
from services.strategies.backtest.regime import DAY_MS, HOUR_MS, RegimeSeries, label_series
from services.strategies.evals import oil_regime_test as O

START = O.START                       # $225, the account
RISK_CAP = 0.15
FIFTEEN_MS = 900_000
BASELINE = "live"
SILVER_MAX_LEV = 25.0
COINS = {"brent": "xyz:BRENTOIL", "silver": "xyz:SILVER"}

# ── Fix A grids
A1_STRUCT = (4, 6, 8, 12)
A2_RETEST = (6, 12, 24)
A3_K = (0.5, 1.0, 1.5)
A3_CANCEL = (2, 4, 8)
A4_FRESH = (1, 2, 4)
TARGETS = ("far", "thesis")

# ── Fix B grids
PAIRS = ("same", "opposite")
WHO = {"silver": ("silver",), "both": ()}
MULTS = (0.0, 0.25, 0.5, 0.75)
SPLITS = {"90/10": 0.9, "70/30": 0.7}
LIVE_SPLIT = "90/10"
BIG_MOVE = 0.01                       # |Brent 1h| >= 1%: an Iran-headline-sized hour

# ── 2026-11-02
NOV2_A_FROM = (2026, 11, 2, 8, 45)    # first 15m tick after the 08:40:22Z short thesis
NOV2_B_FROM = (2026, 11, 2, 0, 0)
# The real fills of 11-02 (public userFills, /tmp/review/bundle-live, LOSS_AUDIT_2026-11-02.md):
# (desk, side, entry UTC, exit UTC, net $). The 05:34Z silver short topped up at 05:49Z.
NOV2_REAL = (("silver", "short", (2026, 11, 2, 0, 51), (2026, 11, 2, 1, 5), -1.82),
             ("silver", "short", (2026, 11, 2, 1, 18), (2026, 11, 2, 5, 30), -2.56),
             ("oil", "long", (2026, 11, 2, 3, 50), (2026, 11, 2, 8, 35), -33.87),
             ("silver", "short", (2026, 11, 2, 5, 34), (2026, 11, 2, 13, 38), -2.85))

# Driver tags from a thesis's own argument (call one-liner, bull, bear), for B3.
DRIVER_RE = {"iran": re.compile(r"\biran|hormuz", re.I),
             "rates": re.compile(r"\bfed\b|fomc|real yield|rate (?:cut|hike)s?|cuts? priced", re.I)}


def _ms(parts: Sequence[int]) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)


def _iso(ms: Optional[int]) -> Optional[str]:
    return O._iso(ms)


# ── Fix A variants (pure) ──────────────────────────────────────────────────

def a_variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides on the live params."""
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for tgt in TARGETS:
        for h in A1_STRUCT:
            out[f"a1_{h}h_{tgt}"] = {"pullback_entry": "a1", "pb_struct_h": h, "pb_target": tgt}
        out[f"a1_8h_{tgt}_nocap"] = {"pullback_entry": "a1", "pb_struct_h": 8, "pb_target": tgt,
                                     "pb_cap_thesis_stop": False}
        for h in A2_RETEST:
            out[f"a2_{h}h_{tgt}"] = {"pullback_entry": "a2", "pb_retest_h": h, "pb_target": tgt}
        for k in A3_K:
            for c in A3_CANCEL:
                out[f"a3_k{k:g}_c{c}_{tgt}"] = {"pullback_entry": "a3", "pb_bounce_atr": k, "pb_cancel_h": float(c),
                                                "pb_target": tgt}
    for h in A4_FRESH:
        out[f"a4_{h}h"] = {"pullback_entry": "a4", "pb_fresh_h": float(h)}
    # Fit leverage: a stop too wide for 20x trades at the leverage it passes (5x or more).
    for h in A1_STRUCT:
        out[f"a1_{h}h_thesis_fit"] = {"pullback_entry": "a1", "pb_struct_h": h, "pb_target": "thesis",
                                      "pb_fit_leverage": True}
    for h in A2_RETEST:
        out[f"a2_{h}h_thesis_fit"] = {"pullback_entry": "a2", "pb_retest_h": h, "pb_target": "thesis",
                                      "pb_fit_leverage": True}
    for h in A4_FRESH:
        out[f"a4_{h}h_fit"] = {"pullback_entry": "a4", "pb_fresh_h": float(h), "pb_fit_leverage": True}
    return out


def _line(grid: Sequence[Any], fmt: Callable[[Any], str]) -> Dict[str, List[str]]:
    names = [fmt(x) for x in grid]
    return {n: [names[j] for j in (i - 1, i + 1) if 0 <= j < len(names)] for i, n in enumerate(names)}


def a_families() -> Dict[str, List[str]]:
    """Plateau neighbours: the next grid value either side (a3: along k and
    along the cancel time). The no-cap a1 rows are contrasts, no family."""
    fam: Dict[str, List[str]] = {}
    for tgt in TARGETS:
        fam.update(_line(A1_STRUCT, lambda h, t=tgt: f"a1_{h}h_{t}"))
        fam.update(_line(A2_RETEST, lambda h, t=tgt: f"a2_{h}h_{t}"))
        for k in A3_K:
            for c in A3_CANCEL:
                ki, ci = A3_K.index(k), A3_CANCEL.index(c)
                nb = [f"a3_k{A3_K[j]:g}_c{c}_{tgt}" for j in (ki - 1, ki + 1) if 0 <= j < len(A3_K)]
                nb += [f"a3_k{k:g}_c{A3_CANCEL[j]}_{tgt}" for j in (ci - 1, ci + 1) if 0 <= j < len(A3_CANCEL)]
                fam[f"a3_k{k:g}_c{c}_{tgt}"] = nb
    fam.update(_line(A4_FRESH, lambda h: f"a4_{h}h"))
    fam.update(_line(A1_STRUCT, lambda h: f"a1_{h}h_thesis_fit"))
    fam.update(_line(A2_RETEST, lambda h: f"a2_{h}h_thesis_fit"))
    fam.update(_line(A4_FRESH, lambda h: f"a4_{h}h_fit"))
    return fam


# ── Fix B variants (pure) ──────────────────────────────────────────────────

def b_name(pair: str, who: str, mult: float) -> str:
    return f"b_{pair}_{who}_m{mult:g}"


def b_variants() -> Dict[str, Optional[CorrGuard]]:
    out: Dict[str, Optional[CorrGuard]] = {BASELINE: None}
    for pair in PAIRS:
        for who, desks in WHO.items():
            for m in MULTS:
                out[b_name(pair, who, m)] = CorrGuard(pair=pair, mult=m, desks=desks)
    return out


def b_families() -> Dict[str, List[str]]:
    fam: Dict[str, List[str]] = {}
    for pair in PAIRS:
        for who in WHO:
            fam.update(_line(MULTS, lambda m, p=pair, w=who: b_name(p, w, m)))
    return fam


def opposite_pair(corr: Optional[float]) -> Optional[str]:
    """Which pair of positions is two opposite bets on one move: with a
    negative oil/silver correlation, positions on the SAME side offset each
    other (oil short wins when silver short loses); with a positive one,
    positions on OPPOSITE sides do. Position P&L correlation = side x side x
    corr. None when the correlation is missing or zero."""
    if corr is None or corr == 0:
        return None
    return "same" if corr < 0 else "opposite"


# ── correlation (pure) ─────────────────────────────────────────────────────

def hourly_returns(c1h: Sequence[Candle]) -> Dict[int, float]:
    """Close-to-close return of each 1h bar, keyed by the bar's open time
    (needs the previous hour's bar)."""
    bars = sorted(c1h, key=lambda c: c.t)
    out: Dict[int, float] = {}
    for a, b in zip(bars, bars[1:]):
        if b.t - a.t == HOUR_MS and a.c > 0:
            out[b.t] = b.c / a.c - 1.0
    return out


def daily_returns(c1h: Sequence[Candle]) -> Dict[int, float]:
    """UTC-day close-to-close returns from the last 1h close of each day."""
    last: Dict[int, float] = {}
    for c in sorted(c1h, key=lambda c: c.t):
        last[c.t // DAY_MS] = c.c
    days = sorted(last)
    return {d: last[d] / last[p] - 1.0 for p, d in zip(days, days[1:]) if d - p == 1 and last[p] > 0}


def corr_stats(a: Mapping[int, float], b: Mapping[int, float], big: float = BIG_MOVE,
               keys: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """Pearson of `a` (Brent) and `b` (silver) on shared keys; the same on
    keys where |a| >= big; the share of those big hours where silver moved
    the other way; and silver's mean move on big Brent down / up hours."""
    ks = sorted(set(a) & set(b) if keys is None else (set(keys) & set(a) & set(b)))
    xs, ys = [a[k] for k in ks], [b[k] for k in ks]
    bk = [k for k in ks if abs(a[k]) >= big]
    bx, by = [a[k] for k in bk], [b[k] for k in bk]
    opp = sum(1 for k in bk if a[k] * b[k] < 0)
    down = [b[k] for k in bk if a[k] < 0]
    up = [b[k] for k in bk if a[k] > 0]

    def r(v: Optional[float]) -> Optional[float]:
        return None if v is None else round(v, 3)
    return {"n": len(ks), "corr": r(pearson(xs, ys)), "n_big": len(bk), "corr_big": r(pearson(bx, by)),
            "opposite_share_big": round(opp / len(bk), 3) if bk else None,
            "silver_mean_on_brent_down_pct": round(100 * sum(down) / len(down), 3) if down else None,
            "silver_mean_on_brent_up_pct": round(100 * sum(up) / len(up), 3) if up else None,
            "n_big_down": len(down), "n_big_up": len(up)}


def monthly_corr(a: Mapping[int, float], b: Mapping[int, float]) -> List[Dict[str, Any]]:
    by_m: Dict[str, List[int]] = {}
    for k in sorted(set(a) & set(b)):
        by_m.setdefault(datetime.fromtimestamp(k / 1000, tz=timezone.utc).strftime("%Y-%m"), []).append(k)
    return [{"month": m, **corr_stats(a, b, keys=ks)} for m, ks in by_m.items()]


# ── thesis driver tags (pure) ──────────────────────────────────────────────

def driver_tags(raw: Mapping[str, Any]) -> FrozenSet[str]:
    """Tags of a thesis's own argument: the call's one-liner and the bull and
    bear texts (not the catalyst calendar or the sources)."""
    call = raw.get("call") or {}
    parts = [str(call.get("one_liner") or "")]
    for k in ("bull", "bear"):
        for item in raw.get(k) or []:
            parts.append(str(item.get("text") if isinstance(item, dict) else item))
    text = " ".join(parts)
    return frozenset(tag for tag, rx in DRIVER_RE.items() if rx.search(text))


def load_tags(theses_dir: str, keep: Optional[set] = None) -> List[Tuple[int, FrozenSet[str]]]:
    """(created ms, tags) per thesis file, oldest first. READ ONLY."""
    out: List[Tuple[int, FrozenSet[str]]] = []
    if not theses_dir or not os.path.isdir(theses_dir):
        return out
    for name in sorted(os.listdir(theses_dir)):
        if not name.endswith(".json"):
            continue
        raw = D.read_json(os.path.join(theses_dir, name))
        if not isinstance(raw, dict):
            continue
        try:
            t = int(datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00")).timestamp() * 1000)
        except (KeyError, ValueError, TypeError):
            continue
        if keep is not None and t not in keep:
            continue
        out.append((t, driver_tags(raw)))
    return sorted(out)


def tags_at(rows: Sequence[Tuple[int, FrozenSet[str]]]) -> Callable[[int], FrozenSet[str]]:
    def at(t: int) -> FrozenSet[str]:
        cur: FrozenSet[str] = frozenset()
        for ct, tags in rows:
            if ct <= t:
                cur = tags
            else:
                break
        return cur
    return at


# ── per-run numbers (pure) ─────────────────────────────────────────────────

def pb_stats(run: Run) -> Dict[str, Any]:
    """The trades the pullback entry opened: count, wins, summed R, $."""
    trs = [t for t in run.trades if t.entry_kind]
    rs = [t.r_multiple for t in trs if t.r_multiple is not None]
    return {"pb_trades": len(trs), "pb_wins": sum(1 for t in trs if t.net_pnl_usd > 0),
            "pb_sum_r": round(sum(rs), 2), "pb_net_usd": round(sum(t.net_pnl_usd for t in trs), 2)}


def priced_at(side: str, entry: float, size: float, px: float, entry_fee: float, exit_fee_rate: float) -> float:
    """Net $ of a position closed at `px`: the move, less its entry fee and
    the exit fee at `exit_fee_rate` (no slippage)."""
    sign = 1.0 if side == "long" else -1.0
    return sign * (px - entry) * size - entry_fee - exit_fee_rate * px * size


def nov2_row(run: Run, p: Params) -> Dict[str, Any]:
    """The first trade of an 11-02 run (or why none): entry, levels, how it
    ended, and $ at the last mid (realized + open), at its stop and at its
    target (no slippage)."""
    realized = sum(t.net_pnl_usd for t in run.trades)
    open_net = 0.0
    op, lv = run.open_position_at_end, run.open_position_levels
    if op and lv:
        open_net = op["unrealized_pnl_usd"] - lv["entry_fees_usd"] - lv["funding_usd"]
    row: Dict[str, Any] = {"pnl_at_end_usd": round(realized + open_net, 2), "trades": len(run.trades),
                           "rested": run.rested, "lapsed": run.cancelled_resting,
                           "refused": dict(sorted(run.refused.items()))}
    if run.trades:
        tr = run.trades[0]
        stop = tr.orig_stop_px or tr.stop_px
        fee_in = (tr.fees_usd - tr.exit_fee_usd)
        row.update({"entry": _iso(tr.entry_time), "side": tr.side, "kind": tr.entry_kind or "live",
                    "entry_px": round(tr.entry_px, 3), "stop_px": round(stop, 3), "target_px": round(tr.target_px, 3),
                    "exit": tr.exit_reason, "exit_time": _iso(tr.exit_time), "exit_px": round(tr.exit_px or 0, 3),
                    "net_usd": round(tr.net_pnl_usd, 2),
                    "at_stop_usd": round(priced_at(tr.side, tr.entry_px, tr.size, stop, fee_in, p.taker_fee), 2),
                    "at_target_usd": round(priced_at(tr.side, tr.entry_px, tr.size, tr.target_px, fee_in,
                                                     p.maker_fee), 2)})
    elif op and lv:
        row.update({"entry": _iso(lv["opened_at"]), "side": op["side"], "kind": lv["entry_kind"] or "live",
                    "entry_px": round(op["entry_px"], 3), "stop_px": round(lv["orig_stop_px"], 3),
                    "target_px": round(lv["target_px"], 3), "exit": "open", "mark_px": round(op["mark_px"], 3),
                    "at_stop_usd": round(priced_at(op["side"], op["entry_px"], op["size"], lv["orig_stop_px"],
                                                   lv["entry_fees_usd"], p.taker_fee), 2),
                    "at_target_usd": round(priced_at(op["side"], op["entry_px"], op["size"], lv["target_px"],
                                                     lv["entry_fees_usd"], p.maker_fee), 2)})
    else:
        row["entry"] = None
    return row


def real_guard_rows(real: Sequence[Tuple[str, str, Sequence[int], Sequence[int], float]] = NOV2_REAL
                    ) -> Dict[str, Dict[str, Any]]:
    """Each guard variant on the real 11-02 fills: which entries it would
    have blocked or scaled (the other desk's real position at that minute),
    and the real net with that change (a blocked trade counts 0, a scaled one
    its net x mult; a refused entry is not a saved loss for sure, the desk
    could have entered later)."""
    rows = sorted(((desk, side, _ms(a), _ms(b), net) for desk, side, a, b, net in real), key=lambda r: r[2])
    out: Dict[str, Dict[str, Any]] = {}
    for name, g in b_variants().items():
        total, acts, taken = 0.0, [], []
        for desk, side, t0, t1, net in rows:
            # what the other desk held at that minute: only trades the guard let through
            held = {d: s for d, s, a, b in taken if d != desk and a <= t0 < b}
            m = 1.0
            if g is not None and (not g.desks or desk in g.desks):
                if any((h == side) if g.pair == "same" else (h != side) for h in held.values()):
                    m = g.mult
            if m != 1.0:
                acts.append(f"{desk} {side} {_iso(t0)} x{m:g} (other: {', '.join(held.values())})")
            if m > 0:
                taken.append((desk, side, t0, t1))
            total += net * m
        out[name] = {"net_usd": round(total, 2), "acted": acts}
    return out


# ── data and runs ──────────────────────────────────────────────────────────

def silver_params(c1h: Sequence[Candle]) -> Params:
    from services.strategies.evals.brent_stop_buffer_test import SILVER_LIVE
    from services.strategies.evals.cross_market_backtest import mean_atr
    from services.strategies.evals.market_scout import scouting_params
    atr, _ = mean_atr(c1h)
    return dataclasses.replace(scouting_params(SILVER_MAX_LEV, atr), size_slippage=False, starting_equity=START,
                               risk_cap_frac=RISK_CAP, **SILVER_LIVE)


def brent_params() -> Params:
    from services.strategies.evals.strategy_sweep import base_params
    return dataclasses.replace(base_params(), starting_equity=START, risk_cap_frac=RISK_CAP)


def run_a(coin: Dict[str, Any], spec: Dict[str, Any], base: Params, ov: Dict[str, Any]) -> Run:
    return O.run_one(coin, spec, base, ov, {})


def max_liq_frac(run: Run) -> float:
    """The widest stop of the run as a share of the way to liquidation, at
    each trade's own leverage (a fit-leverage trade is gated at its own)."""
    p = run.params or Params()
    out = 0.0
    for t in run.trades:
        lev = t.leverage or p.leverage
        f = O.stop_liq_frac(t.entry_px, t.orig_stop_px or t.stop_px, dataclasses.replace(p, leverage=lev))
        if f is not None:
            out = max(out, f)
    return round(out, 3)


def portfolio_specs(coins: Dict[str, Dict[str, Any]], theses: Dict[str, Dict[str, List[D.ThesisDirection]]]
                    ) -> Dict[str, Dict[str, Any]]:
    """name -> {ticks, start, end, oil, silver (direction callables)}."""
    from services.strategies.evals.desk_tick_backtest import journaled_direction
    from services.strategies.evals.market_scout import direction_series
    b, s = coins["brent"], coins["silver"]
    end = min(b["end"], s["end"])
    full_start = max((O.day_of(c["c1h"][0].t) + O.WARMUP_DAYS) * DAY_MS for c in (b, s))
    q_start = max((O.day_of(c["c15"][0].t) + 1) * DAY_MS for c in (b, s))
    out: Dict[str, Dict[str, Any]] = {}
    oj, sj = theses["brent"].get("journal") or [], theses["silver"].get("journal") or []
    if oj and sj:
        out["journal_shared"] = {"ticks": 15, "start": min(t.created_at_ms for t in sj), "end": end,
                                 "oil": journaled_direction(oj), "silver": journaled_direction(sj)}
    orr, srr = theses["brent"].get("replay30") or [], theses["silver"].get("replay30") or []
    if orr and srr:
        st = max(min(t.created_at_ms for t in orr), min(t.created_at_ms for t in srr))
        en = min(end, max(t.created_at_ms for t in orr) + DAY_MS, max(t.created_at_ms for t in srr) + DAY_MS)
        out["replay"] = {"ticks": 15, "start": st, "end": en, "oil": journaled_direction(orr),
                         "silver": journaled_direction(srr)}
    for c in O.CONTROLS:
        bd, sd = direction_series(c, b["c1h"]), direction_series(c, s["c1h"])
        out[f"{c}_full_1h"] = {"ticks": 60, "start": full_start, "end": end, "oil": bd, "silver": sd}
        out[f"{c}_52d_15m"] = {"ticks": 15, "start": q_start, "end": end, "oil": bd, "silver": sd}
    return out


def desk_for(name: str, share: float, coin: Dict[str, Any], spec: Dict[str, Any], d_at: Callable,
             p: Params, a_ov: Optional[Dict[str, Any]] = None) -> Desk:
    st, en = spec["start"], spec["end"]
    p = dataclasses.replace(p, tick_minutes=spec["ticks"], **O.sim_overrides(a_ov or {}))
    c1h = [c for c in coin["c1h"] if c.t < en]
    if spec["ticks"] >= 60:
        return Desk(name, share, [], c1h, O.from_start(d_at, st), p, coin["funding"])
    c15 = [c for c in coin["c15"] if st <= c.t < en]
    return Desk(name, share, c15, c1h, d_at, p, coin["funding"])


def run_b(coins: Dict[str, Dict[str, Any]], spec: Dict[str, Any], params: Dict[str, Params], oil_share: float,
          guard: Optional[CorrGuard], a_ov: Optional[Dict[str, Any]] = None):
    desks = [desk_for("oil", oil_share, coins["brent"], spec, spec["oil"], params["brent"], a_ov),
             desk_for("silver", round(1.0 - oil_share, 10), coins["silver"], spec, spec["silver"], params["silver"],
                      a_ov)]
    return simulate_portfolio(desks, START, guard)


def b_summary(prun) -> Dict[str, Any]:
    s = portfolio_stats(prun)
    s["guard_blocked"] = sum(r.refused.get("guard", 0) for r in prun.runs.values())
    s["guard_scaled"] = sum(r.guard_scaled for r in prun.runs.values())
    s["max_stop_liq_frac"] = max((max_liq_frac(r) for r in prun.runs.values()), default=0.0)
    return s


def verdict_table(rows: Dict[str, Dict[str, Dict[str, Any]]], fam: Dict[str, List[str]], names: Sequence[str],
                  thesis_rows: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None) -> Dict[str, Any]:
    scores = O.score(rows)
    th = O.score(thesis_rows) if thesis_rows else None
    max_liq = {v: max(rows[ds][v]["max_stop_liq_frac"] for ds in rows) for v in names}
    return {"scores": scores, "thesis_scores": th,
            "verdicts": {v: O.verdict(scores, v, fam, max_liq, th) for v in names if v != BASELINE}}


# ── main ───────────────────────────────────────────────────────────────────

A_FIELDS = ["coin", "dataset", "variant", "final_equity", "return_pct", "max_dd_pct", "trades", "win_rate", "avg_r",
            "total_r", "max_stop_liq_frac", "pb_trades", "pb_wins", "pb_sum_r", "pb_net_usd",
            "trend_down_trades", "trend_down_sum_r", "chop_trades", "chop_sum_r", "trend_up_trades", "trend_up_sum_r"]
B_FIELDS = ["split", "dataset", "variant", "final_equity", "return_pct", "max_dd_pct", "worst_day_pct", "trades",
            "win_rate", "corr", "oil_pnl", "silver_pnl", "guard_blocked", "guard_scaled", "max_stop_liq_frac"]


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.desk_tick_backtest import journaled_direction
    from services.strategies.evals.stop_cap_test import same_market

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal")
    ap.add_argument("--metals-theses-dir", default=os.environ.get("PATHIEL_METALS_THESES_DIR", ""),
                    help="READ ONLY: the live metals journal")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--silver-replay-dir", default="/tmp/thesis-replay-silver/theses")
    ap.add_argument("--cache-dir", default="/tmp/pullback-corr/cache")
    ap.add_argument("--out-dir", default="/tmp/pullback-corr")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} pullback-corr: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    coins = {k: O.load_coin(v, a.cache_dir) for k, v in COINS.items()}
    history = {k: {"1h_from": _iso(c["c1h"][0].t), "1h_bars": len(c["c1h"]), "15m_from": _iso(c["c15"][0].t),
                   "15m_bars": len(c["c15"]), "end": _iso(c["end"]), "funding_rows": len(c["funding"])}
               for k, c in coins.items()}
    log(f"data {json.dumps(history)}")
    params = {"brent": brent_params(), "silver": silver_params(coins["silver"]["c1h"])}
    met_j = D.load_journaled_theses(a.metals_theses_dir) if a.metals_theses_dir else []
    met_j = same_market(met_j, coins["silver"]["c1h"]) if met_j else []
    theses = {"brent": {"replay30": D.load_journaled_theses(a.oil_replay_dir),
                        "journal": D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []},
              "silver": {"replay30": D.load_journaled_theses(a.silver_replay_dir), "journal": met_j}}
    counts = {k: {n: len(v) for n, v in x.items()} for k, x in theses.items()}
    log(f"theses {json.dumps(counts)}")
    series: Dict[str, RegimeSeries] = {k: label_series(c["c1h"]) for k, c in coins.items()}

    # ── correlation
    br, sr = hourly_returns(coins["brent"]["c1h"]), hourly_returns(coins["silver"]["c1h"])
    bd, sd = daily_returns(coins["brent"]["c1h"]), daily_returns(coins["silver"]["c1h"])
    end_all = min(c["end"] for c in coins.values())
    last30 = [k for k in br if k >= end_all - 30 * DAY_MS]
    corr = {"hourly_all": corr_stats(br, sr), "hourly_30d": corr_stats(br, sr, keys=last30),
            "daily_all": corr_stats(bd, sd, big=0.03), "monthly": monthly_corr(br, sr),
            "nov2_hours": [{"t": _iso(k), "brent_pct": round(100 * br[k], 2), "silver_pct": round(100 * sr[k], 2)}
                           for k in sorted(br) if k >= _ms((2026, 11, 2)) and k in sr]}
    primary = opposite_pair(corr["hourly_all"]["corr_big"])
    corr["opposite_pair"] = primary
    log(f"correlation {json.dumps({k: v for k, v in corr.items() if k in ('hourly_all', 'hourly_30d')})} "
        f"-> opposite bets: {primary} sides")

    # ── Fix A
    named_a = a_variants()
    fam_a = a_families()
    results_a: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    for k in COINS:
        specs = O.dataset_specs(k, coins[k], theses[k])
        results_a[k] = {}
        for ds, spec in specs.items():
            results_a[k][ds] = {}
            for v, ov in named_a.items():
                run = run_a(coins[k], spec, params[k], ov)
                s = O.summarize(run, series[k], spec["start"], spec["end"])
                s["max_stop_liq_frac"] = max_liq_frac(run)
                s.update(pb_stats(run))
                results_a[k][ds][v] = s
            log(f"A {ds}: {len(named_a)} variants ({time.time() - t0:.0f}s)")
    a_out: Dict[str, Any] = {}
    for k in COINS:
        rows = results_a[k]
        th_rows = {ds: r for ds, r in rows.items() if not any(c in ds for c in O.CONTROLS)}
        a_out[k] = verdict_table(rows, fam_a, list(named_a), th_rows)
        a_out[k]["pb_pooled"] = {v: {x: sum(rows[ds][v][x] for ds in rows) for x in
                                     ("pb_trades", "pb_wins", "pb_sum_r", "pb_net_usd")} for v in named_a}
        a_out[k]["regime_delta"] = {v: O.regime_delta(rows, v) for v in named_a if v != BASELINE}

    # ── Fix A, 11-02 08:45Z to the data end, journal calls
    nov2_a: Dict[str, Dict[str, Any]] = {}
    for k in COINS:
        j = theses[k]["journal"]
        if not j:
            continue
        spec = {"ticks": 15, "start": _ms(NOV2_A_FROM), "end": coins[k]["end"], "direction": journaled_direction(j)}
        nov2_a[k] = {}
        for v, ov in named_a.items():
            run = run_a(coins[k], spec, params[k], ov)
            nov2_a[k][v] = nov2_row(run, dataclasses.replace(params[k], **O.sim_overrides(ov)))
    log(f"A 11-02 rows ({time.time() - t0:.0f}s)")

    # ── Fix B
    named_b = b_variants()
    fam_b = b_families()
    pspecs = portfolio_specs(coins, theses)
    results_b: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    for split, share in SPLITS.items():
        results_b[split] = {}
        for ds, spec in pspecs.items():
            results_b[split][ds] = {v: b_summary(run_b(coins, spec, params, share, g)) for v, g in named_b.items()}
        log(f"B {split}: {len(pspecs)} datasets x {len(named_b)} variants ({time.time() - t0:.0f}s)")
    b_out: Dict[str, Any] = {}
    for split in SPLITS:
        rows = results_b[split]
        th_rows = {ds: r for ds, r in rows.items() if ds in ("journal_shared", "replay")}
        b_out[split] = verdict_table(rows, fam_b, list(named_b), th_rows)

    # B3: the drivers each desk's theses cite, and the guard only on a shared tag
    tag_rows = {"oil_journal": load_tags(a.oil_theses_dir), "oil_replay": load_tags(a.oil_replay_dir),
                "silver_journal": load_tags(a.metals_theses_dir, {t.created_at_ms for t in met_j}),
                "silver_replay": load_tags(a.silver_replay_dir)}
    tag_counts = {k: {"theses": len(v), **dict(Counter(tag for _, tags in v for tag in tags))}
                  for k, v in tag_rows.items()}
    b3: Dict[str, Any] = {"tag_counts": tag_counts, "runs": {}}
    for ds, (ok, sk) in (("journal_shared", ("oil_journal", "silver_journal")), ("replay", ("oil_replay",
                                                                                             "silver_replay"))):
        if ds not in pspecs:
            continue
        drivers = {"oil": tags_at(tag_rows[ok]), "silver": tags_at(tag_rows[sk])}
        for pair in PAIRS:
            g = CorrGuard(pair=pair, mult=0.0, desks=("silver",), drivers=drivers)
            s = b_summary(run_b(coins, pspecs[ds], params, SPLITS[LIVE_SPLIT], g))
            b3["runs"][f"{ds}:{pair}"] = {"final_equity": s["final_equity"], "guard_blocked": s["guard_blocked"],
                                          "live_final": results_b[LIVE_SPLIT][ds][BASELINE]["final_equity"]}

    # ── Fix B, 11-02 00:00Z to the data end: the sim, and the real fills
    nov2_b: Dict[str, Any] = {"real": real_guard_rows()}
    oj, sj = theses["brent"]["journal"], theses["silver"]["journal"]
    if oj and sj:
        spec = {"ticks": 15, "start": _ms(NOV2_B_FROM), "end": end_all, "oil": journaled_direction(oj),
                "silver": journaled_direction(sj)}
        sim_rows: Dict[str, Any] = {}
        a_pick = {"none": None, "a1_8h_thesis": named_a["a1_8h_thesis"], "a4_2h": named_a["a4_2h"],
                  "a2_12h_thesis": named_a["a2_12h_thesis"], "a4_2h_fit": named_a["a4_2h_fit"],
                  "a1_8h_thesis_fit": named_a["a1_8h_thesis_fit"]}
        for an, aov in a_pick.items():
            for v in (BASELINE, b_name("same", "silver", 0.0), b_name("same", "both", 0.5),
                      b_name("opposite", "silver", 0.0), b_name("opposite", "both", 0.0),
                      b_name("opposite", "both", 0.5)):
                prun = run_b(coins, spec, params, SPLITS[LIVE_SPLIT], named_b[v], aov)
                trades = []
                for desk, r in prun.runs.items():
                    for tr in r.trades:
                        trades.append({"desk": desk, "side": tr.side, "entry": _iso(tr.entry_time),
                                       "entry_px": round(tr.entry_px, 3), "exit": tr.exit_reason,
                                       "exit_time": _iso(tr.exit_time), "net_usd": round(tr.net_pnl_usd, 2),
                                       "kind": tr.entry_kind or "live"})
                    op = r.open_position_at_end
                    if op:
                        trades.append({"desk": desk, "side": op["side"], "entry": "open",
                                       "entry_px": round(op["entry_px"], 3),
                                       "unrealized_usd": round(op["unrealized_pnl_usd"], 2)})
                sim_rows[f"{an}+{v}"] = {"final_equity": round(prun.final_equity, 2),
                                         "pnl_at_end_usd": round(sum(nov2_row(r, r.params or Params())["pnl_at_end_usd"]
                                                                     for r in prun.runs.values()), 2),
                                         "trades": sorted(trades, key=lambda x: str(x["entry"])),
                                         "guard": [{**g, "time": _iso(g["time"])} for g in prun.guard_log
                                                   if g["mult"] != 1.0][:6]}
        nov2_b["sim"] = sim_rows
    log(f"scored ({time.time() - t0:.0f}s)")

    # ── write
    with open(os.path.join(a.out_dir, "a_variants.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=A_FIELDS)
        w.writeheader()
        for k, by_ds in results_a.items():
            for ds, by_v in by_ds.items():
                for v, s in by_v.items():
                    row = {"coin": k, "dataset": ds, "variant": v, **{x: s.get(x) for x in A_FIELDS[3:15]}}
                    for lab in ("trend_down", "chop", "trend_up"):
                        br_ = s["by_regime"].get(lab, {})
                        row[f"{lab}_trades"] = br_.get("trades", 0)
                        row[f"{lab}_sum_r"] = br_.get("sum_r", 0.0)
                    w.writerow(row)
    with open(os.path.join(a.out_dir, "b_variants.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=B_FIELDS)
        w.writeheader()
        for split, by_ds in results_b.items():
            for ds, by_v in by_ds.items():
                for v, s in by_v.items():
                    w.writerow({"split": split, "dataset": ds, "variant": v,
                                **{x: s.get(x) for x in B_FIELDS[3:11]},
                                "oil_pnl": s["desk_pnl"].get("oil"), "silver_pnl": s["desk_pnl"].get("silver"),
                                "guard_blocked": s["guard_blocked"], "guard_scaled": s["guard_scaled"],
                                "max_stop_liq_frac": s["max_stop_liq_frac"]})
    with open(os.path.join(a.out_dir, "correlation.csv"), "w", newline="") as fh:
        fields = ["window", "n", "corr", "n_big", "corr_big", "opposite_share_big", "silver_mean_on_brent_down_pct",
                  "silver_mean_on_brent_up_pct", "n_big_down", "n_big_up"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for name in ("hourly_all", "hourly_30d", "daily_all"):
            w.writerow({"window": name, **corr[name]})
        for m in corr["monthly"]:
            w.writerow({"window": m["month"], **{x: m[x] for x in fields[1:]}})
    with open(os.path.join(a.out_dir, "nov2.json"), "w") as fh:
        json.dump({"a": nov2_a, "b": nov2_b}, fh, indent=1, default=str)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "history": history, "theses": counts, "correlation": corr,
        "a": {k: {**v, "results": results_a[k]} for k, v in a_out.items()},
        "b": {s: {**v, "results": results_b[s]} for s, v in b_out.items()}, "b3": b3,
        "nov2": {"a": nov2_a, "b": nov2_b},
        "datasets_b": {ds: {"start": _iso(sp["start"]), "end": _iso(sp["end"]), "ticks": sp["ticks"]}
                       for ds, sp in pspecs.items()},
        "adopt_a": {k: sorted(v for v, x in a_out[k]["verdicts"].items() if x["adopt"]) for k in a_out},
        "adopt_b": {s: sorted(v for v, x in b_out[s]["verdicts"].items() if x["adopt"]) for s in b_out},
    }
    with open(os.path.join(a.out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(json.dumps(rep["history"], indent=1))
    print("theses", json.dumps(rep["theses"]))
    c = rep["correlation"]
    for k in ("hourly_all", "hourly_30d", "daily_all"):
        print(f"corr {k}: {json.dumps(c[k])}")
    print("opposite bets on:", c["opposite_pair"], "sides")
    for k, x in rep["a"].items():
        print(f"\n== A [{k}] verdicts (beats/loses, geo, dd, theses, neighbours, fails)")
        for v, y in sorted(x["verdicts"].items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            pb = x["pb_pooled"][v]
            print(f"  {v:<24} {y['beats']}/{y['loses']} of {y['of']} flat {y['flat_beats']} geo {y['geo_mean_ratio']:.3f} "
                  f"dd {y['mean_dd']} (live {y['live_mean_dd']}) th {y['theses']} nb {y['neighbours_good']} "
                  f"pb n {pb['pb_trades']} R {pb['pb_sum_r']:+.1f} adopt {y['adopt']}")
    for s, x in rep["b"].items():
        print(f"\n== B [{s}] verdicts")
        for v, y in sorted(x["verdicts"].items(), key=lambda kv: -kv[1]["geo_mean_ratio"]):
            print(f"  {v:<24} {y['beats']}/{y['loses']} of {y['of']} flat {y['flat_beats']} geo {y['geo_mean_ratio']:.3f} "
                  f"dd {y['mean_dd']} (live {y['live_mean_dd']}) th {y['theses']} nb {y['neighbours_good']} "
                  f"adopt {y['adopt']}")
    print("\nB3", json.dumps(rep["b3"]))
    print("\n== 11-02 A")
    for k, by_v in rep["nov2"]["a"].items():
        for v, row in by_v.items():
            keep = ("entry", "side", "kind", "entry_px", "stop_px", "target_px", "exit", "pnl_at_end_usd",
                    "at_stop_usd", "at_target_usd")
            print(f"  {k} {v:<24} {json.dumps({x: row.get(x) for x in keep})}")
    print("\n== 11-02 B real fills")
    for v, row in rep["nov2"]["b"]["real"].items():
        print(f"  {v:<24} {json.dumps(row)}")
    print("\n== 11-02 B sim")
    for v, row in (rep["nov2"]["b"].get("sim") or {}).items():
        print(f"  {v:<34} final {row['final_equity']} pnl {row['pnl_at_end_usd']} trades "
              f"{[(t['desk'], t['side'], t['entry'], t.get('net_usd', t.get('unrealized_usd'))) for t in row['trades']]}")
    print("\nadopt A", rep["adopt_a"], "adopt B", rep["adopt_b"])


if __name__ == "__main__":
    raise SystemExit(main())
