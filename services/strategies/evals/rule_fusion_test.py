"""Rule fusion sweep, 2026-11-13: every combination of the oil desk's OFF /
optional rules, on the journal replay, with overfitting guards.

Operator: "can we get a good fusion of the rules?? and combine the ROI and
SUM? how would the best of the best work together?"

Each rule tested so far was scored alone against the live baseline
(`trend_gate_test.base_params`: market entry off the 24h range, 0.75 stop,
0.5% offset target, 2R, 20x, no entry after a break, weekend flat, 6% risk).
This sweep turns every optional `desk_tick_sim.Params` switch that already
exists and was tested into an AXIS with an off level, its tested value and
one neighbour, and runs the full factorial of the axes on the live oil
journal (71 theses), the way `winner_traits_test` scored the three
winner-trait candidates:

  delay      entry_delay_min            off / 90 / 120 / 180      (TRADE_ATTRIBUTION_2026-11-13)
  trend      trend_gate_span            off / 120 / 144           (TREND_GATE_TEST_2026-11-13)
  reclaim    reclaim_after_stops=2      off / 24h / 12h window    (STOP_RECLAIM_TEST_2026-11-10)
  cap        daily_stop_cap             off / 2 / 1               (STOP_CAP_TEST_2026-11-02)
  edge       entry_mode="edge"          off / 0.25 / 0.15 ATR     (TRADE_ATTRIBUTION_2026-11-13)
  one_entry  max_stop_reentries +       off / 1 / 2               (FINAL_STRATEGY_TEST_2026-11-04,
             reentry_needs_1h_reclaim                               "entries_reclaim")
  lock       lock_trigger_r -> entry    off / 1R / 2R             (PROFIT_LOCK_TEST_2026-11-01)
  half       partial_frac 0.5           off / 3R / 4R             (FINAL_STRATEGY_TEST_2026-11-04)

The zone gate (`entry_zone_frac`) is left out: it changes no trade on any
dataset (the 2R gate already implies it). The funding filter is a no-op on
the journal. The leverage fit (20/15) is live and is reported as a
diagnostic, not an axis.

Per combo, on the journal: trades, wins, net $, ROI % on the starting
equity, max DD, the last third (its own run from the start equity), and
WTI (xyz:CL) with the journal's sides. Then the guards, which are the
point:

  old bar    net beats the baseline; max DD no worse; every neighbour
             (one axis moved to its adjacent ON level) still beats the
             baseline; last third not negative and not below the
             baseline's; WTI not negative.
  a. drop-best-day   the net delta over the baseline with its best exit
             day removed stays positive, and the review's FLUKE rule in R
             (`winner_traits_test.one_day_flips`) does not fire.
  b. weeks   the weekly $ delta over the baseline (7-day buckets from the
             window start, by exit) is positive in more weeks than it is
             negative, and leaving any one week out keeps the total
             positive.
  c. plateau the neighbour check above.
  d. complexity  every rule in the combo adds value on its own: the
             marginal gain from adding it to the same combo without it is
             positive. A combo carrying a rule that adds nothing is not
             minimal.
  e. multiple testing  the report says how many combos were tried, how
             many beat the baseline on net alone, and how many survive
             each guard, so the top result is read with the suspicion it
             deserves.

"Best of the best" is the SMALLEST combo (fewest rules on) that passes
all of it, ties broken by net. The overlap table says, for every pair of
rules at their tested value, how many baseline trades both remove and
whether the pair adds up (net of the pair above both singles), overlaps
(blocks the same trades, interaction negative) or cancels (the pair under
both singles).

    PATHIEL_STATE_DIR=<pathia>/.state python3 -m services.strategies.evals.rule_fusion_test \\
        --oil-theses-dir <pathia>/.state/strategies/oil/theses \\
        --cache-dir <fresh dir> --out-dir <dir> --equity 213.80

Reads candles through the eval disk cache and the venue's public /info;
never touches the live account, the live config or the journal. Exit 0
when a fusion passes everything, 1 when none does, 2 when the journal is
missing.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import itertools
import json
import math
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Run, simulate
from services.strategies.evals.desk_tick_backtest import DAY_MS, HOUR_MS, journaled_direction
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, _iso, closed_only
from services.strategies.evals.stop_reclaim_test import thirds
from services.strategies.evals.trend_gate_test import base_params, side_only
from services.strategies.evals.winner_traits_test import one_day_flips, summarize

START = 182.09                # the tournament's and trend-gate test's start equity, so the baselines compare
RISK_CAP_FRAC = 0.06
EQUITY_NOW = 213.80           # account_overview, 2026-11-13 (read only); --equity overrides
BRENT = "xyz:BRENTOIL"
WTI = "xyz:CL"
WEEK_MS = 7 * DAY_MS
TOP_N = 10
EPS = 1e-9

# axis -> {"levels": {level: Params overrides} (first = off, then the tested
# value, then its neighbour(s), in adjacency order), "primary": level}
AXES: Dict[str, Dict[str, Any]] = {
    "delay": {"levels": {"off": {}, "90": {"entry_delay_min": 90.0}, "120": {"entry_delay_min": 120.0},
                         "180": {"entry_delay_min": 180.0}}, "primary": "120",
              "doc": "no entry in a thesis's first N minutes (TRADE_ATTRIBUTION_2026-11-13)"},
    "trend": {"levels": {"off": {}, "120": {"trend_gate_span": 120}, "144": {"trend_gate_span": 144}},
              "primary": "120", "doc": "no long while the N-hour return is negative (TREND_GATE_TEST_2026-11-13)"},
    "reclaim": {"levels": {"off": {}, "24h": {"reclaim_after_stops": 2, "reclaim_window_h": 24.0},
                           "12h": {"reclaim_after_stops": 2, "reclaim_window_h": 12.0}}, "primary": "24h",
                "doc": "after 2 same-side stops in the window, wait for a 1h reclaim (STOP_RECLAIM_TEST_2026-11-10)"},
    "cap": {"levels": {"off": {}, "2": {"daily_stop_cap": 2}, "1": {"daily_stop_cap": 1}}, "primary": "2",
            "doc": "no new entry after N stops in a UTC day (STOP_CAP_TEST_2026-11-02)"},
    "edge": {"levels": {"off": {}, "0.25": {"entry_mode": "edge", "atr_rest_frac": 0.25},
                        "0.15": {"entry_mode": "edge", "atr_rest_frac": 0.15}}, "primary": "0.25",
             "doc": "rest a limit at the 24h edge within N ATR instead of taking the mid (TRADE_ATTRIBUTION_2026-11-13)"},
    "one_entry": {"levels": {"off": {}, "1": {"max_stop_reentries": 1, "reentry_needs_1h_reclaim": True},
                             "2": {"max_stop_reentries": 2, "reentry_needs_1h_reclaim": True}}, "primary": "1",
                  "doc": "N re-entries per thesis after a stop, each after a 1h close back inside the range "
                         "(FINAL_STRATEGY_TEST_2026-11-04 entries_reclaim)"},
    "lock": {"levels": {"off": {}, "1R": {"lock_trigger_r": 1.0, "lock_r": 0.0},
                        "2R": {"lock_trigger_r": 2.0, "lock_r": 0.0}}, "primary": "1R",
             "doc": "stop to entry once the trade is N R in favour (PROFIT_LOCK_TEST_2026-11-01)"},
    "half": {"levels": {"off": {}, "3R": {"partial_frac": 0.5, "partial_r": 3.0},
                        "4R": {"partial_frac": 0.5, "partial_r": 4.0}}, "primary": "3R",
             "doc": "take half off at N R as a resting limit (FINAL_STRATEGY_TEST_2026-11-04 half-off)"},
}
LEV_FIT_DIAGNOSTIC = {"entry_fit_leverage": (20.0, 15.0)}   # live since 11-04; the sim baseline omits it

Combo = Tuple[str, ...]      # one level name per axis, in AXES order


# ── the grid (pure) ────────────────────────────────────────────────────────

def axis_names(axes: Dict[str, Dict[str, Any]] = AXES) -> List[str]:
    return list(axes)


def combos(axes: Dict[str, Dict[str, Any]] = AXES) -> List[Combo]:
    """Every combination of levels, the all-off baseline first."""
    return [tuple(c) for c in itertools.product(*(list(a["levels"]) for a in axes.values()))]


def baseline_combo(axes: Dict[str, Dict[str, Any]] = AXES) -> Combo:
    return tuple(next(iter(a["levels"])) for a in axes.values())


def is_off(level: str, axis: Dict[str, Any]) -> bool:
    return level == next(iter(axis["levels"]))


def rules_on(combo: Combo, axes: Dict[str, Dict[str, Any]] = AXES) -> Dict[str, str]:
    """axis -> level for the axes that are not off."""
    return {n: lv for n, lv, a in zip(axes, combo, axes.values()) if not is_off(lv, a)}


def n_rules(combo: Combo, axes: Dict[str, Dict[str, Any]] = AXES) -> int:
    return len(rules_on(combo, axes))


def combo_name(combo: Combo, axes: Dict[str, Dict[str, Any]] = AXES) -> str:
    on = rules_on(combo, axes)
    return "+".join(f"{n}{lv}" for n, lv in on.items()) if on else "baseline"


def overrides(combo: Combo, axes: Dict[str, Dict[str, Any]] = AXES) -> Dict[str, Any]:
    """The merged Params overrides. Two axes never set the same field."""
    out: Dict[str, Any] = {}
    for n, lv in zip(axes, combo):
        for k, v in axes[n]["levels"][lv].items():
            if k in out and out[k] != v:
                raise ValueError(f"axes collide on Params.{k}: {combo}")
            out[k] = v
    return out


def without(combo: Combo, axis: str, axes: Dict[str, Dict[str, Any]] = AXES) -> Combo:
    """The same combo with one axis turned off."""
    names = axis_names(axes)
    i = names.index(axis)
    return combo[:i] + (next(iter(axes[axis]["levels"])),) + combo[i + 1:]


def single(axis: str, level: Optional[str] = None, axes: Dict[str, Dict[str, Any]] = AXES) -> Combo:
    """One axis at `level` (default: its primary), everything else off."""
    base = list(baseline_combo(axes))
    base[axis_names(axes).index(axis)] = level or axes[axis]["primary"]
    return tuple(base)


def neighbours(combo: Combo, axes: Dict[str, Dict[str, Any]] = AXES) -> Dict[str, Combo]:
    """For each ON axis, the combos with that axis at its adjacent ON
    level(s) (adjacent in the axis's level order, the off level excluded).
    Key: "axis=level"."""
    names = axis_names(axes)
    out: Dict[str, Combo] = {}
    for n, lv in rules_on(combo, axes).items():
        on_levels = list(axes[n]["levels"])[1:]
        i = on_levels.index(lv)
        for j in (i - 1, i + 1):
            if 0 <= j < len(on_levels):
                c = list(combo)
                c[names.index(n)] = on_levels[j]
                out[f"{n}={on_levels[j]}"] = tuple(c)
    return out


# ── summaries (pure) ───────────────────────────────────────────────────────

def trade_rows(run: Run) -> List[Dict[str, Any]]:
    return [{"side": t.side, "entry_ms": t.entry_time, "exit_ms": t.exit_time, "entry": _iso(t.entry_time),
             "exit": _iso(t.exit_time), "entry_px": round(t.entry_px, 3), "exit_px": round(t.exit_px, 3)
             if t.exit_px is not None else None, "reason": t.exit_reason, "net_usd": round(t.net_pnl_usd, 2),
             "r": round(t.r_multiple, 2) if t.r_multiple is not None else None} for t in run.trades]


def delta_by_bucket(base_trades: Sequence[Dict[str, Any]], cand_trades: Sequence[Dict[str, Any]],
                    key: Callable[[Dict[str, Any]], Any]) -> Dict[Any, Dict[str, float]]:
    """Per bucket (`key` of a trade row): the candidate's R and net $ minus
    the baseline's. Buckets where nothing differs are dropped."""
    out: Dict[Any, Dict[str, float]] = {}
    for rows, sign in ((base_trades, -1.0), (cand_trades, 1.0)):
        for t in rows:
            row = out.setdefault(key(t), {"r": 0.0, "net_usd": 0.0})
            row["r"] += sign * (t["r"] or 0.0)
            row["net_usd"] += sign * (t["net_usd"] or 0.0)
    return {k: {"r": round(v["r"], 2), "net_usd": round(v["net_usd"], 2)} for k, v in sorted(out.items())
            if abs(v["r"]) > EPS or abs(v["net_usd"]) > EPS}


def _day(t: Dict[str, Any]) -> str:
    return (t["exit"] or t["entry"] or "")[:10]


def week_of(t_ms: Optional[int], start_ms: int) -> int:
    return int(((t_ms if t_ms is not None else start_ms) - start_ms) // WEEK_MS)


def drop_best_day(base_trades: Sequence[Dict[str, Any]], cand_trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Guard a. The $ delta over the baseline by exit day; the best day
    removed; the review's R fluke rule."""
    d = delta_by_bucket(base_trades, cand_trades, _day)
    total = round(sum(v["net_usd"] for v in d.values()), 2)
    if not d:
        return {"delta_usd": 0.0, "best_day": None, "best_day_usd": 0.0, "rest_usd": 0.0,
                "fluke_r": one_day_flips({}), "pass": False, "by_day": {}}
    best = max(d, key=lambda k: d[k]["net_usd"])
    rest = round(total - d[best]["net_usd"], 2)
    fluke = one_day_flips(d)
    return {"delta_usd": total, "best_day": best, "best_day_usd": d[best]["net_usd"], "rest_usd": rest,
            "fluke_r": fluke, "pass": rest > EPS and not fluke["fluke"], "by_day": d}


def weeks(base_trades: Sequence[Dict[str, Any]], cand_trades: Sequence[Dict[str, Any]], start_ms: int,
          end_ms: int) -> Dict[str, Any]:
    """Guard b. The $ delta over the baseline per 7-day week of the window
    (by exit); won = weeks with a positive delta, lost = negative. Pass:
    won > lost, and leaving any one week out keeps the total positive."""
    n_weeks = max(1, math.ceil((end_ms - start_ms) / WEEK_MS))
    d = delta_by_bucket(base_trades, cand_trades, lambda t: week_of(t["exit_ms"], start_ms))
    per = {w: d.get(w, {"r": 0.0, "net_usd": 0.0})["net_usd"] for w in range(n_weeks)}
    total = round(sum(per.values()), 2)
    won = sum(1 for v in per.values() if v > EPS)
    lost = sum(1 for v in per.values() if v < -EPS)
    loo = {w: round(total - v, 2) for w, v in per.items()}
    loo_min = min(loo.values()) if loo else 0.0
    return {"n_weeks": n_weeks, "per_week_usd": {str(w): round(v, 2) for w, v in per.items()}, "won": won,
            "lost": lost, "flat": n_weeks - won - lost, "most_weeks": won > n_weeks / 2,
            "leave_one_out_min_usd": loo_min, "pass": won > lost and loo_min > EPS}


def plateau(combo: Combo, nets: Dict[Combo, float], base_net: float,
            axes: Dict[str, Dict[str, Any]] = AXES) -> Dict[str, Any]:
    """Guard c / the old bar's neighbour leg: every neighbour beats the baseline."""
    nb = {k: nets.get(c) for k, c in neighbours(combo, axes).items()}
    ok = {k: (v is not None and v > base_net + EPS) for k, v in nb.items()}
    return {"neighbours_usd": nb, "ok": ok, "pass": bool(ok) and all(ok.values())}


def marginal(combo: Combo, nets: Dict[Combo, float], axes: Dict[str, Dict[str, Any]] = AXES) -> Dict[str, Any]:
    """Guard d. For each rule in the combo, net(combo) - net(combo without
    it). Pass: every rule adds value (> 0)."""
    net = nets[combo]
    gains = {n: round(net - nets[without(combo, n, axes)], 2) for n in rules_on(combo, axes)}
    return {"gains_usd": gains, "pass": bool(gains) and all(g > EPS for g in gains.values())}


def old_bar(s: Dict[str, Any], base: Dict[str, Any], plat: Dict[str, Any], last: float, base_last: float,
            wti_net: Optional[float]) -> Dict[str, Any]:
    net_ok = s["net_usd"] > base["net_usd"] + EPS
    dd_ok = s["max_dd_pct"] <= base["max_dd_pct"] + EPS
    last_ok = last >= -EPS and last >= base_last - EPS
    wti_ok = wti_net is not None and wti_net >= -EPS
    return {"net": net_ok, "max_dd": dd_ok, "plateau": plat["pass"], "last_third": last_ok, "wti": wti_ok,
            "pass": net_ok and dd_ok and plat["pass"] and last_ok and wti_ok}


def roi_pct(net_usd: float, start: float) -> float:
    return round(net_usd / start * 100.0, 1) if start else 0.0


def judge(results: Dict[Combo, Dict[str, Any]], start_ms: int, end_ms: int, start_eq: float,
          axes: Dict[str, Dict[str, Any]] = AXES) -> Dict[Combo, Dict[str, Any]]:
    """Every guard on every combo. `results[combo]` holds "brent" (summary),
    "brent_trades", "last_third" (summary), "wti" (summary or None). Pure."""
    b0 = baseline_combo(axes)
    base, base_trades = results[b0]["brent"], results[b0]["brent_trades"]
    base_last = results[b0]["last_third"]["net_usd"]
    nets = {c: r["brent"]["net_usd"] for c, r in results.items()}
    out: Dict[Combo, Dict[str, Any]] = {}
    for c, r in results.items():
        s = r["brent"]
        plat = plateau(c, nets, base["net_usd"], axes)
        wti_net = r["wti"]["net_usd"] if r.get("wti") else None
        row = {"name": combo_name(c, axes), "rules": rules_on(c, axes), "n_rules": n_rules(c, axes),
               "trades": s["trades"], "wins": s["wins"], "net_usd": s["net_usd"],
               "roi_pct": roi_pct(s["net_usd"], start_eq), "delta_usd": round(s["net_usd"] - base["net_usd"], 2),
               "max_dd_pct": s["max_dd_pct"], "last_third_usd": r["last_third"]["net_usd"],
               "wti_net_usd": wti_net, "wti_trades": r["wti"]["trades"] if r.get("wti") else None,
               "refused": s.get("refused", {})}
        if c == b0:
            row.update({"old_bar": None, "drop_best_day": None, "weeks": None, "plateau": None, "marginal": None,
                        "pass": False})
        else:
            a = drop_best_day(base_trades, r["brent_trades"])
            b = weeks(base_trades, r["brent_trades"], start_ms, end_ms)
            d = marginal(c, nets, axes)
            ob = old_bar(s, base, plat, r["last_third"]["net_usd"], base_last, wti_net)
            row.update({"old_bar": ob, "drop_best_day": a, "weeks": b, "plateau": plat, "marginal": d,
                        "pass": ob["pass"] and a["pass"] and b["pass"] and plat["pass"] and d["pass"]})
        out[c] = row
    return out


def best_of_best(judged: Dict[Combo, Dict[str, Any]]) -> Optional[Combo]:
    """The smallest passing combo, ties broken by net."""
    passing = [c for c, j in judged.items() if j["pass"]]
    if not passing:
        return None
    return min(passing, key=lambda c: (judged[c]["n_rules"], -judged[c]["net_usd"]))


def funnel(judged: Dict[Combo, Dict[str, Any]]) -> Dict[str, int]:
    """Guard e. How many combos survive each leg."""
    rows = [j for j in judged.values() if j["old_bar"] is not None]
    return {"combos_tried": len(rows),
            "beat_baseline_net": sum(1 for j in rows if j["old_bar"]["net"]),
            "old_bar": sum(1 for j in rows if j["old_bar"]["pass"]),
            "drop_best_day": sum(1 for j in rows if j["drop_best_day"]["pass"]),
            "weeks": sum(1 for j in rows if j["weeks"]["pass"]),
            "plateau": sum(1 for j in rows if j["plateau"]["pass"]),
            "marginal": sum(1 for j in rows if j["marginal"]["pass"]),
            "all": sum(1 for j in rows if j["pass"])}


def overlap_table(results: Dict[Combo, Dict[str, Any]], axes: Dict[str, Dict[str, Any]] = AXES
                  ) -> List[Dict[str, Any]]:
    """Every pair of rules at their primary level: the baseline trades each
    removes (by entry time), how many both remove, the pair's net against
    the two singles, and a label: "adds up" (pair above both singles and
    the interaction not negative), "overlap" (shared removals, interaction
    negative), "cancel" (pair under both singles), else "mixed"."""
    b0 = baseline_combo(axes)
    base_net = results[b0]["brent"]["net_usd"]
    base_entries = {t["entry_ms"] for t in results[b0]["brent_trades"]}
    names = axis_names(axes)

    def removed(c: Combo) -> set:
        return base_entries - {t["entry_ms"] for t in results[c]["brent_trades"]}
    rows: List[Dict[str, Any]] = []
    for a, b in itertools.combinations(names, 2):
        ca, cb = single(a, None, axes), single(b, None, axes)
        pair = list(b0)
        pair[names.index(a)] = axes[a]["primary"]
        pair[names.index(b)] = axes[b]["primary"]
        cab = tuple(pair)
        if ca not in results or cb not in results or cab not in results:
            continue
        na, nb, nab = (results[c]["brent"]["net_usd"] for c in (ca, cb, cab))
        shared = len(removed(ca) & removed(cb))
        inter = round(nab - na - nb + base_net, 2)
        rows.append({"a": f"{a}{axes[a]['primary']}", "b": f"{b}{axes[b]['primary']}", "net_a": na, "net_b": nb,
                     "net_pair": nab, "removed_a": len(removed(ca)), "removed_b": len(removed(cb)),
                     "shared_removed": shared, "interaction_usd": inter,
                     "label": overlap_label(na, nb, nab, shared, inter)})
    return rows


def overlap_label(net_a: float, net_b: float, net_pair: float, shared: int, interaction: float) -> str:
    if net_pair > max(net_a, net_b) + EPS and interaction >= -EPS:
        return "adds up"
    if net_pair < min(net_a, net_b) - EPS:
        return "cancel"
    if shared > 0 and interaction < -EPS:
        return "overlap"
    return "mixed"


# ── runs ───────────────────────────────────────────────────────────────────

_CTX: Dict[str, Tuple[Any, ...]] = {}     # dataset -> (c15, c1h, direction, base params, funding); set before the pool forks


def _window(c15, c1h, start_ms, end_ms):
    return [c for c in c15 if start_ms <= c.t < end_ms], [c for c in c1h if c.t < end_ms]


def _run_one(ds: str, ov: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    c15, c1h, direction, base, fund = _CTX[ds]
    run = simulate(c15, c1h, direction, dataclasses.replace(base, **ov), fund)
    return summarize(run, base.starting_equity), trade_rows(run)


def _job(args: Tuple[int, Dict[str, Any], Sequence[str]]) -> Tuple[int, Dict[str, Any]]:
    i, ov, datasets = args
    out: Dict[str, Any] = {}
    for ds in datasets:
        try:
            s, rows = _run_one(ds, ov)
        except ValueError as e:                     # an invalid Params combination
            s, rows = {"error": str(e), "trades": 0, "wins": 0, "net_usd": float("nan"), "max_dd_pct": float("nan")}, []
        out[ds] = s
        if ds == "brent":
            out["brent_trades"] = rows
    return i, out


def sweep(all_combos: Sequence[Combo], datasets: Sequence[str], jobs: int, log: Callable[[str], None],
          axes: Dict[str, Dict[str, Any]] = AXES) -> Dict[Combo, Dict[str, Any]]:
    tasks = [(i, overrides(c, axes), datasets) for i, c in enumerate(all_combos)]
    results: Dict[Combo, Dict[str, Any]] = {}
    t0 = time.time()
    done = 0

    def take(i: int, out: Dict[str, Any]) -> None:
        nonlocal done
        results[all_combos[i]] = out
        done += 1
        if done % 500 == 0 or done == len(tasks):
            log(f"{done}/{len(tasks)} combos ({time.time() - t0:.0f}s)")
    if jobs > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(jobs) as pool:
            for i, out in pool.imap_unordered(_job, tasks, chunksize=16):
                take(i, out)
    else:
        for t in tasks:
            take(*_job(t))
    return results


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import load_coin_candles

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=os.environ.get("PATHIEL_OIL_THESES_DIR", ""),
                    help="READ ONLY: the live oil journal's theses")
    ap.add_argument("--cache-dir", default="/tmp/rule-fusion/cache", help="15m/1h candles (fetched once, then reused)")
    ap.add_argument("--out-dir", default="/tmp/rule-fusion")
    ap.add_argument("--start-equity", type=float, default=START)
    ap.add_argument("--risk-cap-frac", type=float, default=RISK_CAP_FRAC)
    ap.add_argument("--equity", type=float, default=EQUITY_NOW, help="today's equity, for the $ and $/30d columns")
    ap.add_argument("--axes", nargs="*", default=None, help="a subset of axes (default: all)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--no-wti", action="store_true")
    ap.add_argument("--lev-fit", action="store_true",
                    help="put the live 20/15 leverage fit (entry_fit_leverage) in the baseline and every combo")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    axes = {k: AXES[k] for k in (a.axes or list(AXES))}

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} rule-fusion: {msg}"
        print(line, flush=True)
        with open(os.path.join(a.out_dir, "progress.log"), "a") as fh:
            fh.write(line + "\n")

    def load(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        c15, c1h = closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], HOUR_MS, asof)
        end = c15[-1].t + FIFTEEN_MS
        return c15, c1h, end, D.fetch_funding_cached(coin, c15[0].t, end, a.cache_dir)

    theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if not theses:
        log(f"no theses at {a.oil_theses_dir!r}: nothing to judge")
        return 2
    b15, b1h, b_end, b_fund = load(BRENT)
    bp = base_params(a.start_equity, a.risk_cap_frac)
    if a.lev_fit:
        bp = dataclasses.replace(bp, **LEV_FIT_DIAGNOSTIC)
    start, end = min(th.created_at_ms for th in theses), b_end
    direction = journaled_direction(theses)
    w15, w1h = _window(b15, b1h, start, end)
    _CTX["brent"] = (w15, w1h, direction, bp, b_fund)
    l_start, l_end = thirds(start, end)[-1]
    t15, t1h = _window(b15, b1h, l_start, l_end)
    _CTX["last_third"] = (t15, t1h, direction, bp, b_fund)
    datasets = ["brent", "last_third"]
    if not a.no_wti:
        c15, c1h, w_end, w_fund = load(WTI)
        x15, x1h = _window(c15, c1h, start, min(end, w_end))
        _CTX["wti"] = (x15, x1h, side_only(direction), bp, w_fund)
        datasets.append("wti")
    all_combos = combos(axes)
    log(f"{len(theses)} theses {_iso(start)} -> {_iso(end)}; Brent candles to {_iso(b_end)}; "
        f"{len(all_combos)} combos x {datasets}; {a.jobs} jobs")
    results = sweep(all_combos, datasets, a.jobs, log, axes)
    for c, r in results.items():
        r.setdefault("wti", None)
    judged = judge(results, start, end, a.start_equity, axes)
    best = best_of_best(judged)
    fun = funnel(judged)
    overlap = overlap_table(results, axes)
    b0 = baseline_combo(axes)
    ranked = sorted((c for c in judged if c != b0), key=lambda c: -judged[c]["net_usd"])
    singles = [single(n, None, axes) for n in axes]
    report_rows = [b0] + singles + ranked[:TOP_N] + ([best] if best else [])
    seen: List[Combo] = []
    for c in report_rows:
        if c not in seen:
            seen.append(c)
    # the same rows at today's equity (the $ and $/30d columns)
    days = (end - start) / DAY_MS
    _CTX["now"] = (w15, w1h, direction, dataclasses.replace(bp, starting_equity=a.equity), b_fund)
    at_now: Dict[str, Dict[str, Any]] = {}
    for c in seen:
        s, _ = _run_one("now", overrides(c, axes))
        at_now[combo_name(c, axes)] = {"net_usd": s["net_usd"], "per_30d_usd": round(s["net_usd"] / days * 30.0, 2),
                                       "roi_pct": roi_pct(s["net_usd"], a.equity), "max_dd_pct": s["max_dd_pct"],
                                       "trades": s["trades"], "wins": s["wins"]}
    lev_fit, _ = _run_one("brent", LEV_FIT_DIAGNOSTIC)
    # the same rows on top of the live leverage fit (the sim baseline omits it)
    with_fit: Dict[str, float] = {}
    for c in seen:
        s, _ = _run_one("brent", {**overrides(c, axes), **LEV_FIT_DIAGNOSTIC})
        with_fit[combo_name(c, axes)] = s["net_usd"]
    log(f"judged ({time.time() - t0:.0f}s): {fun}")

    csv_path = os.path.join(a.out_dir, "combos.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "n_rules", "trades", "wins", "net_usd", "roi_pct", "delta_usd", "max_dd_pct",
                    "last_third_usd", "wti_net_usd", "old_bar", "drop_best_day_rest_usd", "drop_best_day",
                    "weeks_won", "weeks_lost", "weeks_loo_min_usd", "weeks", "plateau", "marginal", "pass"])
        for c in [b0] + ranked:
            j = judged[c]
            ob, a_, wk = j["old_bar"], j["drop_best_day"], j["weeks"]
            w.writerow([j["name"], j["n_rules"], j["trades"], j["wins"], j["net_usd"], j["roi_pct"], j["delta_usd"],
                        j["max_dd_pct"], j["last_third_usd"], j["wti_net_usd"],
                        ob["pass"] if ob else "", a_["rest_usd"] if a_ else "", a_["pass"] if a_ else "",
                        wk["won"] if wk else "", wk["lost"] if wk else "", wk["leave_one_out_min_usd"] if wk else "",
                        wk["pass"] if wk else "", j["plateau"]["pass"] if j["plateau"] else "",
                        j["marginal"]["pass"] if j["marginal"] else "", j["pass"]])
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "window": [_iso(start), _iso(end)], "days": round(days, 1),
        "n_theses": len(theses), "start_equity": a.start_equity, "risk_cap_frac": a.risk_cap_frac,
        "lev_fit_in_baseline": bool(a.lev_fit), "equity_now": a.equity, "axes": {n: {"levels": list(ax["levels"]), "primary": ax["primary"], "doc": ax["doc"]}
                                         for n, ax in axes.items()},
        "funnel": fun, "best": combo_name(best, axes) if best else None,
        "verdict": {"pass": best is not None, "best": judged[best] if best else None},
        "baseline": judged[b0], "singles": {combo_name(c, axes): judged[c] for c in singles},
        "top": [judged[c] for c in ranked[:TOP_N]], "passing": [judged[c]["name"] for c in ranked if judged[c]["pass"]],
        "at_equity_now": at_now, "overlap": overlap,
        "lev_fit_diagnostic": {"baseline_net_usd": judged[b0]["net_usd"], "lev_fit_net_usd": lev_fit["net_usd"],
                               "identical": abs(lev_fit["net_usd"] - judged[b0]["net_usd"]) < 0.005
                               and lev_fit["trades"] == judged[b0]["trades"], "rows_with_fit_net_usd": with_fit},
        "journal_trades": {combo_name(c, axes): results[c]["brent_trades"] for c in seen},
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    _print(report)
    print(f"\nreport: {out_path}\ncombos: {csv_path}")
    return 0 if best else 1


