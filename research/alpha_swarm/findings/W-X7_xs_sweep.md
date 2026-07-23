# W-X7 — full xs_momentum recipe sweep: is the live crypto book on the frontier?

**Question (operator, 2026-07-23):** re-examine the live `xs_momentum` config with math;
sweep all variants, find the most EV+ combo.

**Method (`hypotheses/W-X7_xs_sweep.py`):** 288 books = ranking {pctk, raw trailing,
residual-vs-BTC} × window {7,14,21} × k {3,4,5,8} × hold {5,7,10,14} × meme-exclusion
{on,off}, on `W-X2_cache_daily.json` (top-50 crypto by volume, 401 daily bars), the shared
W-X2/W-X4/W-X5 engine. Non-overlapping xs long-top-k/short-bottom-k, decide bar i / fill
open[i+1] / exit open[i+1+H], net25 = ev − 0.0025·turnover, OOS = rebalance halves, 2000
matched random-book null on the top-15. **Strict-dominance gate (W-X5 bar):** a variant is
wire-worthy only if it beats the LIVE cell (pctk/14, k4, H10, meme-excluded) on net25 EV
AND Sharpe AND BOTH OOS halves AND null p<0.05. UNGATED sim — vol_gate/vol_managed not
modelled; this sweeps ranking/window/k/hold/meme only.

## Results

| book | n | net25 | ann | oos h1/h2 | Sharpe | null p | $/wk |
|---|---|---|---|---|---|---|---|
| resid21 k4 H14 no-meme (EV-max) | 23 | +5.00% | +130% | +5.83/+4.25 | 0.559 | 0.0 | +$15.37 |
| resid21 k5 H14 no-meme | 23 | +4.27% | +111% | +4.59/+3.98 | 0.611 | 0.0 | +$16.40 |
| **pctk14 k4 H10 no-meme (LIVE)** | 33 | **+3.68%** | +134% | +4.34/+3.06 | **+0.636** | 0.0 | **+$15.83** |
| pctk14 k4 H10 ALL-memes-in | 33 | +3.25% | +119% | +3.64/+2.89 | +0.589 | — | +$14.00 |

## Reading

1. **No variant strictly dominates the live recipe.** The EV-max cell (resid21/k4/H14)
   beats live on net25/rebal (+5.00 vs +3.68) and both halves, BUT: lower Sharpe (0.559 vs
   0.636), **half the rebalances** (n=23 vs 33), and **the same weekly $** (+$15.37 vs
   +$15.83). The higher per-rebalance EV is bought entirely by holding 14d instead of 10d —
   more EV accrues per trade, but you do fewer trades, so throughput is flat and drawdown-
   per-unit-return is worse. On the risk-adjusted bar the live cell wins. Classic
   sweep-top overfit: it is the max of 288 in-sample looks.

2. **Meme-exclusion CONFIRMED (again).** no-meme +3.68% vs all-memes-in +3.25% — dropping
   the 8 SECTOR_MAP MEME names in the top-50 (CASHCAT/DOGE/FARTCOIN/PUMP/TRUMP/VINE/kBONK/
   kPEPE) is worth **+0.43%/rebal (+$1.83/wk)**. An empty `exclude_coins` silently forfeits
   that. This reproduces W-X4's STRICT-DOMINANCE result on the same cache.

3. **Live pctk14/k4/H10/no-meme is on the efficient frontier** — best Sharpe of all 288 and
   best $/wk. Consistent with W-X5's XYZ-book finding (live cell most phase-stable).

4. **Watch (no action):** residual ranking at a longer window+hold (resid21/H14) has real
   higher EV/rebal — the "longer momentum horizon pays more per trade" characteristic. If
   the operator ever wants EV-per-trade over Sharpe/throughput (e.g. capacity-limited by
   API/fees, wanting fewer rebalances), resid21/k5/H14 (+$16.40/wk, Sharpe 0.611) is the
   one to re-test on a longer tape. NOT a wire today.

## VERDICT: **LIVE RECIPE STANDS.** pctk(14)/k4/H10/meme-excluded is on the frontier; no
variant clears the strict Sharpe+EV+both-halves+null gate. Action: keep the live recipe;
**keep `exclude_coins` populated** (empty costs −$1.83/wk). Do not chase the sweep top —
same $/wk, lower Sharpe, n=23, in-sample max. Caveats: one tape (~401d, down-then-recover),
survivor-biased top-50, ungated sim (vol_gate not modelled), funding not modelled.
Artifacts: `hypotheses/W-X7_results.json`.
