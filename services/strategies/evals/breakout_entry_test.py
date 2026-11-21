"""Oil desk breakout/continuation entry test, 2026-11-19.

Pre-registration (written before this file ran against real data)
--------------------------------------------------------------------
Operator, 2026-11-18/19: "ur like doing one trade every 2 weeks" ... "i need
to see some profitable trades soon". The desk's thesis direction has been
right but the live entry rule (`services/strategies/next_trade.py` via
`scripts/oil_levels.py --next`) only buys a pullback to the thesis's own
entry zone and never chases past it -- replayed exactly by `desk_tick_sim.
live_params()`/`live_brent_params()`. Two clear cases in the 24h before this
file was written:

  1. 2026-11-18 09:10Z: short thesis (`oil-20261118T0910-m.json`) waits to
     sell a rally to 90.30-91.17. Brent instead fell from ~90.5 to ~87.6
     after the EIA report -- the short never filled.
  2. 2026-11-19 00:32Z: long thesis (`oil-20261119T0032-m.json`) waits to
     buy a dip to 88.40-88.80, target 91.664. Brent ran 88.6 -> 90.34 and
     hit the target with no entry.

Both are the SAME shape: price moves away from the pullback zone, in the
thesis's OWN direction, breaking structure (the 8h high/low) on the way.
Question: does a SECOND, additional entry on the thesis side -- a momentum/
breakout entry on an 8h structure break -- catch trades like these two
without losing money on net, on both real datasets, both halves, within a
drawdown bar?

Two earlier tests already said NO to adjacent fixes: lowering the live 2R
gate to 1.5R (`docs/strategy/MIN_R_GATE_TEST_2026-11-18.md`,
`min_r_gate_test.py`) and resting the live rule's own trigger as a limit
order (`feature/rest-entry-at-trigger-2026-11-18`, `rest_at_trigger_eval.py`
-- WORSE on net R, +3.89R vs +8.73R, docs/strategy/OIL_STRATEGY.md's
2026-11-18 entry). This file reuses BOTH tests' real-thesis datasets and
loaders (`services.strategies.backtest.data`, `desk_tick_backtest.
journaled_direction`, `live_params.live_brent_params`, `stop_cap_test`'s
`FIFTEEN_MS`/`START`/`_iso`/`closed_only`, `market_scout.load_coin_candles`)
and the min_r test's own generic `run_dataset` (full window + both halves +
long/short cuts over an arbitrary `Params` override dict -- never
min_r-specific) rather than rebuilding any of that harness.

The entry itself is `desk_tick_sim.py`'s new `Params.pullback_entry == "a6"`
(see that module's own Params block for the full mechanics): on a live
"chase" or "min_r" refusal, buy/sell the break of the prior 8h high/low in
the thesis direction, confirmed on a closed 15m OR 1h bar, stop back inside
the broken level by k x ATR14(1h) or at the 8h range's own midpoint, target
shared with every other pullback mode (thesis / the 24h far edge / a fixed
1.5R-2R, the 1.5R row needing its own `pb_breakout_min_r` override since the
live 2R gate would otherwise refuse a target that pays exactly 1.5R by
construction), sized at full or half the desk's size, capped at one fill per
thesis call. The live pullback rule is UNCHANGED and stays active
(`live_brent_params()`'s own fields are never touched): a6 is layered on
top, one slot at a time (desk_tick_sim's existing one-position invariant
already makes a6 and the pullback rule mutually exclusive -- whichever one's
condition is met first takes the slot). Event blackouts, the weekend flat
and live fees (taker 4.5bps, maker 1.5bps, the real EXIT_SLIP_CURVE via
`size_slippage=True`) are the same live_brent_params() already enforces.

Grid: confirm (1h, 15m) x stop (level_atr k=0.5/0.75/1.0, midpoint) x target
(thesis, 24h-extreme, fixed 1.5R, fixed 2R) x size (full, half) = 64 named
variants, each run against the UNMODIFIED live rule ("live") on the same
data.

Datasets, PRIMARY (same two the min_r test gated on):
  brent_journal    the live oil journal's theses (READ ONLY,
                   $PATHIEL_STATE_DIR/strategies/oil/theses), 2026-10-18 to
                   data end -- this is also where the two named cases live.
  brent_replay30   30 replayed Opus theses, price only
                   (/tmp/thesis-replay-30b/oil/theses), READ ONLY

Adoption bar, fixed before any table below was read (`variant_bar`):
  1. Net $ beats "live" in BOTH halves of EVERY primary dataset.
  2. Full-window max drawdown is no more than `MATERIAL_DD_PP` (5.0)
     percentage points worse than "live", on every primary dataset.
A variant clearing both is a PASS candidate; `NOISE_MIN_TRADES` (8) a6 fills
on either primary dataset downgrades an otherwise-passing variant to NOISE
(too few fills to trust the win). A variant that beats "live" on the FULL
window of BOTH datasets (not necessarily both halves) and still clears the
drawdown bar, with nothing passing outright, is LEANING. Anything else is
FAIL. `overall_verdict` ranks candidates by combined net $ edge; it never
reports a number it did not compute.

The two named cases are read off the SAME `brent_journal` run's own trade
list every variant above already produced (`case_rows`) -- never a second,
hand-rolled walk of those two windows.

    python -m services.strategies.evals.breakout_entry_test \\
        --oil-theses-dir "$PATHIEL_STATE_DIR/strategies/oil/theses" \\
        --oil-replay-dir /tmp/thesis-replay-30b/oil/theses

Reads candles through the eval disk cache and the venue's public /info;
reads the oil journal READ ONLY. Never touches the live account, config,
place_bracket, or the desk-tick agent. No LLM calls.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.strategies.backtest import data as D
from services.strategies.backtest.desk_tick_sim import Trade
from services.strategies.evals.desk_tick_backtest import DAY_MS, journaled_direction
from services.strategies.evals.live_params import live_brent_params
from services.strategies.evals.min_r_gate_test import run_dataset
from services.strategies.evals.stop_cap_test import FIFTEEN_MS, START, _iso, closed_only

BREAKOUT_ENTRY_H = 8
MATERIAL_DD_PP = 5.0
NOISE_MIN_TRADES = 8
BASELINE = "live"

# ── the 64-variant grid (pure) ───────────────────────────────────────────

CONFIRMS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("1h", {"pb_breakout_confirm": "1h"}),
    ("15m", {"pb_breakout_confirm": "15m"}),
)
STOPS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("k0.5", {"pb_breakout_stop_mode": "level_atr", "pb_breakout_stop_k": 0.5}),
    ("k0.75", {"pb_breakout_stop_mode": "level_atr", "pb_breakout_stop_k": 0.75}),
    ("k1.0", {"pb_breakout_stop_mode": "level_atr", "pb_breakout_stop_k": 1.0}),
    ("mid", {"pb_breakout_stop_mode": "midpoint"}),
)
# "1.5R"/"2R" set pb_breakout_min_r to the SAME value as pb_target_r: a fixed
# target that pays exactly N R is, by construction, refused by the live 2R
# gate for any N < 2.0 unless this mode's own gate is relaxed to match (see
# the module docstring and desk_tick_sim.py's pb_breakout_min_r field). The
# live rule's own min_r (2.0) is never touched either way.
TARGETS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("thesis", {"pb_target": "thesis"}),
    ("24hext", {"pb_target": "far"}),
    ("1.5R", {"pb_target_r": 1.5, "pb_breakout_min_r": 1.5}),
    ("2R", {"pb_target_r": 2.0, "pb_breakout_min_r": 2.0}),
)
SIZES: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("full", {"pb_breakout_size_frac": 1.0}),
    ("half", {"pb_breakout_size_frac": 0.5}),
)


def variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides; "live" (empty overrides: the unmodified
    live rule) plus one entry per point in the confirm x stop x target x
    size grid, every one of them `pullback_entry="a6"` ON TOP of whatever
    `base` (live_brent_params()) already is -- the pullback rule is never
    turned off. Pure."""
    out: Dict[str, Dict[str, Any]] = {BASELINE: {}}
    for cn, co in CONFIRMS:
        for sn, so in STOPS:
            for tn, to in TARGETS:
                for zn, zo in SIZES:
                    name = f"a6_{cn}_{sn}_{tn}_{zn}"
                    out[name] = {"pullback_entry": "a6", "pb_breakout_entry_h": BREAKOUT_ENTRY_H,
                                "pb_breakout_cap_one": True, **co, **so, **to, **zo}
    return out


