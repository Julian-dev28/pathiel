#!/usr/bin/env python3
"""Build a MARGINAL-VOLUME candle dataset to test lowering the liquidity floors.

The main dataset is the top-40 liquid coins (all >> the floors), so it says nothing
about whether LOW-volume coins are tradeable. This samples coins across volume BANDS
spanning below/around the floors, native + HIP-3, tagged with each coin's dayNtlVlm,
so the swarm can measure EV by liquidity band (net of band-appropriate slippage).

Bands (USDC 24h volume): the marginal range the floors live in.
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path

_REPO = Path("/Users/julian_dev/Documents/code/hermes-trader")
sys.path.insert(0, str(_REPO))
for _line in (_REPO / ".env.local").read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        k, _, v = _line.partition("="); os.environ.setdefault(k.strip(), v.strip())

from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles

OUT = Path(__file__).resolve().parent / "marginal_dataset.json"
LOG = Path("/tmp/marginal-ds/progress.log"); LOG.parent.mkdir(parents=True, exist_ok=True)
def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"; print(line, flush=True)
    LOG.open("a").write(line + "\n")

# volume bands (lo, hi, label, per-band sample cap)
BANDS = [
    (100_000, 700_000, "0.1-0.7M", 16),     # BELOW the 700k long floor
    (700_000, 2_000_000, "0.7-2M", 16),      # just above long floor
    (2_000_000, 5_000_000, "2-5M", 14),
    (5_000_000, 20_000_000, "5-20M", 14),    # below short floor
    (20_000_000, 50_000_000, "20-50M", 12),  # below short floor (where rally-exh's $20M lives)
    (50_000_000, 1e15, "50M+", 10),          # ABOVE both floors (liquid reference)
]
INTERVALS = {"1d": 250, "1h": 1200}

def band_of(v):
    for lo, hi, label, _ in BANDS:
        if lo <= v < hi:
            return label
    return None

def main():
    log("MARGINAL DS start — fetching universe (native + HIP-3)")
    uni = get_universe(force_refresh=True, include_hip3=True)
    # split native vs hip3; keep only tradeable perps
    def pick(rows, kind):
        rows = sorted(rows, key=lambda u: u.get("dayNtlVlm", 0), reverse=True)
        per_band = {b[2]: [] for b in BANDS}
        for u in rows:
            v = float(u.get("dayNtlVlm", 0) or 0)
            b = band_of(v)
            coin = u.get("coin") or ""
            if not b or not coin or coin.startswith("@"):
                continue
            cap = next(x[3] for x in BANDS if x[2] == b)
            if len(per_band[b]) < cap:
                per_band[b].append(u)
        out = [u for lst in per_band.values() for u in lst]
        log(f"{kind}: " + " ".join(f"{b}={len(per_band[b])}" for b in per_band))
        return out

    native = pick([u for u in uni if u.get("type") == "perp" and u.get("dex") is None], "native")
    hip3 = pick([u for u in uni if u.get("dex") is not None], "hip3")
    selected = native + hip3
    log(f"selected {len(selected)} coins ({len(native)} native + {len(hip3)} hip3)")

    data = {"meta": {"built": time.time(), "bands": [b[2] for b in BANDS], "intervals": INTERVALS,
                     "note": "marginal-volume; survivorship worse here (low-vol coins die more) — treat positives skeptically"},
            "universe": {}, "candles": {}}
    total = len(selected); done = 0
    for u in selected:
        coin = u["coin"]
        data["universe"][coin] = {"dayNtlVlm": float(u.get("dayNtlVlm", 0) or 0),
                                  "band": band_of(float(u.get("dayNtlVlm", 0) or 0)),
                                  "dex": u.get("dex"), "type": u.get("type"),
                                  "maxLeverage": u.get("maxLeverage")}
        data["candles"][coin] = {}
        for iv, cnt in INTERVALS.items():
            try:
                cs = fetch_hl_candles(coin, iv, cnt)
                data["candles"][coin][iv] = [[c.t, c.o, c.h, c.l, c.c, c.v] for c in cs]
            except Exception as e:
                data["candles"][coin][iv] = []; log(f"  WARN {coin} {iv}: {e}")
            time.sleep(0.12)
        done += 1
        n1d = len(data["candles"][coin].get("1d", []))
        if done % 10 == 0 or n1d < 30:
            log(f"  {coin} [{data['universe'][coin]['band']}] 1d={n1d} [{done}/{total}]")
    OUT.write_text(json.dumps(data))
    log(f"MARGINAL DS done — {OUT} ({OUT.stat().st_size/1e6:.1f} MB), {total} coins")

if __name__ == "__main__":
    main()
