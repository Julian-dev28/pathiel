# Lane E scoreboard — HIP-3 tokenized-equity cross-asset frontier (2026-07-09)

Data: W-E_dataset.json (built by hypotheses/W-E0_fetch.py) — 36 US-RTH xyz names >= $700k vol,
1h candles 2025-12-15..2026-07-09 (up to 5000 bars), 15m for 6 core names, hourly funding+premium
(120d) for the liquid subset. NOTE: W-E_dataset.json is ~10MB DATA — needs a .gitignore line
(research agents cannot edit .gitignore).

- ➖ `W-E1_weekend_gap_fade` — weekend xyz drift REVERTS at the Monday reopen (basket corr -0.34,
  n=31 weekends; fade +0.23%@25bps, OOS both halves +, momo is mirror-negative) but MC p_sign
  0.073-0.112 — MARGINAL, needs ~15 more weekends; weekend vol structurally LOW (0.4-0.6x RTH).
- ➖ `W-E2_open_reversal` — weekday overnight FADE refuted; **BTC-residualized overnight MOMENTUM
  (idio after-hours drift continues through the session) is the lane's best cell**: n=87 day-episodes,
  +0.29%@12bps / +0.16%@25bps, OOS +0.24/+0.33, p_sign 0.031 AND p_pool 0.035 — MARGINAL,
  shadow-wire candidate (spec in findings/W-E2_open_reversal.md).
- ❌ `W-E3_offhours_leadlag` — closed-hours BTC lead is REAL (+0.057%/1h, p=0.0000) and DEAD by
  6bps; co-move is 87% contemporaneous. Closes the loop on stock_crypto_leadlag.md (active bars):
  no laggable window exists, open or shut.
- ❌ `W-E4_premium_dislocation` — mark-vs-oracle premium fade negative in all 9 cells AT ZERO COST
  (oracle converges to the perp, not vice versa); momo dead too (best of 18 cells p=0.11).
  Stale-oracle arb on xyz does not exist; |premium| p99 is only ~47bps anyway.

Lane read: the 24/7-vs-RTH mismatch is real STRUCTURE (low weekend vol, instant crypto co-move,
information-bearing premium) but only two thin directional edges survive costs, both MARGINAL.
Frontier here = forward shadow-grading, not more backtest slicing.