# ── adoption bar (pure) ───────────────────────────────────────────────────

def adv_usd(by_v: Dict[str, Dict[str, Any]], variant: str, baseline: str = BASELINE) -> Optional[float]:
    """net $ at `variant` minus net $ at `baseline`; None if either row is
    missing. Pure."""
    if variant not in by_v or baseline not in by_v:
        return None
    return round(by_v[variant]["net_usd"] - by_v[baseline]["net_usd"], 2)


def variant_bar(primary: Dict[str, Dict[str, Dict[str, Any]]], variant: str, baseline: str = BASELINE,
                material_dd_pp: float = MATERIAL_DD_PP) -> Dict[str, Any]:
    """The two-part adoption bar (pre-registered above): net $ beats
    `baseline` in BOTH halves of EVERY primary dataset, and the full-window
    drawdown is no more than `material_dd_pp` points worse, on every
    primary dataset. Pure."""
    reasons: List[str] = []
    both_halves = True
    for ds, windows in primary.items():
        for half in ("half1", "half2"):
            a = adv_usd(windows[half], variant, baseline)
            if a is None or a <= 0:
                both_halves = False
                reasons.append(f"{ds} {half}: {variant} net $ does not beat {baseline} (edge {a})")
    dd_ok = True
    for ds, windows in primary.items():
        f = windows["full"]
        if variant not in f or baseline not in f:
            continue
        gap = round(f[variant]["max_dd_pct"] - f[baseline]["max_dd_pct"], 1)
        if gap > material_dd_pp:
            dd_ok = False
            reasons.append(f"{ds}: {variant} drawdown {gap:.1f}pp worse than {baseline} (bar {material_dd_pp:.1f}pp)")
    return {"adopt": both_halves and dd_ok, "both_halves": both_halves, "dd_ok": dd_ok, "reasons": reasons}


