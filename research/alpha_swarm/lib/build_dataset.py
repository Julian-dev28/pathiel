#!/usr/bin/env python3
"""Build ONE shared candle dataset for the alpha-hunt swarm.

Fetches a liquid PIT-current universe across 1d / 1h / 5m and writes a single
JSON the research agents read in cache-only mode. One fetch, many readers — no
429 storm. Survivorship caveat: the universe is TODAY's liquid set, so any
positive result is an UPPER BOUND (dead coins are absent).
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path

# repo root = three levels up: lib/ -> alpha_swarm/ -> research/ -> <repo>
_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.is_file():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, _, v = _line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles

# default output = research/alpha_swarm/dataset.json (gitignored), matching alpha_lib's resolver
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (Path(__file__).resolve().parent.parent / "dataset.json")
LOG = Path("/tmp/alpha-dataset/progress.log")
LOG.parent.mkdir(parents=True, exist_ok=True)

def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")

# top-N liquid NATIVE crypto perps (dex is None). Exclude HIP-3/stock noise.
N_COINS = int(os.environ.get("N_COINS", "40"))
INTERVALS = {"1d": 300, "1h": 2000, "5m": 6000}  # ~10mo daily / ~83d hourly / ~21d 5m

def main():
    log("DATASET BUILD start — fetching universe")
    uni = get_universe(force_refresh=True, include_hip3=False)
    perps = [u for u in uni if u.get("type") == "perp" and u.get("dex") is None]
    perps.sort(key=lambda u: u.get("dayNtlVlm", 0), reverse=True)
    coins = [u["coin"] for u in perps[:N_COINS]]
    log(f"universe: {len(coins)} coins — {', '.join(coins[:12])}...")

    data = {"meta": {"built": time.time(), "coins": coins,
                     "intervals": INTERVALS, "n_coins": N_COINS,
                     "survivorship": "PIT-current liquid set; positive results are UPPER BOUNDS"},
            "universe": {u["coin"]: {k: u.get(k) for k in
                         ("dayNtlVlm", "openInterest", "maxLeverage", "funding", "prevDayPx")}
                         for u in perps[:N_COINS]},
            "candles": {}}

    total = len(coins) * len(INTERVALS)
    done = 0
    for coin in coins:
        data["candles"][coin] = {}
        for iv, count in INTERVALS.items():
            try:
                cs = fetch_hl_candles(coin, iv, count)
                data["candles"][coin][iv] = [[c.t, c.o, c.h, c.l, c.c, c.v] for c in cs]
            except Exception as e:
                log(f"  WARN {coin} {iv}: {e}")
                data["candles"][coin][iv] = []
            done += 1
            time.sleep(0.15)
        n1d = len(data["candles"][coin].get("1d", []))
        log(f"  {coin}: 1d={n1d} 1h={len(data['candles'][coin].get('1h', []))} 5m={len(data['candles'][coin].get('5m', []))} [{done}/{total} {100*done//total}%]")

    OUT.write_text(json.dumps(data))
    mb = OUT.stat().st_size / 1e6
    log(f"DATASET BUILD done — {OUT} ({mb:.1f} MB), {len(coins)} coins")

if __name__ == "__main__":
    main()
