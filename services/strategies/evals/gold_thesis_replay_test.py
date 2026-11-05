"""Gold thesis replay test, 2026-11-05 (docs/strategy/GOLD_THESIS_REPLAY_2026-11-05.md).

Operator, 2026-11-05: "replay gold". GOLD_STRATEGY_TEST_2026-11-05.md ran the
live formula on gold with no thesis and lost on 7 of 9 control windows; the
desk shipped inactive with one gate: the same 30-day thesis replay silver
got before it went live (SILVER_THESIS_TEST_2026-10-31.md). This eval is
that gate. The theses come from `services.thesis.evals.replay` (one per
day at 16:00Z, point in time: `truncating_fetcher` only hands the writer
bars that CLOSED at or before that day's as-of time), written to
/tmp/thesis-replay-gold/theses; this module reads them as the direction
source and runs the live gold rule over the same window.

The rule under test is `live_params.live_gold_params`: market entry off the
24h range, stop beyond the edge by max(1.3 x the tick's 1h ATR14, FLOOR x
mid), target the far 24h edge less 0.5% as a limit, 2R gate, never chase,
no entry after a break (`no_break_entry`), leverage fit 20/15, 15-minute
ticks. The risk cap is 6%: the operator moved `risk_per_trade_pct` to 6 on
2026-11-05, so `--risk-cap-frac 0.06` is the default here (the 15% in
`live_params.LIVE_RISK_CAP_FRAC` is the pre-11-05 value).

Variants: floor 0.6 / 0.8 / 1.0% x leverage fit 20/15 or fixed 20, every
one with `no_break_entry` on. `live` = floor 0.8 + fit, the rule as shipped.

Datasets, all on one pinned snapshot (no network in the run):
  replay          the replayed gold theses, window [first thesis, last
                  thesis + 1 day] clamped to the data (the silver test's
                  convention)
  journal_gold    the one journaled gold thesis (READ ONLY), for its 7-day
                  horizon (gold_strategy_test.journal_window)
  always_long / always_short / daily_sign over the replay window
  buy_hold        gold's own price return over the replay window, at 1x

VERDICT RULE, fixed before any result was read (`activation_verdict`):
activate only if the live variant on the replay dataset (1) ends net
positive, (2) ends above always_long, always_short AND daily_sign under the
same live variant on the same window, and (3) keeps its max drawdown under
35%. Anything else: do not activate.

    python -m services.strategies.evals.gold_thesis_replay_test \\
        --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261105T153000Z-gold \\
        --replay-theses-dir /tmp/thesis-replay-gold/theses

Writes /tmp/gold-thesis-replay/report.json and summary.csv. Never touches
the live account, the live config, the journal (read only) or any pathiel
MCP tool. No LLM calls (the theses were written beforehand).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.backtest import snapshot as SNAP
from services.strategies.backtest.desk_tick_sim import Params
from services.strategies.evals.brent_stop_buffer_test import _iso
from services.strategies.evals.cross_market_backtest import mean_atr
from services.strategies.evals.desk_tick_backtest import DAY_MS, journaled_direction
from services.strategies.evals.extended_history_test import buy_hold_pct, direction_hit_rate
from services.strategies.evals.gold_strategy_test import journal_window, run_variants
from services.strategies.evals.live_params import live_gold_params
from services.strategies.evals.market_scout import direction_series
from services.strategies.evals.stop_cap_test import same_market

COIN = "xyz:GOLD"
START = 64.0
DEFAULT_GOLD_MAX_LEVERAGE = 25.0        # venue max for xyz:GOLD (metaAndAssetCtxs dex=xyz, 2026-11-05)
LIVE_RISK_CAP_FRAC = 0.06               # risk_per_trade_pct 6 since 2026-11-05 (operator)
FLOOR_PCTS: Tuple[float, ...] = (0.6, 0.8, 1.0)
LEV_MODES: Dict[str, Optional[Tuple[float, ...]]] = {"fit": (20.0, 15.0), "fix20": None}
LIVE = "floor_0.8_fit"                  # live_gold_params as shipped
CONTROLS: Tuple[str, ...] = ("always_long", "always_short", "daily_sign")
MAX_DD_PCT = 35.0                       # the verdict rule's drawdown ceiling
THESIS_HORIZON_DAYS = 7
HIT_HORIZONS_DAYS: Tuple[float, ...] = (1.0, 3.0, 7.0)


# ── variants ─────────────────────────────────────────────────────────────

def variant_name(floor_pct: float, lev: str) -> str:
    return f"floor_{floor_pct:.1f}_{lev}"


def variants() -> Dict[str, Dict[str, Any]]:
    """name -> Params overrides on `live_gold_params`: both knobs set on
    every row, `no_break_entry` pinned on so the live rule's break guard
    never drifts out of a variant."""
    out: Dict[str, Dict[str, Any]] = {}
    for p in FLOOR_PCTS:
        for lev, cands in LEV_MODES.items():
            out[variant_name(p, lev)] = {"stop_buffer_floor_frac": p / 100.0, "entry_fit_leverage": cands,
                                         "no_break_entry": True}
    return out


def base_params(venue_max_leverage: float, atr_price_mean: float, risk_cap_frac: float = LIVE_RISK_CAP_FRAC,
                start: float = START) -> Params:
    """The live gold rule at today's risk cap. `live_gold_params` carries the
    15% cap the oil rule shipped with on 11-02; the operator moved live
    `risk_per_trade_pct` to 6 on 11-05, so the eval's base says so."""
    return live_gold_params(venue_max_leverage, atr_price_mean, starting_equity=start, risk_cap_frac=risk_cap_frac)