def _fmt_row(j: Dict[str, Any], now: Optional[Dict[str, Any]] = None) -> str:
    ob, a_, wk = j.get("old_bar"), j.get("drop_best_day"), j.get("weeks")
    flags = "" if ob is None else (
        f"bar {'Y' if ob['pass'] else 'n'} day {'Y' if a_['pass'] else 'n'}({a_['rest_usd']:+.2f}) "
        f"wk {wk['won']}/{wk['lost']}/{wk['flat']} {'Y' if wk['pass'] else 'n'} plat {'Y' if j['plateau']['pass'] else 'n'} "
        f"marg {'Y' if j['marginal']['pass'] else 'n'} -> {'PASS' if j['pass'] else 'fail'}")
    now_s = f" | now {now['net_usd']:+.2f}$ {now['per_30d_usd']:+.2f}$/30d" if now else ""
    wti = f"{j['wti_net_usd']:+.2f}" if j["wti_net_usd"] is not None else "-"
    return (f"  {j['name']:<44} {j['trades']:>3} {j['wins']:>3} {j['net_usd']:>8.2f} {j['roi_pct']:>6.1f}% "
            f"{j['max_dd_pct']:>5.1f} {j['last_third_usd']:>8.2f} {wti:>8}{now_s}  {flags}")


