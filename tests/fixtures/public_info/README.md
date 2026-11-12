# Testnet /info fixtures for the public-install MCP tests

Real responses from `https://api.hyperliquid-testnet.xyz/info`, recorded
2026-11-12, trimmed so the folder stays small. Each file is
`{"request": <the payload sent>, "response": <the body, trimmed>}`.
Read only: no /exchange call, no mainnet call.

Used by the offline harness in `tests/test_eval_public_tiers.py`
(`FakeVenue`), which answers every /info read by payload type from here.
`tests/test_mcp_public_mode.py` and `tests/test_mcp_operator_unchanged.py`
load that harness.

| file | trimmed to | what it proves |
|---|---|---|
| `perpDexs.json` | null (main), `test`, `xyz`; long lists cut to 3 | testnet registers `xyz` (278 dexes in total that day) |
| `meta_main.json`, `metaAndAssetCtxs_main.json` | BTC, ETH, SOL, MATIC | MATIC carries `isDelisted` on testnet |
| `meta_xyz.json`, `metaAndAssetCtxs_xyz.json` | xyz:CL, xyz:GOLD, xyz:NVDA, xyz:BRENTOIL | **xyz:BRENTOIL is delisted on testnet** (the oil desk's coin) |
| `allMids_main.json`, `allMids_xyz.json` | the same coins | mids per dex |
| `predictedFundings.json` | BTC, ETH, SOL, MATIC | |
| `l2Book.json` | BTC, 5 levels a side | the harness scales it to other coins' mids |
| `candleSnapshot.json` | BTC 1h, last 30 | the harness renames `s` to the coin asked |
| `spotMeta.json`, `spotMetaAndAssetCtxs.json` | first 3 spot pairs | |
| `fundingHistory.json` | BTC, last 24 | |
| `clearinghouseState.json` and the other user reads | user `0x00...01`, histories cut to the last few rows | real account shapes; the harness injects positions and resting orders per test |

Regenerate (read only, testnet):

```
.venv/bin/python <recorder> tests/fixtures/public_info
```

The recorder used is a short script that posts each `request` above to the
testnet `/info` and applies the trims in this table; re-trim by hand if the
venue adds fields.