# ── datasets ─────────────────────────────────────────────────────────────

def replay_window(theses: Sequence[D.ThesisDirection], data_start_ms: int, data_end_ms: int,
                  tail_days: int = 1) -> Optional[Tuple[int, int]]:
    """[first thesis, last thesis + `tail_days`), clamped to the data on
    both ends (silver_strategy_test's convention for a replay dataset: the
    last call runs a day, not for ever). None with nothing to run. Pure."""
    if not theses:
        return None
    start = max(data_start_ms, min(th.created_at_ms for th in theses))
    end = min(data_end_ms, max(th.created_at_ms for th in theses) + tail_days * DAY_MS)
    return (start, end) if end > start else None


def side_counts(theses: Sequence[D.ThesisDirection]) -> Dict[str, int]:
    """How many replayed calls went long / short / flat. Pure."""
    return dict(sorted(Counter(th.side for th in theses).items()))


def buy_hold_row(c15: Sequence[Candle], start_ms: int, end_ms: int, start: float = START) -> Dict[str, Any]:
    """Gold held at 1x over the window: the price return and what $`start`
    became. No fees, no funding, no leverage. Pure."""
    pct = buy_hold_pct(c15, start_ms, end_ms)
    final = round(start * (1.0 + pct / 100.0), 2) if pct is not None else None
    return {"return_pct": pct, "final_equity": final, "net_usd": round(final - start, 2) if final is not None else None}


# ── the verdict ──────────────────────────────────────────────────────────

def activation_verdict(results: Dict[str, Any], live: str = LIVE, controls: Sequence[str] = CONTROLS,
                       start: float = START, max_dd_pct: float = MAX_DD_PCT) -> Dict[str, Any]:
    """The rule stated in the module docstring, applied to `results`
    ({dataset: {"variants": {name: summary}}}). The live variant on the
    "replay" dataset must end net positive, end above every control's live
    variant on the same window, and keep max drawdown under `max_dd_pct`.
    A missing replay dataset or control fails the matching check (never
    passes by absence). Pure."""
    rep = (results.get("replay") or {}).get("variants", {}).get(live)
    if rep is None:
        return {"activate": False, "live": live, "fails": ["no replay dataset"], "checks": {}}
    net = rep["final_equity"] - start
    beats: Dict[str, Optional[bool]] = {}
    for c in controls:
        cv = (results.get(c) or {}).get("variants", {}).get(live)
        beats[c] = None if cv is None else rep["final_equity"] > cv["final_equity"]
    checks = {"net_positive": net > 0,
              "beats_controls": all(bool(v) for v in beats.values()) and len(beats) == len(controls),
              "max_dd_under_cap": rep["max_dd_pct"] < max_dd_pct}
    fails = [{"net_positive": "net not positive", "beats_controls": "does not beat every control",
              "max_dd_under_cap": f"max drawdown {rep['max_dd_pct']}% not under {max_dd_pct:g}%"}[k]
             for k, ok in checks.items() if not ok]
    return {"activate": all(checks.values()), "live": live, "net_usd": round(net, 2),
            "max_dd_pct": rep["max_dd_pct"], "beats": beats, "checks": checks, "fails": fails}


