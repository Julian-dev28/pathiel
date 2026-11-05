"""Gold strategy test, 2026-11-05 (docs/strategy/GOLD_STRATEGY_TEST_2026-11-05.md).

Operator, 2026-11-05: "stop the silver tick, and silver market, add gold tick
and gold market, same formula, headlines, thesis, price action, high lows,
etc ... allocate 10% of our equity". Silver went 0 for 5 (all shorts, all
stopped, -$14.41). Before xyz:GOLD goes live on the metals desk, the live
rule is run on gold's own public candles: which stop floor and which
leverage rule hold up on gold, and does the formula make money on gold
without a thesis at all (the no-thesis controls)?

The rule under test is the live non-Brent rule (`live_params.
live_silver_params`): market entry off the 24h range, stop beyond the 24h
edge by max(1.3 x the tick's 1h ATR14, FLOOR x mid), target the far 24h
edge less 0.5% as a limit, 2R gate, never chase, no entry after a break,
15% risk cap, 15-minute ticks. Two knobs vary, the rest is pinned:

  floor   0 (bare ATR), 0.3%, 0.4%, 0.5%, 0.6%, 0.8% (silver's live floor),
          1.0% of the mid. Gold's 1.3 x ATR14 runs about 0.34% of price
          (silver 0.63%, Brent 0.79%, 2026-09-14..11-05), its median 24h
          range about 1.35% (silver 2.5%, Brent 3.0%), so silver's 0.8%
          floor is 60% of gold's whole 24h range: the 2R gate to the far
          edge would refuse nearly every entry. The grid shows where the
          floor stops helping.
  lev     fix20: 20x fixed (silver before 2026-11-04); fit: 20/15
          (`next_trade.LEVERAGE_FIT`, Brent and silver since 2026-11-04).

BASELINE is `floor_0.8_fit`: silver's live rule transplanted to gold as is.

Datasets (one pinned snapshot, `services.strategies.backtest.snapshot`,
no network in the run itself):
  journal_gold       the metals journal's GOLD theses (READ ONLY; one, the
                     2026-10-23 short at 4028, before the desk moved to
                     silver), each held for its own 7-day horizon.
                     `stop_cap_test.same_market` keeps the gold ones.
  <ctrl>_30d         always_long / always_short / daily_sign (the 20-day
                     trend sign) over the snapshot's last 30 days, 15m ticks.
  <ctrl>_50d         the same over the whole 15m history (about 52 days).
  <ctrl>_1h_<n>d     the same over the whole 1h history (about 180 days
                     after the daily_sign warm-up), HOURLY ticks
                     (`Params.tick_minutes=60`): fewer entries than the live
                     15m loop, but four times the sample for the floor grid.
Each window also reports gold's own move (first close to last close), so a
control's result can be read against the market it ran in.

Adopt bar (`adopt_verdict`): the candidate beats the baseline on most
control datasets with a geo-mean ratio of 1 or more, ends above the $64
start on at least half of them, is a plateau on the floor grid (a
neighbour on either side also beats the baseline), its max drawdown is
within 2 points of the baseline's, and every stop sits inside half the
liquidation distance at the trade's own leverage. The journal dataset is
one thesis: reported, never scored.

    python -m services.strategies.backtest.snapshot --coin xyz:GOLD --days-back 240 \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/<stamp>
    python -m services.strategies.evals.gold_strategy_test \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/<stamp> \\
        [--metals-theses-dir <pathia>/.state/strategies/metals/theses]

Writes /tmp/gold-strategy/report.json and summary.csv. Never touches the
live account, the live config, the journal (read only) or any pathiel MCP
tool. No LLM calls.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest import snapshot as SNAP
from services.strategies.backtest.desk_tick_sim import Params, simulate
from services.strategies.evals.brent_stop_buffer_test import (
    MAX_STOP_FRAC_OF_LIQ, _iso, plateau, score_vs_live, summarize)
from services.strategies.evals.closer_target_test import per_day_pct
from services.strategies.evals.cross_market_backtest import mean_atr
from services.strategies.evals.desk_tick_backtest import DAY_MS, journaled_direction
from services.strategies.evals.final_strategy_test import corrected_max_stop_liq_frac
from services.strategies.evals.live_params import live_silver_params
from services.strategies.evals.market_scout import direction_series, fetch_market_meta
from services.strategies.evals.stop_cap_test import same_market

COIN = "xyz:GOLD"
START = 64.0
DEFAULT_GOLD_MAX_LEVERAGE = 25.0        # venue max for xyz:GOLD (metaAndAssetCtxs dex=xyz, 2026-11-05)
FLOOR_PCTS: Tuple[float, ...] = (0.0, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0)
LEV_MODES: Dict[str, Optional[Tuple[float, ...]]] = {"fix20": None, "fit": (20.0, 15.0)}
BASELINE = "floor_0.8_fit"              # silver's live rule as is
CONTROLS: Tuple[str, ...] = ("always_long", "always_short", "daily_sign")
THESIS_HORIZON_DAYS = 7                 # a metals thesis's own horizon (DeskSpec.horizon_days)
DAILY_SIGN_WARMUP_DAYS = 25             # build_daily_sign_direction's 20-day lookback plus slack


# ── variants ─────────────────────────────────────────────────────────────

def variant_name(floor_pct: float, lev: str) -> str:
    return f"floor_{floor_pct:.1f}_{lev}"


def variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides on the gold base (`live_silver_params`'
    shape): every variant sets BOTH knobs, so the base's own floor and
    leverage rule never leak into a row."""
    out: Dict[str, Dict[str, Any]] = {}
    for p in FLOOR_PCTS:
        for lev, cands in LEV_MODES.items():
            out[variant_name(p, lev)] = {"stop_buffer_floor_frac": (p / 100.0) if p > 0 else None,
                                         "entry_fit_leverage": cands}
    return out


