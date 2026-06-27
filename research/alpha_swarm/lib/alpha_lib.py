"""Shared substrate for the alpha-hunt swarm.

Every agent imports this so all hypotheses are validated identically:
lookahead-safe entry/exit, OOS both-halves, tier-slippage cost sweep,
PIT-survivorship awareness. Written once, constrains every agent forever.

DATA SHAPE: load_dataset() -> dict
  d["coins"]            -> list[str]
  d["candles"][coin][iv] -> list[[t,o,h,l,c,v]]  (iv in 1d/1h/5m)
  d["universe"][coin]  -> {dayNtlVlm, openInterest, maxLeverage, funding, prevDayPx}

RULES (do not violate — these are the gates that killed prior false positives):
  1. NEVER use bar i's close to decide an entry you fill at bar i's close.
     Decide on bars [..i], fill at bar i+1 open (or i close as a documented
     approximation only for slow daily signals).
  2. Report EV at multiple slippage tiers. An edge that dies by 25bps is not an edge.
  3. Split the sample by TIME into first/second half. Report BOTH. A sign flip
     across halves = noise, not alpha.
  4. The universe is survivor-biased (today's liquid set). Positive = upper bound.
  5. For mean-reversion/squeeze edges, SWEEP stop width {8,15,20,25,40}% — a tight
     stop anchored to the live DSL banks the squeeze and inverts a real edge.
"""
from __future__ import annotations
import json, os, statistics
from pathlib import Path
from typing import Any, Callable

# Resolve the dataset: env override wins, else the candle file next to this package
# (research/alpha_swarm/dataset.json — gitignored 17MB data, rebuild with lib/build_dataset.py).
DATASET = Path(os.environ.get(
    "HERMES_ALPHA_DATASET",
    Path(__file__).resolve().parent.parent / "dataset.json",
))

def load_dataset(path: Path | str = DATASET) -> dict:
    d = json.loads(Path(path).read_text())
    d.setdefault("coins", d.get("meta", {}).get("coins", list(d.get("candles", {}).keys())))
    return d

def candles(d: dict, coin: str, iv: str) -> list[list[float]]:
    return d["candles"].get(coin, {}).get(iv, []) or []

# OHLCV index helpers
T, O, H, L, C, V = 0, 1, 2, 3, 4, 5

def pct(a: float, b: float) -> float:
    return (b - a) / a if a else 0.0

def time_split(trades: list[dict], key: str = "t") -> tuple[list, list]:
    """Split trades into first/second TIME half by entry timestamp."""
    if not trades:
        return [], []
    ts = sorted(t[key] for t in trades)
    mid = ts[len(ts) // 2]
    first = [t for t in trades if t[key] <= mid]
    second = [t for t in trades if t[key] > mid]
    return first, second

# slippage tiers in basis points (round-trip cost applied to each trade return)
SLIP_TIERS_BPS = [0, 6, 12, 25, 50]

def summarize(trades: list[dict], ret_key: str = "ret") -> dict:
    """trades: list of {t: entry_ms, ret: gross fractional return (signed for side)}.
    Returns EV per slippage tier + OOS-both-halves verdict."""
    if not trades:
        return {"n": 0, "verdict": "NO TRADES"}
    rets = [t[ret_key] for t in trades]
    n = len(rets)
    out: dict[str, Any] = {"n": n}
    for bps in SLIP_TIERS_BPS:
        cost = bps / 10000.0
        net = [r - cost for r in rets]
        mean = statistics.mean(net)
        wins = sum(1 for r in net if r > 0)
        out[f"slip{bps}"] = {
            "mean_ret_pct": round(100 * mean, 4),
            "total_pct": round(100 * sum(net), 2),
            "win_rate": round(wins / n, 3),
            "sharpe_like": round(mean / (statistics.pstdev(net) + 1e-9), 3),
        }
    # OOS both halves at 12bps (the realistic-ish tier)
    first, second = time_split(trades)
    def _ev(ts):
        if not ts: return None
        net = [t[ret_key] - 0.0012 for t in ts]
        return round(100 * statistics.mean(net), 4)
    h1, h2 = _ev(first), _ev(second)
    out["oos_12bps"] = {"first_half_mean_pct": h1, "second_half_mean_pct": h2,
                        "n_first": len(first), "n_second": len(second)}
    robust = (h1 is not None and h2 is not None and h1 > 0 and h2 > 0)
    out["verdict"] = "ROBUST +EV both halves @12bps" if robust else \
                     ("SIGN-FLIP / one-sided (noise)" if (h1 is not None and h2 is not None) else "thin sample")
    return out

def sweep_stop(entry_px: float, side: str, fwd: list[list[float]],
               stop_pcts: list[float], horizon: int,
               tp_pct: float | None = None) -> dict[float, float]:
    """Walk forward bars, return {stop_pct: realized_fractional_return} for each
    stop width. side in {long,short}. Exits on stop, optional TP, or horizon end.
    Lookahead-safe: only uses bars strictly AFTER entry."""
    out: dict[float, float] = {}
    sign = 1.0 if side == "long" else -1.0
    for sp in stop_pcts:
        stop_px = entry_px * (1 - sp) if side == "long" else entry_px * (1 + sp)
        tp_px = None
        if tp_pct is not None:
            tp_px = entry_px * (1 + tp_pct) if side == "long" else entry_px * (1 - tp_pct)
        ret = None
        for bar in fwd[:horizon]:
            hi, lo, cl = bar[H], bar[L], bar[C]
            if side == "long":
                if lo <= stop_px:
                    ret = pct(entry_px, stop_px); break
                if tp_px and hi >= tp_px:
                    ret = pct(entry_px, tp_px); break
            else:
                if hi >= stop_px:
                    ret = sign * pct(entry_px, stop_px); break
                if tp_px and lo <= tp_px:
                    ret = sign * pct(entry_px, tp_px); break
        if ret is None:
            last = fwd[min(horizon, len(fwd)) - 1][C] if fwd else entry_px
            ret = sign * pct(entry_px, last)
        out[sp] = ret
    return out