# ── CSV / report plumbing ───────────────────────────────────────────────

SUMMARY_FIELDS = ["dataset", "window_start", "window_end", "days", "gold_move_pct", "variant", "final_equity",
                  "net_usd", "return_pct", "per_day_pct", "max_dd_pct", "trades", "win_rate", "avg_r",
                  "max_stop_liq_frac", "stop_exits", "target_exits", "refused"]


def summary_csv_rows(results: Dict[str, Any], start: float = START) -> List[Dict[str, Any]]:
    out = []
    for ds, r in results.items():
        for v, s in r["variants"].items():
            exits = s.get("exits") or {}
            out.append({"dataset": ds, "window_start": r["window"][0], "window_end": r["window"][1], "days": r["days"],
                        "gold_move_pct": r["gold_move_pct"], "variant": v, "final_equity": s["final_equity"],
                        "net_usd": round(s["final_equity"] - start, 2), "return_pct": s["return_pct"],
                        "per_day_pct": s["per_day_pct"], "max_dd_pct": s["max_dd_pct"], "trades": s["trades"],
                        "win_rate": s["win_rate"], "avg_r": s["avg_r"], "max_stop_liq_frac": s["max_stop_liq_frac"],
                        "stop_exits": exits.get("stop", 0), "target_exits": exits.get("target", 0),
                        "refused": json.dumps(s.get("refused") or {}, sort_keys=True)})
    return out


