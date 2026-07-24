"""Backtest on RESOLVED Polymarket markets — the null the LLM lane must beat.

What can honestly be backtested here and what cannot:

  CAN  — everything mechanical: is the market's own price calibrated? are
         favorites underpriced (the reversed longshot bias W-Z1 cites)? does a
         hard 24h repricing (the BREAKING feed) continue or revert?
  CANNOT — the LLM's forecast itself. Any model asked today about a market that
         resolved in the past has the outcome in its weights or one search away.
         A "backtested LLM edge" on resolved markets is leakage, full stop. The
         LLM lane earns its number FORWARD, on the shadow ledger, against the
         numbers this file produces.

Method (no lookahead): for each resolved market, pull the CLOB price series for
the YES token, take `t_end` = last traded bar, and read the price at t_end − H.
`price_at` only ever looks BACKWARD from the sample point (last bar at or before
t), so no bar after the decision time can leak in. Every PnL is net of a fee +
slippage proxy applied against us on both legs.

    python -m services.polymarket_scout.backtest --limit 400
    python -m services.polymarket_scout.backtest --json    # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.polymarket_scout.ledger import _state_dir
from services.polymarket_scout.scout import FEE_PER_FILL, PolymarketClient, market_yes_prob

HOUR = 3600
DAY = 24 * HOUR
# Price series are last-trade marks, so a real fill is worse than the printed
# price. 1c of adverse slippage per side on top of the fee proxy keeps every
# number below pessimistic, never above optimistic.
SLIPPAGE = 0.01
BUCKETS: Tuple[float, ...] = (0.0, 0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1.0)


# ── pure math ────────────────────────────────────────────────────────────────
def price_at(history: Sequence[Dict[str, float]], t: float) -> Optional[float]:
    """Last printed price at or before `t`. Backward-only: this is the no-lookahead
    guarantee the whole file rests on. History must be ascending in `t`."""
    out: Optional[float] = None
    for pt in history:
        try:
            ts = float(pt["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if ts > t:
            break
        out = float(pt["p"])
    return out


def net_pnl(entry_px: float, won: bool, fee: float = FEE_PER_FILL,
            slip: float = SLIPPAGE) -> float:
    """PnL per $1 staked on one side bought at `entry_px`, net of the fee proxy
    (charged on the fill, and again on redemption when the side wins) and of
    adverse slippage on entry."""
    px = min(0.99, entry_px + slip)
    gross = (1.0 - px) if won else -px
    fees = fee + (fee if won else 0.0)
    return gross - fees


def bucket_of(p: float, edges: Sequence[float] = BUCKETS) -> str:
    for lo, hi in zip(edges, edges[1:]):
        if lo <= p < hi:
            return f"{lo:.2f}-{hi:.2f}"
    return f"{edges[-2]:.2f}-{edges[-1]:.2f}"


def calibration(pairs: Sequence[Tuple[float, bool]],
                edges: Sequence[float] = BUCKETS) -> List[Dict[str, Any]]:
    """(price, yes_won) -> per-bucket realized YES rate + the EV of buying YES
    and of buying NO at that price. `lift` = realized − priced; positive means
    the bucket resolved YES MORE often than it was priced (underpriced YES)."""
    agg: Dict[str, Dict[str, Any]] = {}
    for p, won in pairs:
        b = agg.setdefault(bucket_of(p, edges), {"n": 0, "yes": 0, "px": [],
                                                 "ev_yes": [], "ev_no": []})
        b["n"] += 1
        b["yes"] += 1 if won else 0
        b["px"].append(p)
        b["ev_yes"].append(net_pnl(p, won))
        b["ev_no"].append(net_pnl(1.0 - p, not won))
    out = []
    for name in sorted(agg):
        b = agg[name]
        out.append({
            "bucket": name, "n": b["n"],
            "priced": round(sum(b["px"]) / b["n"], 4),
            "realized": round(b["yes"] / b["n"], 4),
            "lift": round(b["yes"] / b["n"] - sum(b["px"]) / b["n"], 4),
            "ev_buy_yes": round(sum(b["ev_yes"]) / b["n"], 4),
            "ev_buy_no": round(sum(b["ev_no"]) / b["n"], 4),
        })
    return out


def _stats(xs: Sequence[float]) -> Dict[str, Any]:
    n = len(xs)
    if n == 0:
        return {"n": 0}
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    sd = var ** 0.5
    return {"n": n, "mean": round(mean, 4),
            "t": round(mean / (sd / (n ** 0.5)), 2) if sd > 0 and n > 1 else None,
            "win_rate": round(sum(1 for x in xs if x > 0) / n, 3)}


def move_study(series: Sequence[Dict[str, Any]], threshold: float = 0.05,
               horizon_s: int = DAY, seed: int = 7) -> Dict[str, Any]:
    """The BREAKING question: after a hard 24h repricing, does the market keep
    going or give it back?

    Each element of `series` is {history, yes_won}. Sample points are spaced one
    horizon apart, skipping the final horizon before settlement (the last day is
    dominated by resolution drift, not by news). At each point:
        prior = p(t) - p(t-H);  fwd = p(t+H) - p(t)
    Rows with |prior| >= threshold are the BREAKING set. `momentum` buys the side
    the move went toward, `fade` buys the other side; both are held to
    resolution and netted. `null` picks the side by coin flip at the same prices
    and the same points — the matched baseline both rules must beat.
    """
    rng = random.Random(seed)
    fwd_same: List[float] = []
    ev_mom: List[float] = []
    ev_fade: List[float] = []
    ev_null: List[float] = []
    ev_all: List[float] = []            # unconditional: buy YES at every sample point
    n_points = 0
    for row in series:
        hist = row.get("history") or []
        won = bool(row.get("yes_won"))
        if len(hist) < 3:
            continue
        t0, t_end = float(hist[0]["t"]), float(hist[-1]["t"])
        t = t0 + horizon_s
        while t <= t_end - horizon_s:
            p_prev, p_now, p_fwd = (price_at(hist, t - horizon_s), price_at(hist, t),
                                    price_at(hist, t + horizon_s))
            t += horizon_s
            if p_prev is None or p_now is None or p_fwd is None:
                continue
            n_points += 1
            ev_all.append(net_pnl(p_now, won))
            prior = p_now - p_prev
            if abs(prior) < threshold:
                continue
            # sign convention: a rising YES price = the news pushed toward YES
            fwd = p_fwd - p_now
            fwd_same.append(fwd if prior > 0 else -fwd)
            mom_yes = prior > 0
            ev_mom.append(net_pnl(p_now if mom_yes else 1.0 - p_now,
                                  won if mom_yes else not won))
            ev_fade.append(net_pnl((1.0 - p_now) if mom_yes else p_now,
                                   (not won) if mom_yes else won))
            coin_yes = rng.random() < 0.5
            ev_null.append(net_pnl(p_now if coin_yes else 1.0 - p_now,
                                   won if coin_yes else not won))
    return {
        "threshold": threshold, "horizon_h": horizon_s // HOUR,
        "sample_points": n_points,
        "fwd_move_same_direction": _stats(fwd_same),   # >0 mean = continuation
        "momentum_to_resolution": _stats(ev_mom),
        "fade_to_resolution": _stats(ev_fade),
        "matched_null_random_side": _stats(ev_null),
        "unconditional_buy_yes": _stats(ev_all),
    }


def horizon_study(series: Sequence[Dict[str, Any]],
                  horizons_h: Sequence[int] = (24, 72, 168)) -> Dict[str, Any]:
    """Calibration + mechanical-rule EV at fixed lead times before settlement."""
    out: Dict[str, Any] = {}
    for h in horizons_h:
        pairs: List[Tuple[float, bool]] = []
        for row in series:
            hist = row.get("history") or []
            if not hist:
                continue
            t_end = float(hist[-1]["t"])
            p = price_at(hist, t_end - h * HOUR)
            if p is None or float(hist[0]["t"]) > t_end - h * HOUR:
                continue                       # market younger than the horizon
            pairs.append((p, bool(row.get("yes_won"))))
        cal = calibration(pairs)
        fav = [net_pnl(p, w) for p, w in pairs if p >= 0.80]
        dog = [net_pnl(1.0 - p, not w) for p, w in pairs if p <= 0.20]
        out[f"T-{h}h"] = {"n": len(pairs), "calibration": cal,
                          "buy_favorites_p>=0.80": _stats(fav),
                          "fade_longshots_p<=0.20": _stats(dog),
                          "brier_market": round(
                              sum((p - (1.0 if w else 0.0)) ** 2 for p, w in pairs) / len(pairs), 4)
                          if pairs else None}
    return out


# ── data ─────────────────────────────────────────────────────────────────────
def _cache_path() -> str:
    return os.path.join(_state_dir(), "backtest_cache.json")


def resolved_outcome(m: Dict[str, Any]) -> Optional[bool]:
    """A settled binary market pins YES to ~1 or ~0. Anything in between is a
    void/50-50/disputed resolution and is dropped, not guessed."""
    p = market_yes_prob(m)
    if p is None:
        return None
    if p >= 0.99:
        return True
    if p <= 0.01:
        return False
    return None


# Sports/esports game lines resolve on a scoreboard, not on news synthesis, and
# they are the bulk of resolved volume. Kept in the sample (they are real
# evidence about market calibration) but reported as a separate subset, because
# the live lane excludes them (trending.DEFAULT_EXCLUDE_TAGS).
_SPORTS_MARKERS = (" vs ", " vs. ", "o/u", "total:", "moneyline", "spread:",
                   "game 1", "game 2", "game 3", "-ml", "will win the series")


def is_sports_like(question: str) -> bool:
    q = f" {(question or '').lower()} "
    return any(mk in q for mk in _SPORTS_MARKERS)


def build_sample(client: PolymarketClient, limit: int = 100, pages: int = 10,
                 min_volume: float = 20_000.0, max_age_days: float = 400.0,
                 use_cache: bool = True, progress: bool = True) -> List[Dict[str, Any]]:
    """Resolved markets + their YES price series. Cached to
    `.state/polymarket_scout/backtest_cache.json` so a rerun costs nothing.

    `max_age_days` is measured off the series' LAST bar (when trading actually
    stopped), not `endDate` — Gamma stamps placeholder end dates years out on
    plenty of settled markets, so endDate is not a usable clock.
    """
    cache: Dict[str, Any] = {}
    if use_cache and os.path.isfile(_cache_path()):
        try:
            cache = json.load(open(_cache_path()))
        except Exception:
            cache = {}
    markets = client.resolved_markets(limit=limit, pages=pages, min_volume=min_volume)
    now = time.time()
    series: List[Dict[str, Any]] = []
    fetched = 0
    for i, m in enumerate(markets):
        mid = str(m.get("id") or "")
        won = resolved_outcome(m)
        try:
            vol = float(m.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        if won is None or vol < min_volume or not mid:
            continue
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except Exception:
            continue
        if not isinstance(toks, list) or len(toks) != 2:
            continue
        if mid in cache:
            hist = cache[mid].get("history") or []
        else:
            hist = client.price_history(str(toks[0]))
            fetched += 1
            cache[mid] = {"history": hist}
            if progress and fetched % 25 == 0:
                print(f"[backtest] fetched {fetched} price series "
                      f"({i + 1}/{len(markets)} markets scanned, {len(series)} kept)",
                      flush=True)
        if len(hist) < 24:
            continue
        if (now - float(hist[-1]["t"])) > max_age_days * DAY:
            continue
        series.append({"market_id": mid, "question": m.get("question") or "",
                       "yes_won": won, "volume": vol, "history": hist,
                       "sports": is_sports_like(m.get("question") or "")})
    if use_cache:
        try:
            json.dump(cache, open(_cache_path(), "w"))
        except Exception:
            pass
    return series


def run(client: Optional[PolymarketClient] = None, limit: int = 100, pages: int = 10,
        min_volume: float = 20_000.0, max_age_days: float = 400.0,
        threshold: float = 0.05) -> Dict[str, Any]:
    client = client or PolymarketClient()
    t0 = time.time()
    series = build_sample(client, limit=limit, pages=pages, min_volume=min_volume,
                          max_age_days=max_age_days)
    judgment = [s for s in series if not s.get("sports")]
    report = {
        "generated_at": int(time.time()),
        "n_markets": len(series),
        "n_judgment": len(judgment),
        "min_volume": min_volume, "max_age_days": max_age_days,
        "elapsed_s": round(time.time() - t0, 1),
        "fee_per_fill": FEE_PER_FILL, "slippage": SLIPPAGE,
        "horizons": horizon_study(series),
        "horizons_judgment": horizon_study(judgment),
        "breaking": move_study(series, threshold=threshold),
        "breaking_judgment": move_study(judgment, threshold=threshold),
        "caveat": ("LLM forecasts are NOT backtested here — asking a model about a "
                   "resolved market leaks the outcome. These are the mechanical nulls "
                   "the forward LLM ledger must beat."),
    }
    return report


def _fmt_horizons(L: List[str], horizons: Dict[str, Any], label: str) -> None:
    for h, blk in horizons.items():
        L.append(f"\n## {label} · {h} before settlement   n={blk['n']}  "
                 f"brier_market={blk['brier_market']}")
        if not blk["n"]:
            continue
        L.append(f"{'bucket':<12}{'n':>6}{'priced':>9}{'realized':>10}{'lift':>8}"
                 f"{'ev_yes':>9}{'ev_no':>9}")
        for r in blk["calibration"]:
            L.append(f"{r['bucket']:<12}{r['n']:>6}{r['priced']:>9.3f}{r['realized']:>10.3f}"
                     f"{r['lift']:>+8.3f}{r['ev_buy_yes']:>+9.3f}{r['ev_buy_no']:>+9.3f}")
        for k in ("buy_favorites_p>=0.80", "fade_longshots_p<=0.20"):
            s = blk[k]
            if s.get("n"):
                L.append(f"  {k:<26} n={s['n']:<5} mean={s['mean']:+.4f}/$ "
                         f"win={s['win_rate']} t={s['t']}")


def _fmt_breaking(L: List[str], b: Dict[str, Any], label: str) -> None:
    L.append(f"\n## {label} BREAKING lane — |{b['horizon_h']}h move| >= "
             f"{b['threshold']:.0%}, {b['sample_points']} sample points")
    for k in ("fwd_move_same_direction", "momentum_to_resolution", "fade_to_resolution",
              "matched_null_random_side", "unconditional_buy_yes"):
        s = b[k]
        if s.get("n"):
            L.append(f"  {k:<28} n={s['n']:<6} mean={s['mean']:+.4f} "
                     f"win={s['win_rate']} t={s['t']}")


def _fmt(report: Dict[str, Any]) -> str:
    L = [f"# Polymarket backtest — {report['n_markets']} resolved markets "
         f"({report['n_judgment']} non-sports), vol>=${report['min_volume']:,.0f}, "
         f"fee {report['fee_per_fill']:.1%}/fill + {report['slippage']:.0%} slippage"]
    _fmt_horizons(L, report["horizons"], "ALL")
    _fmt_horizons(L, report["horizons_judgment"], "NON-SPORTS")
    _fmt_breaking(L, report["breaking"], "ALL")
    _fmt_breaking(L, report["breaking_judgment"], "NON-SPORTS")
    L.append(f"\n{report['caveat']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100, help="markets per page (Gamma caps at 100)")
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--min-volume", type=float, default=20_000.0)
    ap.add_argument("--max-age-days", type=float, default=400.0)
    ap.add_argument("--threshold", type=float, default=0.05, help="|24h move| for BREAKING")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="", help="also write the report JSON here")
    args = ap.parse_args()
    rep = run(limit=args.limit, pages=args.pages, min_volume=args.min_volume,
              max_age_days=args.max_age_days, threshold=args.threshold)
    print(json.dumps(rep, indent=1) if args.json else _fmt(rep))
    if args.out:
        json.dump(rep, open(args.out, "w"), indent=1)
        print(f"\n# wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