def families() -> Dict[str, List[str]]:
    """The floor grid per leverage mode, in floor order: the plateau check's
    neighbours (brent_stop_buffer_test.plateau)."""
    return {f"floor_{lev}": [variant_name(p, lev) for p in FLOOR_PCTS] for lev in LEV_MODES}


# ── datasets ─────────────────────────────────────────────────────────────

def gold_move_pct(c1h: Sequence[Candle], start_ms: int, end_ms: int) -> Optional[float]:
    """Gold's own move over [start_ms, end_ms): first close to last close,
    in percent. None with under two bars. Pure."""
    bars = [c for c in sorted(c1h, key=lambda c: c.t) if start_ms <= c.t < end_ms]
    if len(bars) < 2 or bars[0].c <= 0:
        return None
    return round((bars[-1].c / bars[0].c - 1.0) * 100.0, 2)


def journal_window(theses: Sequence[D.ThesisDirection], data_end_ms: int,
                   horizon_days: int = THESIS_HORIZON_DAYS) -> Optional[Tuple[int, int]]:
    """[first thesis, last thesis + its horizon), clamped to the data. The
    journal's gold theses are not followed by another gold call, so the
    sim would otherwise hold the last call's side until the data ends.
    None when there is nothing to run. Pure."""
    if not theses:
        return None
    start = min(th.created_at_ms for th in theses)
    end = min(data_end_ms, max(th.created_at_ms for th in theses) + horizon_days * DAY_MS)
    return (start, end) if end > start else None


def run_variants(c15: Sequence[Candle], c1h: Sequence[Candle], direction_at, base: Params, start_ms: int,
                 end_ms: int, named: Dict[str, Dict[str, Any]], funding=None) -> Dict[str, Any]:
    """Every variant on one window. Like brent_stop_buffer_test.run_dataset,
    but the stop/liquidation fraction is the trade's OWN leverage's
    (final_strategy_test.corrected_max_stop_liq_frac), which the fit
    variants need, and each row carries its per-day return."""
    w15 = [c for c in c15 if start_ms <= c.t < end_ms]
    w1h = [c for c in c1h if c.t < end_ms]
    out: Dict[str, Any] = {"window": [_iso(start_ms), _iso(end_ms)], "days": round((end_ms - start_ms) / DAY_MS, 1),
                           "gold_move_pct": gold_move_pct(c1h, start_ms, end_ms), "variants": {}}
    for v, ov in named.items():
        p = dataclasses.replace(base, **ov)
        run = simulate(w15, w1h, direction_at, p, funding)
        s = summarize(run, start_ms, end_ms)
        s["max_stop_liq_frac"] = round(corrected_max_stop_liq_frac(run, p), 3)
        s["per_day_pct"] = per_day_pct(s)
        out["variants"][v] = s
    return out