# ── main ─────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--coin", default=COIN)
    ap.add_argument("--replay-theses-dir", default="/tmp/thesis-replay-gold/theses")
    ap.add_argument("--metals-theses-dir", default=os.environ.get(
        "PATHIEL_METALS_THESES_DIR", os.path.expanduser("~/Documents/code/pathia/.state/strategies/metals/theses")),
        help="READ ONLY: the live metals journal's theses (same_market keeps the gold ones)")
    ap.add_argument("--gold-max-leverage", type=float, default=DEFAULT_GOLD_MAX_LEVERAGE)
    ap.add_argument("--risk-cap-frac", type=float, default=LIVE_RISK_CAP_FRAC)
    ap.add_argument("--out-dir", default="/tmp/gold-thesis-replay")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    log_path = os.path.join(a.out_dir, "progress.log")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} gold-thesis-replay: {msg}"
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    snap = SNAP.load_snapshot(a.snapshot_dir, a.coin)
    c15, c1h, funding = snap.c15, snap.c1h, snap.funding
    if not c15 or not c1h:
        raise SystemExit(f"snapshot at {a.snapshot_dir!r} has no {a.coin} candles -- run snapshot.py first")
    b15_start, b15_end = snap.window("15m")
    log(f"snapshot {a.snapshot_dir}: 15m {snap.window_iso('15m')}, 1h {snap.window_iso('1h')}")

    atr_price, atr_pct = mean_atr(c1h)
    base = base_params(a.gold_max_leverage, atr_price or 10.0, a.risk_cap_frac)
    log(f"gold max leverage {a.gold_max_leverage:g}x, mean 1h ATR14 {atr_price} ({(atr_pct or 0) * 100:.3f}%), "
        f"risk cap {a.risk_cap_frac:.0%}, no_break_entry={base.no_break_entry}")
    named = variants()
    results: Dict[str, Any] = {}

    # replay: the replayed gold theses
    replay_theses = D.load_journaled_theses(a.replay_theses_dir)
    rw = replay_window(replay_theses, b15_start, b15_end)
    if rw is None:
        raise SystemExit(f"no replayed theses at {a.replay_theses_dir!r}")
    r_start, r_end = rw
    results["replay"] = run_variants(c15, c1h, journaled_direction(replay_theses), base, r_start, r_end, named,
                                     funding)
    hits = {f"{h:g}d": direction_hit_rate(replay_theses, c1h, h) for h in HIT_HORIZONS_DAYS}
    log(f"replay: {len(replay_theses)} theses {side_counts(replay_theses)}, window {_iso(r_start)}..{_iso(r_end)}, "
        f"direction hit rate {hits}")

    # controls and buy-and-hold on the SAME window
    for s in CONTROLS:
        results[s] = run_variants(c15, c1h, direction_series(s, c1h), base, r_start, r_end, named, funding)
    hold = buy_hold_row(c15, r_start, r_end)
    log(f"controls {CONTROLS} and buy-and-hold ({hold}) over the replay window")

    # the one journaled gold thesis, for its own horizon
    raw = D.load_journaled_theses(a.metals_theses_dir)
    gold_theses = same_market(raw, c1h)
    jw = journal_window(gold_theses, b15_end)
    journal_calls = [{"thesis_id": th.thesis_id, "created_at": _iso(th.created_at_ms), "side": th.side,
                      "entry_px": th.entry_px} for th in gold_theses]
    if jw and any(th.side in ("long", "short") for th in gold_theses):
        j_start, j_end = max(jw[0], b15_start), jw[1]
        results["journal_gold"] = run_variants(c15, c1h, journaled_direction(gold_theses), base, j_start, j_end,
                                              named, funding)
        log(f"journal_gold: {len(gold_theses)} gold theses of {len(raw)}, window {_iso(j_start)}..{_iso(j_end)}")
    else:
        log(f"journal_gold: no gold theses with a side at {a.metals_theses_dir!r}, skipped")

    verdict = activation_verdict(results)
    log(f"verdict: activate={verdict['activate']} fails={verdict['fails']} ({time.time() - t0:.0f}s)")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "snapshot_dir": a.snapshot_dir, "coin": a.coin,
        "gold_max_leverage": a.gold_max_leverage, "risk_cap_frac": a.risk_cap_frac, "mean_atr14_1h": atr_price,
        "mean_atr14_1h_pct": atr_pct, "snapshot_window": {iv: snap.window_iso(iv) for iv in SNAP.INTERVALS},
        "live": LIVE, "replay_theses_dir": a.replay_theses_dir, "replay_theses": len(replay_theses),
        "replay_sides": side_counts(replay_theses), "replay_direction_hit_rate": hits,
        "replay_calls": [{"thesis_id": th.thesis_id, "created_at": _iso(th.created_at_ms), "side": th.side,
                          "entry_px": th.entry_px, "stop_px": th.stop_px, "confidence": th.confidence}
                         for th in replay_theses],
        "journal_calls": journal_calls, "buy_hold": hold, "results": results, "verdict": verdict,
        "variants": named,
    }
    with open(os.path.join(a.out_dir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    with open(os.path.join(a.out_dir, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(summary_csv_rows(results))
    log(f"done in {time.time() - t0:.0f}s -> {a.out_dir}/report.json, summary.csv")
    _print(report)
    return 0


def _print(rep: Dict[str, Any]) -> None:
    v = rep["verdict"]
    print(f"\nlive {rep['live']}; activate: {v['activate']}; fails: {v['fails'] or 'none'}")
    print(f"buy-and-hold over the replay window: {rep['buy_hold']}")
    print("per dataset (variant: net $ / return % / trades / win rate / max DD):")
    for ds, r in rep["results"].items():
        print(f"  {ds} {r['window'][0]}..{r['window'][1]} gold {r['gold_move_pct']:+}%")
        for name, s in r["variants"].items():
            wr = f"{s['win_rate']:.2f}" if s["win_rate"] is not None else "  -"
            print(f"     {name:16s} {s['final_equity'] - START:+8.2f} {s['return_pct']:+7.1f}% {s['trades']:4d} {wr} "
                  f"{s['max_dd_pct']:5.1f}")


if __name__ == "__main__":
    raise SystemExit(main())
