# memecheck

Red-flag report for a pump.fun / Solana memecoin. Read only, no keys, never touches the
Hyperliquid account.

```
.venv/bin/python -m services.memecheck https://pump.fun/coin/<mint>
.venv/bin/python -m services.memecheck <mint> --json          # machine-readable
.venv/bin/python -m services.memecheck <mint> --save /tmp/x   # keep raw API responses
```

## Sources

| Source | Gives |
|---|---|
| DexScreener `latest/dex/tokens/<mint>` | pairs, price, mc, liquidity, volume, txns, socials |
| RugCheck `tokens/<mint>/report` | authorities, LP lock, holders, creator balance, insider networks |
| RugCheck `tokens/<mint>/insiders/graph` | per-wallet insider holdings (split-buy detection) |
| DexScreener on the quote token | USD price when the pair is quoted in something other than SOL/USDC |

pump.fun's own API sits behind Cloudflare and is not used.

## Flags

| Flag | Fires when |
|---|---|
| `fast_graduation` | bonding curve to AMM pool in under 300s (bundle / sniper launch) |
| `thin_liquidity` | pool USD under 20% of market cap |
| `sellers_match_buyers` | last 5 min sells >= buys |
| `exotic_quote` | quote asset not SOL/WSOL/USDC/USDT (e.g. a tokenized stock) |
| `no_usd_price` | no USD price from any source |
| `mint_authority` / `freeze_authority` / `mutable_metadata` / `token_extension` / `lp_unlocked` / `rugged` | contract controls still live |
| `creator_out` | creator wallet balance is 0 |
| `concentrated` / `whale` | top 10 real wallets > 30%, or one wallet > 10% (pool and burn excluded) |
| `insider_networks` | RugCheck found linked wallets |
| `split_buy` | 2+ insider wallets hold the exact same token amount |
| `no_description` / `no_website` | empty metadata |

Thresholds live at the top of `memecheck.py`.

## Tests

`analyze()` is pure: three JSON blobs + a clock in, report out. Gate tests replay frozen
responses for HUSS (graduated, bundled launch) and FRIES (on the curve, quoted in SQQQ):

```
.venv/bin/python -m pytest services/memecheck            # gate, offline, <1s
.venv/bin/python -m pytest services/memecheck -m online  # live API shape check
```

No eval suite: there is no LLM call in this service. The judgment ("would I touch this")
stays with the reader; the script only states facts and fires flags.
