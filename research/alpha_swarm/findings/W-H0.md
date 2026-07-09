# W-H0 — Lane H data substrate (extended 1h cache)

Lane H (intra-crypto lead-lag + cascade microstructure) needed more 1h history
than `dataset.json` carries (~83d, 2026-04-04..06-27): rare-event cells were
too thin (n=13 for -8% 1h flushes). Per the lane brief, fetched 1h x 5000 bars
per coin via the project's `fetch_hl_candles` (40 sequential candleSnapshot
requests + 0.35s spacing; 429-blanked coins retried in later passes).

- Script: `research/alpha_swarm/hypotheses/W-H0_fetch.py` (also exports the
  lane's shared lookahead-safe helpers: `hourly_rets`, `rolling_sigma` (strictly
  past), `rolling_beta` (strictly past), `fwd_open_ret` (next-open fill + gap
  check), `dedup_episodes`).
- Cache: session scratchpad `hourly_ext.json` — 40 coins, 1h,
  **2025-12-13 .. 2026-07-09** (~208 days, ~5000 bars/coin; IP 4760, LIT 4771).
  Each coin's final in-progress bar dropped.
- Validation: BTC closes on all 2000 overlapping completed timestamps match
  `dataset.json` exactly (`validate_against_dataset`, W-H0_fetch.py:71). The
  dataset's own final bar was partial at snapshot time (close 60324 vs completed
  60345) and is excluded from the check.
- Note: ~12 days of the cache post-date every prior swarm study's data window
  (dataset ends 06-27; cache ends 07-09), so Lane H's second OOS half contains
  genuinely fresh tape.
- Survivorship: same 40 TODAY-liquid coins as dataset.json — every positive
  result in W-H1..W-H4 is an upper bound.