# ── scoring ──────────────────────────────────────────────────────────────

def positive_count(results: Dict[str, Any], name: str, datasets: Sequence[str], start: float = START) -> int:
    """How many of `datasets` the variant ends above its starting equity on."""
    return sum(1 for ds in datasets if ds in results and results[ds]["variants"][name]["final_equity"] > start)


def adopt_verdict(control_scores: Dict[str, Dict[str, Any]], results: Dict[str, Any],
                  control_datasets: Sequence[str], name: str, baseline_dd: float,
                  fams: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """The adopt bar (module docstring). Pure."""
    fams = fams or families()
    s = control_scores.get(name)
    if s is None:
        return {"adopt": False, "candidate": name, "why": "no score"}
    beats = s["beats"] > s["of"] / 2.0 and s["geo_mean_ratio"] >= 1.0
    pos = positive_count(results, name, control_datasets)
    positive = len(control_datasets) > 0 and pos >= len(control_datasets) / 2.0
    max_dd = max((results[ds]["variants"][name]["max_dd_pct"] for ds in control_datasets if ds in results), default=0.0)
    max_liq = max((results[ds]["variants"][name]["max_stop_liq_frac"] for ds in control_datasets if ds in results),
                  default=0.0)
    dd_ok = max_dd <= baseline_dd + 2.0
    liq_ok = max_liq <= MAX_STOP_FRAC_OF_LIQ + 1e-9
    flat = plateau(control_scores, name, fams)
    fails = [w for w, ok in (("beats the baseline on most controls", beats),
                            ("ends above the start on at least half the controls", positive),
                            ("max DD within 2pts of the baseline", dd_ok), ("plateau on the floor grid", flat),
                            ("stop inside half the liquidation distance", liq_ok)) if not ok]
    return {"adopt": beats and positive and dd_ok and flat and liq_ok, "candidate": name,
            "control_beats": s["beats"], "control_of": s["of"], "control_geo_mean": s["geo_mean_ratio"],
            "positive_controls": pos, "controls": len(control_datasets), "max_dd_pct": round(max_dd, 1),
            "max_stop_liq_frac": round(max_liq, 3), "plateau": flat, "fails": fails}


def rank(verdicts: Dict[str, Dict[str, Any]], control_scores: Dict[str, Dict[str, Any]]) -> List[str]:
    """Candidates best first: adopters first, then by controls beaten minus
    lost, geo-mean ratio, datasets ended positive; the name breaks ties
    (deterministic). Pure."""
    def key(v: str):
        s = control_scores.get(v) or {"beats": 0, "loses": 0, "geo_mean_ratio": 0.0}
        ver = verdicts.get(v) or {}
        return (bool(ver.get("adopt")), s["beats"] - s["loses"], s["geo_mean_ratio"], ver.get("positive_controls", 0), v)
    return sorted(verdicts, key=key, reverse=True)


def floor_curve(results: Dict[str, Any], control_datasets: Sequence[str]) -> List[Dict[str, Any]]:
    """Per floor x leverage mode: the sum of final equity minus start across
    the control datasets, trades and the worst drawdown. The shape of the
    curve is the plateau argument in the doc. Pure."""
    rows = []
    for p in FLOOR_PCTS:
        for lev in LEV_MODES:
            name = variant_name(p, lev)
            net = trades = 0.0
            dd = 0.0
            for ds in control_datasets:
                s = results[ds]["variants"][name]
                net += s["final_equity"] - START
                trades += s["trades"]
                dd = max(dd, s["max_dd_pct"])
            rows.append({"floor_pct": p, "lev": lev, "variant": name, "net_usd": round(net, 2),
                         "trades": int(trades), "max_dd_pct": dd})
    return rows


# ── CSV / report plumbing ───────────────────────────────────────────────

SUMMARY_FIELDS = ["dataset", "window_start", "window_end", "days", "gold_move_pct", "variant", "final_equity",
                  "net_usd", "return_pct", "per_day_pct", "max_dd_pct", "trades", "win_rate", "avg_r",
                  "worst_loss_pct", "max_stop_liq_frac", "stop_exits", "target_exits"]


def summary_csv_rows(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ds, r in results.items():
        for v, s in r["variants"].items():
            exits = s.get("exits") or {}
            out.append({"dataset": ds, "window_start": r["window"][0], "window_end": r["window"][1], "days": r["days"],
                        "gold_move_pct": r["gold_move_pct"], "variant": v, "final_equity": s["final_equity"],
                        "net_usd": round(s["final_equity"] - START, 2), "return_pct": s["return_pct"],
                        "per_day_pct": s["per_day_pct"], "max_dd_pct": s["max_dd_pct"], "trades": s["trades"],
                        "win_rate": s["win_rate"], "avg_r": s["avg_r"], "worst_loss_pct": s.get("worst_loss_pct"),
                        "max_stop_liq_frac": s["max_stop_liq_frac"], "stop_exits": exits.get("stop", 0),
                        "target_exits": exits.get("target", 0)})
    return out


def _latest_snapshot_dir() -> Optional[str]:
    root = SNAP.DEFAULT_SNAPSHOT_ROOT
    if not os.path.isdir(root):
        return None
    stamps = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    return os.path.join(root, stamps[-1]) if stamps else None


# ── main ─────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot-dir", default=_latest_snapshot_dir(),
                    help=f"default: the newest dir under {SNAP.DEFAULT_SNAPSHOT_ROOT}")
    ap.add_argument("--coin", default=COIN)
    ap.add_argument("--metals-theses-dir", default=os.environ.get(
        "PATHIEL_METALS_THESES_DIR", os.path.expanduser("~/Documents/code/pathia/.state/strategies/metals/theses")),
        help="READ ONLY: the live metals journal's theses (same_market keeps the gold ones)")
    ap.add_argument("--gold-max-leverage", type=float, default=None,
                    help="default: fetch the venue's own maxLeverage for --coin")
    ap.add_argument("--out-dir", default="/tmp/gold-strategy")
    a = ap.parse_args(argv)
    if not a.snapshot_dir:
        raise SystemExit("no --snapshot-dir given and none found under " + SNAP.DEFAULT_SNAPSHOT_ROOT)
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} gold-strategy: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    snap = SNAP.load_snapshot(a.snapshot_dir, a.coin)
    c15, c1h, funding = snap.c15, snap.c1h, snap.funding
    if not c15 or not c1h:
        raise SystemExit(f"snapshot at {a.snapshot_dir!r} has no {a.coin} candles -- run snapshot.py first")
    b15_start, b15_end = snap.window("15m")
    b1h_start, _b1h_end = snap.window("1h")
    log(f"snapshot {a.snapshot_dir}: 15m {snap.window_iso('15m')}, 1h {snap.window_iso('1h')}")

    if a.gold_max_leverage is None:
        try:
            meta = fetch_market_meta([a.coin]).get(a.coin) or {}
            a.gold_max_leverage = float(meta.get("maxLeverage") or DEFAULT_GOLD_MAX_LEVERAGE)
        except Exception as e:   # pragma: no cover -- network hiccup, fall back rather than abort the whole eval
            log(f"venue maxLeverage fetch failed ({e!r}), using the default {DEFAULT_GOLD_MAX_LEVERAGE}")
            a.gold_max_leverage = DEFAULT_GOLD_MAX_LEVERAGE
    atr_price, atr_pct = mean_atr(c1h)
    base = live_silver_params(a.gold_max_leverage, atr_price or 10.0, starting_equity=START)
    log(f"gold max leverage {a.gold_max_leverage:g}x, mean 1h ATR14 {atr_price} ({(atr_pct or 0) * 100:.3f}% of price)")

    named = variants()
    results: Dict[str, Any] = {}
    control_datasets: List[str] = []

    # journal: the gold theses, each for its own horizon
    raw = D.load_journaled_theses(a.metals_theses_dir)
    gold_theses = same_market(raw, c1h)
    jw = journal_window(gold_theses, b15_end)
    journal_calls = [{"thesis_id": th.thesis_id, "created_at": _iso(th.created_at_ms), "side": th.side,
                      "entry_px": th.entry_px} for th in gold_theses]
    if jw and any(th.side in ("long", "short") for th in gold_theses):
        start, end = jw
        start = max(start, b15_start)
        results["journal_gold"] = run_variants(c15, c1h, journaled_direction(gold_theses), base, start, end, named,
                                              funding)
        log(f"journal_gold: {len(gold_theses)} gold theses of {len(raw)}, window {_iso(start)}..{_iso(end)}")
    else:
        log(f"journal_gold: no gold theses with a side at {a.metals_theses_dir!r}, skipped")

    # controls, 15m ticks: last 30 days and the whole 15m history
    for days, tag in ((30, "30d"), (None, "50d")):
        start = b15_end - days * DAY_MS if days else b15_start
        for s in CONTROLS:
            name = f"{s}_{tag}"
            results[name] = run_variants(c15, c1h, direction_series(s, c1h), base, start, b15_end, named, funding)
            control_datasets.append(name)
        log(f"controls {tag}: {_iso(start)}..{_iso(b15_end)}, gold {results[f'{CONTROLS[0]}_{tag}']['gold_move_pct']:+}%")

    # controls, hourly ticks over the whole 1h history (after the daily_sign warm-up)
    base_1h = dataclasses.replace(base, tick_minutes=60)
    start_1h = b1h_start + DAILY_SIGN_WARMUP_DAYS * DAY_MS
    n_days = int((b15_end - start_1h) / DAY_MS)
    for s in CONTROLS:
        name = f"{s}_1h_{n_days}d"
        results[name] = run_variants([], c1h, direction_series(s, c1h), base_1h, start_1h, b15_end, named, funding)
        control_datasets.append(name)
    log(f"controls 1h ticks: {_iso(start_1h)}..{_iso(b15_end)} ({n_days} days), "
        f"gold {results[f'{CONTROLS[0]}_1h_{n_days}d']['gold_move_pct']:+}%")

    control_scores = score_vs_live({ds: results[ds]["variants"] for ds in control_datasets}, BASELINE)
    baseline_dd = max(results[ds]["variants"][BASELINE]["max_dd_pct"] for ds in control_datasets)
    verdicts = {v: adopt_verdict(control_scores, results, control_datasets, v, baseline_dd) for v in named
                if v != BASELINE}
    ranked = rank(verdicts, control_scores)
    adopt = [v for v in ranked if verdicts[v]["adopt"]]
    curve = floor_curve(results, control_datasets)
    log(f"scored ({time.time() - t0:.0f}s); adopt {adopt or 'none'}; best {ranked[0] if ranked else None}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "snapshot_dir": a.snapshot_dir,
        "coin": a.coin, "gold_max_leverage": a.gold_max_leverage, "mean_atr14_1h": atr_price,
        "mean_atr14_1h_pct": atr_pct, "snapshot_window": {iv: snap.window_iso(iv) for iv in SNAP.INTERVALS},
        "baseline": BASELINE, "journal_calls": journal_calls,
        "results": results, "control_datasets": control_datasets, "control_scores": control_scores,
        "floor_curve": curve, "verdicts": verdicts, "ranked": ranked, "adopt": adopt, "variants": named,
    }
    with open(os.path.join(a.out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    rows = summary_csv_rows(results)
    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(rows)
    log(f"done in {time.time() - t0:.0f}s -> {a.out_dir}/report.json, summary.csv")
    _print(report)
    return 0


def _print(rep: Dict[str, Any]) -> None:
    print(f"\nbaseline {rep['baseline']}; adopt: {rep['adopt'] or 'none'}; ranked: {rep['ranked'][:5]}")
    print("floor curve (sum of net $ over the controls, trades, worst DD):")
    for r in rep["floor_curve"]:
        print(f"  {r['variant']:18s} net {r['net_usd']:+9.2f}  trades {r['trades']:4d}  maxDD {r['max_dd_pct']:.1f}")
    print("per dataset (variant: net $ / trades / win rate / max DD):")
    for ds, r in rep["results"].items():
        print(f"  {ds} {r['window'][0]}..{r['window'][1]} gold {r['gold_move_pct']:+}%")
        for v, s in r["variants"].items():
            wr = f"{s['win_rate']:.2f}" if s["win_rate"] is not None else "  -"
            print(f"     {v:18s} {s['final_equity'] - START:+8.2f} {s['trades']:4d} {wr} {s['max_dd_pct']:5.1f}")


if __name__ == "__main__":
    raise SystemExit(main())