def full_window_wins_both(primary: Dict[str, Dict[str, Dict[str, Any]]], variant: str,
                          baseline: str = BASELINE) -> bool:
    return all((adv_usd(windows["full"], variant, baseline) or 0.0) > 0 for windows in primary.values())


def trade_count(primary: Dict[str, Dict[str, Dict[str, Any]]], ds: str, variant: str) -> int:
    return primary[ds]["full"].get(variant, {}).get("trades", 0)


def _parse_variant(name: str) -> Optional[Tuple[str, str, str, str]]:
    """"a6_<confirm>_<stop>_<target>_<size>" -> its 4 axis tokens, or None
    for "live" or anything else outside the grid. Pure."""
    if not name.startswith("a6_"):
        return None
    parts = name[len("a6_"):].split("_")
    return tuple(parts) if len(parts) == 4 else None  # type: ignore[return-value]


def neighbor_names(name: str) -> List[str]:
    """Every variant one axis-value away from `name` (confirm, stop, target
    or size changed, the other three held fixed) -- the grid's own
    adjacency, not a numeric one (categorical axes have no "distance").
    Pure."""
    parsed = _parse_variant(name)
    if parsed is None:
        return []
    confirm, stop, target, size = parsed
    out: List[str] = []
    for cn, _ in CONFIRMS:
        if cn != confirm:
            out.append(f"a6_{cn}_{stop}_{target}_{size}")
    for sn, _ in STOPS:
        if sn != stop:
            out.append(f"a6_{confirm}_{sn}_{target}_{size}")
    for tn, _ in TARGETS:
        if tn != target:
            out.append(f"a6_{confirm}_{stop}_{tn}_{size}")
    for zn, _ in SIZES:
        if zn != size:
            out.append(f"a6_{confirm}_{stop}_{target}_{zn}")
    return out


