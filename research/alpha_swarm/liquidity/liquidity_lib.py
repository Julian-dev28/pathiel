"""Shared substrate for the liquidity-floor swarm. Pairs with alpha_lib.

THE methodological crux is slippage-by-volume. The bot trades TINY ($20 short / up to
$200 long), so MARKET IMPACT is negligible even on a $0.5M-volume coin (a $200 order is
~0.04% of daily volume). The real cost of low liquidity is the BID-ASK SPREAD (wider for
illiquid perps) + worse signal quality. So we model a round-trip slippage that grows as
volume falls, and REQUIRE an edge to survive its own band's slippage before the floor can
drop to that band. Agents MUST sweep the slippage multiplier (sens. analysis) — the whole
question hinges on this assumption.

Prior evidence to beat (don't re-discover): lowering the LONG floor was −EV (extension
analysis: +EV band +0.15%@12bps dies by 25bps); the $50M SHORT floor held broadly, but
rally-exhaustion is +EV at $20M with a WIDE stop. So a credible "lower it" result must show
the edge survives the band's slippage AND OOS both halves AND isn't survivorship.
"""
from __future__ import annotations
import json, statistics
from pathlib import Path
from typing import Any

DATASET = Path(__file__).resolve().parent / "marginal_dataset.json"
T, O, H, L, C, V = 0, 1, 2, 3, 4, 5

# round-trip slippage (bps) by 24h USDC volume — conservative HL-perp spread estimates at the
# bot's small trade size (impact ~0, this is spread). Sweep `mult` for sensitivity.
_BANDS = [
    (50_000_000, 1e15, 6.0),
    (20_000_000, 50_000_000, 12.0),
    (5_000_000, 20_000_000, 25.0),
    (2_000_000, 5_000_000, 45.0),
    (700_000, 2_000_000, 70.0),
    (0, 700_000, 120.0),
]

def band_slippage_bps(volume: float, mult: float = 1.0) -> float:
    for lo, hi, bps in _BANDS:
        if lo <= volume < hi:
            return bps * mult
    return 120.0 * mult

def load(path: Path | str = DATASET) -> dict:
    d = json.loads(Path(path).read_text())
    d.setdefault("coins", list(d.get("candles", {}).keys()))
    return d

def candles(d: dict, coin: str, iv: str) -> list:
    return d["candles"].get(coin, {}).get(iv, []) or []

def vol(d: dict, coin: str) -> float:
    return float(d["universe"].get(coin, {}).get("dayNtlVlm", 0) or 0)

def band(d: dict, coin: str) -> str | None:
    return d["universe"].get(coin, {}).get("band")

def dex(d: dict, coin: str):
    return d["universe"].get(coin, {}).get("dex")

def coins_in_band(d: dict, label: str, native_only: bool = False, hip3_only: bool = False) -> list:
    out = []
    for c in d["coins"]:
        if band(d, c) != label:
            continue
        dx = dex(d, c)
        if native_only and dx is not None:
            continue
        if hip3_only and dx is None:
            continue
        out.append(c)
    return out

def pct(a, b):
    return (b - a) / a if a else 0.0

def summarize(rets: list, slip_frac: float) -> dict:
    """net EV after a fixed round-trip slippage fraction, + time-OOS halves (caller pre-sorts by time)."""
    if not rets:
        return {"n": 0}
    net = [r - slip_frac for r in rets]
    n = len(net); half = n // 2
    def ev(xs):
        return round(100 * statistics.mean(xs), 4) if xs else None
    return {"n": n, "mean_pct": ev(net), "win": round(sum(1 for x in net if x > 0) / n, 3),
            "oos_h1": ev(net[:half]), "oos_h2": ev(net[half:])}
