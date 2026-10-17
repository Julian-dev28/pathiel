# HIP-3 /info fixtures

Real responses from `https://api.hyperliquid.xyz/info`, recorded 2026-10-17,
trimmed to a handful of rows each (always including `xyz:CL` / `BTC` so the
HIP-3 regression tests have a fixed, known coin to assert against). Not
fabricated: every value is a real field from a real response, just sliced
down to keep the fixture small.

| file | request | what it proves |
|---|---|---|
| `metaAndAssetCtxs_dex_xyz.json` | `{"type":"metaAndAssetCtxs","dex":"xyz"}` | a dex-scoped call returns names ALREADY prefixed (`xyz:CL`, not bare `CL`) |
| `metaAndAssetCtxs_main.json` | `{"type":"metaAndAssetCtxs"}` | main-dex call has no HIP-3 coins at all |
| `allMids_dex_xyz.json` | `{"type":"allMids","dex":"xyz"}` | HIP-3 mids only show up with an explicit `dex` |
| `allMids_main.json` | `{"type":"allMids"}` | bare `allMids` has zero HIP-3 coverage (`xyz:CL` absent) |
| `predictedFundings_main.json` | `{"type":"predictedFundings"}` | no `dex` param support at all -- confirmed live that passing one changes nothing; no HIP-3 rows exist under any request shape |

Regenerate by re-running the same requests against the live `/info` endpoint
(read-only; see `docs/overhaul/SWARM_RULES.md` section 1) and re-trimming.