def is_isolated(primary: Dict[str, Dict[str, Dict[str, Any]]], variant: str) -> bool:
    """True when NOT ONE of `variant`'s one-axis-away neighbors also wins
    the full window on BOTH primary datasets -- the signature of scanning a
    64-point grid and landing on a multiple-comparisons artifact rather
    than a parameter-stable edge (a real effect should survive a small
    nudge to k, the confirmation timeframe, the target or the size; an
    isolated spike surrounded by neighbors that instead win on ONE dataset
    and lose on the OTHER, by a much larger margin, is exactly what
    overfitting to one grid point looks like). Pure."""
    return not any(full_window_wins_both(primary, nb) for nb in neighbor_names(variant))


def overall_verdict(primary: Dict[str, Dict[str, Dict[str, Any]]], names: Sequence[str]
                    ) -> Tuple[str, Dict[str, Dict[str, Any]], List[str]]:
    """PASS / LEANING / FAIL / NOISE plus every variant's own bar result and
    the ranked list of candidates behind whichever label won (empty on
    FAIL). Ranked by combined net $ edge across every primary dataset's
    full window. A variant that clears the two-part bar is still NOISE, not
    PASS, when either its a6 fill count is thin (`NOISE_MIN_TRADES`) or it
    is `is_isolated` -- do not round up a multiple-comparisons artifact
    into an adoption. Pure."""
    bars = {v: variant_bar(primary, v) for v in names}

    def edge(v: str) -> float:
        return sum((adv_usd(primary[ds]["full"], v) or 0.0) for ds in primary)

    passing = sorted((v for v, b in bars.items() if b["adopt"]), key=edge, reverse=True)
    if passing:
        thin = [v for v in passing if any(trade_count(primary, ds, v) < NOISE_MIN_TRADES for ds in primary)]
        isolated = [v for v in passing if is_isolated(primary, v)]
        if len(thin) == len(passing) or len(isolated) == len(passing):
            return "NOISE", bars, passing
        return "PASS", bars, passing
    leaning = sorted((v for v in names if full_window_wins_both(primary, v) and bars[v]["dd_ok"]),
                     key=edge, reverse=True)
    if leaning:
        return "LEANING", bars, leaning
    return "FAIL", bars, []


# ── the two named cases (pure: reads trades already produced above) ──────

def _utc_ms(y: int, mo: int, d: int, h: int, mi: int, s: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000)


# 2026-11-18 09:10:28Z (oil-20261118T0910-m.json, "sell a rally to 90.30")
# through its rewrite at 19:37:24Z (oil-20261118T1937-m.json) -- the EIA-day
# fall from ~90.5 to ~87.6 happened entirely inside this call's own window.
CASE_1118 = ("2026-11-18 09:10Z short: sell a rally to 90.30-91.17, Brent fell to ~87.6 after EIA instead",
            _utc_ms(2026, 11, 18, 9, 10, 28), _utc_ms(2026, 11, 18, 19, 37, 24), "short")
# 2026-11-19 00:32:16Z (oil-20261119T0032-m.json, "buy a dip to 88.40")
# through 14:00Z: the progress log's own 13:40Z tick already shows mid
# 90.20, just under this thesis's 91.664 target -- the run from 88.6 to
# 90.34 happened entirely inside this window too.
CASE_1119 = ("2026-11-19 00:32Z long: buy a dip to 88.40-88.80, Brent ran 88.6 -> 90.34 with no entry",
            _utc_ms(2026, 11, 19, 0, 32, 16), _utc_ms(2026, 11, 19, 14, 0, 0), "long")
NAMED_CASES = (CASE_1118, CASE_1119)


