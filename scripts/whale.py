#!/usr/bin/env python3
"""Free crypto whale-flow report (our Whale-Alert / UW "large trade" replacement).

Usage: python3 scripts/whale.py BTC
       python3 scripts/whale.py BTC ETH SOL --min 250000
"""
import sys
from hermes_trader.agents.crypto_whale import crypto_whale_signal


def main():
    min_usd = 100_000.0
    window = 15.0
    if "--min" in sys.argv:
        min_usd = float(sys.argv[sys.argv.index("--min") + 1])
    if "--window" in sys.argv:
        window = float(sys.argv[sys.argv.index("--window") + 1])
    coins = [a for a in sys.argv[1:] if not a.startswith("--")
             and not a.replace(".", "").isdigit()]
    for coin in coins or ["BTC"]:
        r = crypto_whale_signal(coin, min_usd=min_usd, window_minutes=window)
        if not r:
            print(f"{coin}: no free whale data (xyz equity or Binance miss)")
            continue
        print(f"\n=== {coin}  ({r.symbol}) — whale prints >= ${r.min_usd:,.0f}, last {r.window_minutes:g}m ===")
        print(f"  scanned {r.window_n} trades | {r.whale_n} whale prints")
        print(f"  buy ${r.buy_usd:,.0f}  sell ${r.sell_usd:,.0f}  net ${r.net_usd:+,.0f}")
        print(f"  -> {r.bias}" + (f"  ({r.note})" if r.note else ""))


if __name__ == "__main__":
    main()
