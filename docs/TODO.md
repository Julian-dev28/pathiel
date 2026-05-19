# TODO

## Tradfi / non-crypto perp coverage

`scan_once` selects the top-N markets by 24h volume (`HERMES_MAX_MARKETS`,
default 60). Hyperliquid volume is crypto-dominated, so non-crypto perps fall
below the cut and are never scanned — e.g. SPX (~rank 53), GAS (~165). PAXG
(~19) currently makes it; most others do not.

**Proposed fix:** a new *additive* config key — `scan_always_include: [...]` —
read by `scan_once` in `hermes_trader/agents/perception.py`, force-unioning the
listed markets into the scan set regardless of volume rank.

Not `coin_allowlist` — that is a *restrictive* execution-stage risk gate
("trade only these, block the rest"), the opposite mechanism, and it runs after
scan so it can never recover a market the scanner skipped.

**Open question to settle first:** do single-stock equity perps (TSLA / NVDA /
AAPL) and commodity perps (NATGAS / SILVER / COPPER) exist on a Hyperliquid
HIP-3 builder DEX? This scanner only queries native `metaAndAssetCtxs` (230
perps, ~all crypto). If equity perps live on a separate builder DEX, supporting
them is a larger change than the scan-list tweak. The README tagline currently
claims those markets — either implement the coverage or correct the tagline.