def case_rows(trades_by_variant: Dict[str, Sequence[Trade]], start_ms: int, end_ms: int, side: str
             ) -> Dict[str, List[Dict[str, Any]]]:
    """Every variant's own trades (already simulated for the headline
    numbers) whose entry falls inside [start_ms, end_ms) on `side` -- what
    each variant actually did in this one real episode, read off the SAME
    run, never a second walk. Pure."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for v, trades in trades_by_variant.items():
        matches = [t for t in trades if start_ms <= t.entry_time < end_ms and t.side == side]
        out[v] = [{"entry": _iso(t.entry_time), "entry_px": round(t.entry_px, 3),
                   "exit": _iso(t.exit_time) if t.exit_time is not None else None,
                   "exit_reason": t.exit_reason,
                   "r_multiple": round(t.r_multiple, 2) if t.r_multiple is not None else None,
                   "net_usd": round(t.net_pnl_usd, 2)} for t in matches]
    return out


# ── data + runs ──────────────────────────────────────────────────────────

def _default_oil_theses_dir() -> str:
    env = os.environ.get("PATHIEL_OIL_THESES_DIR", "")
    if env:
        return env
    state = os.environ.get("PATHIEL_STATE_DIR") or os.path.expanduser("~/Documents/code/pathia/.state")
    return os.path.join(state, "strategies", "oil", "theses")


def main(argv: Optional[List[str]] = None) -> int:
    from services.strategies.evals.market_scout import load_coin_candles

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--oil-theses-dir", default=_default_oil_theses_dir(), help="READ ONLY: the live oil journal")
    ap.add_argument("--oil-replay-dir", default="/tmp/thesis-replay-30b/oil/theses")
    ap.add_argument("--cache-dir", default="/tmp/breakout-entry-test/cache")
    ap.add_argument("--out-dir", default="/tmp/breakout-entry-test")
    ap.add_argument("--quick", action="store_true", help="fast smoke run: live + one a6 combo, one dataset")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.cache_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} breakout-entry: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    named = variants()
    if a.quick:
        named = {BASELINE: {}, "a6_1h_k0.75_24hext_full": named["a6_1h_k0.75_24hext_full"]}

    def fresh(coin: str):
        got = load_coin_candles(coin, a.cache_dir)
        asof = int(os.path.getmtime(D._cache_path(a.cache_dir, coin, "candles_15m_4820")) * 1000)
        return closed_only(got["c15"], FIFTEEN_MS, asof), closed_only(got["c1h"], D.HOUR_MS, asof)

    b15, b1h = fresh("xyz:BRENTOIL")
    b_end = b15[-1].t + FIFTEEN_MS
    funding = D.fetch_funding_cached("xyz:BRENTOIL", b15[0].t, b_end, a.cache_dir)
    bp = live_brent_params(size_slippage=True, starting_equity=START)

    results: Dict[str, Dict[str, Any]] = {}
    primary_keys: List[str] = []

    theses = D.load_journaled_theses(a.oil_theses_dir) if a.oil_theses_dir else []
    if theses:
        start = min(th.created_at_ms for th in theses)
        results["brent_journal"] = run_dataset(b15, b1h, journaled_direction(theses), bp, start, b_end, named,
                                               funding)
        primary_keys.append("brent_journal")
        log(f"brent_journal: {len(theses)} theses, {len(named)} variants done ({time.time() - t0:.0f}s)")
    else:
        log(f"brent_journal: no theses at {a.oil_theses_dir!r}, skipped")

    if not a.quick:
        rtheses = D.load_journaled_theses(a.oil_replay_dir) if a.oil_replay_dir else []
        if rtheses:
            start = min(th.created_at_ms for th in rtheses)
            end = min(b_end, max(th.created_at_ms for th in rtheses) + DAY_MS)
            results["brent_replay30"] = run_dataset(b15, b1h, journaled_direction(rtheses), bp, start, end, named,
                                                     funding)
            primary_keys.append("brent_replay30")
            log(f"brent_replay30: {len(rtheses)} theses done ({time.time() - t0:.0f}s)")
        else:
            log(f"brent_replay30: no theses at {a.oil_replay_dir!r}, skipped")

    primary = {k: {w: results[k][w] for w in ("full", "half1", "half2")} for k in primary_keys}
    variant_names = [v for v in named if v != BASELINE]
    if primary and variant_names:
        verdict_label, bars, ranked = overall_verdict(primary, variant_names)
    else:
        verdict_label, bars, ranked = "NO PRIMARY DATA", {}, []
    log(f"verdict: {verdict_label} ({len(ranked)} candidate(s) behind it)")

    cases: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    if "brent_journal" in results and not a.quick:
        trades_by_variant = results["brent_journal"]["trades"]
        for label, start_ms, end_ms, side in NAMED_CASES:
            cases[label] = case_rows(trades_by_variant, start_ms, end_ms, side)
            n_filled = sum(1 for v, rows in cases[label].items() if v != BASELINE and rows)
            log(f"case [{label}]: live {'FILLED' if cases[label].get(BASELINE) else 'no fill'}, "
                f"{n_filled}/{len(variant_names)} a6 variants filled")

    csv_path = os.path.join(a.out_dir, "summary.csv")
    fields = ["dataset", "variant", "window", "trades", "trades_per_week", "win_rate", "mean_r", "net_r", "net_usd",
              "max_dd_pct", "worst_losing_streak"]
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for ds, r in results.items():
            for window in ("full", "half1", "half2", "long", "short"):
                for v, s in r[window].items():
                    w.writerow({"dataset": ds, "variant": v, "window": window,
                               **{k: s.get(k) for k in fields[3:]}})

    cases_csv = os.path.join(a.out_dir, "cases.csv")
    with open(cases_csv, "w", newline="") as fh:
        cfields = ["case", "variant", "entry", "entry_px", "exit", "exit_reason", "r_multiple", "net_usd"]
        w = csv.DictWriter(fh, fieldnames=cfields)
        w.writeheader()
        for label, by_variant in cases.items():
            for v, rows in by_variant.items():
                for row in rows:
                    w.writerow({"case": label, "variant": v, **row})
                if not rows:
                    w.writerow({"case": label, "variant": v, "entry": None, "entry_px": None, "exit": None,
                               "exit_reason": "no fill", "r_multiple": None, "net_usd": None})

    strip = lambda res: {k: {kk: vv for kk, vv in r.items() if kk != "trades"} for k, r in res.items()}  # noqa: E731
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "seconds": round(time.time() - t0, 1),
        "brent_data_end": _iso(b_end), "n_variants": len(named), "baseline": BASELINE,
        "datasets": strip(results), "primary_datasets": primary_keys,
        "verdict": {"label": verdict_label, "ranked_candidates": ranked, "bars": bars,
                   "isolated": {v: is_isolated(primary, v) for v in ranked} if primary else {},
                   "neighbor_edges": {v: {nb: sum((adv_usd(primary[ds]["full"], nb) or 0.0) for ds in primary_keys)
                                          for nb in neighbor_names(v) if nb in named}
                                     for v in ranked} if primary else {}},
        "cases": cases,
    }
    out_path = os.path.join(a.out_dir, "report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)

    print(f"brent data end {report['brent_data_end']}, {len(named)} variants ({len(variant_names)} a6 + live)")
    for ds in primary_keys:
        f = results[ds]["full"]
        live = f.get(BASELINE, {})
        print(f"\n{ds}: live net$={live.get('net_usd')} trades={live.get('trades')} "
             f"trades/wk={live.get('trades_per_week')} win%={live.get('win_rate')} dd%={live.get('max_dd_pct')}")
    print(f"\nVERDICT: {verdict_label}")
    for v in ranked[:5]:
        edge_v = sum((adv_usd(primary[ds]['full'], v) or 0.0) for ds in primary_keys)
        iso = is_isolated(primary, v) if primary else None
        print(f"  candidate: {v}  edge(sum full-window net$ vs live)={edge_v:.2f}  isolated={iso}")
        if primary and v in bars:
            for nb in neighbor_names(v):
                if nb in named:
                    nb_edge = sum((adv_usd(primary[ds]['full'], nb) or 0.0) for ds in primary_keys)
                    print(f"      neighbor {nb:35s} edge={nb_edge:7.2f}")
    for label, by_variant in cases.items():
        live_rows = by_variant.get(BASELINE, [])
        n_filled = sum(1 for v, rows in by_variant.items() if v != BASELINE and rows)
        print(f"\ncase [{label}]:\n  live: {'filled ' + str(live_rows) if live_rows else 'NO FILL (confirmed)'}")
        print(f"  a6: {n_filled}/{len(variant_names)} variants filled")
    print(f"\nreport: {out_path}\nsummary: {csv_path}\ncases: {cases_csv}")
    return 0   # research only: never a failing exit on the verdict


if __name__ == "__main__":
    raise SystemExit(main())