def _print(rep: Dict[str, Any]) -> None:
    print(f"\nrule fusion  {rep['window'][0]} -> {rep['window'][1]} ({rep['days']}d, {rep['n_theses']} theses, "
          f"start ${rep['start_equity']}, now ${rep['equity_now']}"
          f"{', 20/15 leverage fit in the baseline' if rep.get('lev_fit_in_baseline') else ''})")
    print(f"  {'combo':<44} {'n':>3} {'w':>3} {'net$':>8} {'ROI':>7} {'dd%':>5} {'last3':>8} {'WTI$':>8}")
    now = rep["at_equity_now"]
    print(_fmt_row(rep["baseline"], now.get("baseline")))
    print("  -- each rule alone")
    for n, j in rep["singles"].items():
        print(_fmt_row(j, now.get(n)))
    print(f"  -- top {len(rep['top'])} by net")
    for j in rep["top"]:
        print(_fmt_row(j, now.get(j["name"])))
    f = rep["funnel"]
    print(f"\nfunnel: {f['combos_tried']} combos tried; {f['beat_baseline_net']} beat the baseline on net; "
          f"old bar {f['old_bar']}; drop-best-day {f['drop_best_day']}; weeks {f['weeks']}; plateau {f['plateau']}; "
          f"marginal {f['marginal']}; all guards {f['all']}")
    print(f"verdict: {'FUSION PASSES ' + rep['best'] if rep['best'] else 'NONE'}")
    if rep["best"]:
        print(_fmt_row(rep["verdict"]["best"], now.get(rep["best"])))
    lf = rep["lev_fit_diagnostic"]
    print(f"lev_fit (20/15, live) on the baseline: {lf['lev_fit_net_usd']:+.2f}$ vs {lf['baseline_net_usd']:+.2f}$ "
          f"-> {'identical' if lf['identical'] else 'DIFFERS'}")
    base_fit = lf["rows_with_fit_net_usd"].get("baseline")
    for name, net in lf["rows_with_fit_net_usd"].items():
        if name != "baseline" and base_fit is not None:
            print(f"    with lev_fit: {name:<44} {net:>8.2f}  ({net - base_fit:+.2f} vs the fitted baseline)")
    print("\noverlap (pairs at their tested value):")
    for r in rep["overlap"]:
        print(f"  {r['a']:<12} {r['b']:<12} singles {r['net_a']:>8.2f} {r['net_b']:>8.2f} pair {r['net_pair']:>8.2f} "
              f"removed {r['removed_a']:>2}/{r['removed_b']:>2} shared {r['shared_removed']:>2} "
              f"interaction {r['interaction_usd']:>8.2f}  {r['label']}")


if __name__ == "__main__":
    raise SystemExit(main())
