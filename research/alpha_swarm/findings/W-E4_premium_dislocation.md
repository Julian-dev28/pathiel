# W-E4 — xyz mark-vs-oracle premium dislocation

**Hypothesis:** on xyz tokenized equities the oracle goes stale nights/weekends while the 24/7 perp keeps trading, so hourly premium (mark vs oracle, from fundingHistory) extremes should mean-revert tradeably in price space — fade the dislocated side.

**Rule tested:** funding rows [t, rate, premium] hourly, 120d, coins with full coverage at run time: xyz:XYZ100, xyz:MU, xyz:SP500, xyz:SNDK (the 4 most liquid US-RTH names; repair fetch for 8 more was still paging — see W-E0_fetch.py --funding-only). Episode = threshold crossing of |premium| at data-derived quantiles (p90=14bps, p97.5=31bps, p99=47bps), re-arm below thr/2, 12h per-coin cooldown, fill next 1h open, hold {4,8,24}h, FUNDING ACCRUAL NETTED into the trade, both directions tested (fade AND momo), RTH/closed split, stop sweep. Script: hypotheses/W-E4_premium_dislocation.py.

**Distribution first:** xyz |premium| is SMALL — p50 4bps, p90 14bps, p99 47bps, max ~105bps. The dislocation you'd want to arb is about the size of one round-trip cost.

## Results (episodes, funding netted)

FADE (short rich / long cheap): negative in ALL 9 cells (thr x hold), **even at 0bps**:

| thr | hold | n | 0bps | 12bps | 25bps | p_sign | OOS @12bps |
|---|---|---|---|---|---|---|---|
| p90 | 4h | 254 | -0.10% | -0.22% | -0.35% | 0.81 | -0.13 / -0.32 |
| p90 | 8h | 254 | -0.28% | -0.40% | -0.53% | 0.98 | -0.25 / -0.56 |
| p97.5 | 8h | 94 | -0.35% | -0.47% | -0.60% | 0.94 | -0.45 / -0.49 |
| p99 | 4-24h | 47-57 | -0.0..-0.6% | negative | negative | 0.41-0.92 | mixed neg |

Stops don't rescue (8-40% sweep all negative). ~95% of episodes trigger during CLOSED hours (as the stale-oracle story predicts) and the closed-only split is just as negative.

MOMO (follow the premium): dead too — best cell (p90/24h) +0.23% @12bps, +0.10% @25bps, p_sign 0.108, sharpe-like 0.02; every other cell negative or sign-flipped. One weak cell out of 18 = noise.

**Coverage-stability check:** rerun at 6-name coverage (+SPCX, +NVDA) — the fade cells CHANGE SIGN with universe composition (p97.5/8h went -0.47% -> +0.02% @12bps; p99/8h flipped to a nominal pass H1 +0.61/H2 +0.07, p_sign 0.045, while its 4h and 24h neighbors still sign-flip). A "signal" that inverts when you add two names is sampling noise, not structure. Momo stayed dead everywhere.

**VERDICT: REFUTED (both directions).** The deciding numbers: no (direction x thr x hold) cell passes EV@25bps + OOS-both-halves + p<0.05 together under EITHER universe; the 4-name run had fade negative at ZERO cost in all 9 cells, and the 6-name rerun's single nominal pass (1/18 cells) is unstable across coverage and thresholds. Structural insight worth keeping: on tokenized equities the premium is mostly INFORMATION, not dislocation — the perp prices after-hours news and the ORACLE converges to the PERP (at the reopen), not the perp to the oracle. "Stale-oracle arbitrage" on xyz does not exist at hourly granularity, and at p99 ~45bps the gap is barely one round-trip anyway. Consistent with W-E2 (idiosyncratic overnight drift continues) and with the live premium_fade_short book being a CRYPTO-crowding edge, not transferable to HIP-3 equities.

Caveat: 6-name coverage (most-liquid subset); `W-E0_fetch.py --funding-only` completes the rest if a third pass is ever wanted.
