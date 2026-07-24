# W-Z3 — Polymarket mechanical backtest: is there any rule-based edge?

2026-07-24. Deterministic, free, no LLM. Sample: **263 resolved markets**
(164 non-sports), volume ≥ $20k, resolved within 400 days, pulled from Gamma
`closed=true&order=volumeNum` + the CLOB hourly price series per YES token.
Harness: `services/polymarket_scout/backtest.py` (tests:
`tests/test_polymarket_backtest.py`). Raw report: `/tmp/poly-backtest/report_deep.json`.

## What is and is not testable here

**Not testable: the LLM's forecast.** Any model asked today about a market that
already resolved has the outcome in its weights or one search away. A
"backtested LLM edge" on resolved markets is leakage. The LLM lane earns its
number forward, on `.state/polymarket_scout/signals.jsonl`.

**Testable: everything mechanical.** That is the point of this run — it produces
the null the forward lane has to beat, and it kills the rule-based shortcuts
before they get capital.

Method, no lookahead: `t_end` = last traded bar; a decision at `t` reads
`price_at(hist, t)`, which returns the last bar **at or before** `t` and never
scans forward. Every PnL is net of 1%/fill fees (charged again on redemption
when the side wins) plus 1c of adverse slippage on entry, because the series is
last-trade marks and a real fill is worse.

## Result 1 — the price is well calibrated

| horizon | Brier (all) | Brier (non-sports) |
|---|---|---|
| T-24h | 0.146 | **0.088** |
| T-72h | 0.131 | 0.081 |
| T-168h | 0.051 | 0.050 |

For reference, frontier LLM forecasters benchmark ~0.135 and superforecasters
~0.096 (W-Z1 citations). The non-sports Polymarket price at T-24h is **already at
or better than superforecaster level**. We are not going to beat it on average;
any edge has to come from a selected subset, which is exactly what a divergence
filter is.

## Result 2 — the documented longshot bias does not translate to a trade

| rule | non-sports | all |
|---|---|---|
| buy favorites p≥0.80 @T-24h | −0.037/$ (n=10, t=−0.39) | −0.114/$ (n=20, t=−1.3) |
| fade longshots p≤0.20 @T-24h | **+0.0106/$ (n=89, t=2.28)** | −0.0145/$ (n=95, t=−0.87) |
| fade longshots p≤0.20 @T-72h | +0.0086/$ (n=80, t=1.8) | +0.0016/$ (n=84, t=0.13) |
| fade longshots p≤0.20 @T-168h | −0.0254/$ (n=38, t=−0.94) | −0.0254/$ (n=38, t=−0.94) |

The **reversed** longshot bias W-Z1 cited (favorites underpriced on politics) does
not replicate: buying favorites is negative at every horizon here.

The classic direction — **fade the longshot** — is the best cell on the board at
t=2.28. It does not survive contact:
- it is the best of **12 cells** (2 rules × 3 horizons × 2 subsets). Bonferroni
  puts it at p≈0.30.
- it **flips sign** at T-168h and **flips sign** when sports are included, i.e. it
  is not robust to sample composition.
- win rate 0.27: it is a sell-tails trade whose mean is one bad settlement from
  moving.

**Verdict: CANDIDATE, not validated.** Do not wire it. Re-test at n≥400 with a
pre-registered single cell (non-sports, T-24h, p≤0.20) before it earns anything.

## Result 3 — the BREAKING lane has no mechanical edge

Sampled every 24h across each market's life, excluding the final 24h before
settlement (that bar is resolution drift, not news). BREAKING = |24h move| ≥ 5pp.

| measure | non-sports | all |
|---|---|---|
| forward 24h move, same direction | −0.016 (n=46, t=−0.70) | −0.015 (n=53, t=−0.75) |
| momentum → resolution | −0.060/$ (n=46, t=−0.93) | −0.017/$ (n=53, t=−0.29) |
| fade → resolution | +0.011/$ (n=46, t=0.17) | −0.032/$ (n=53, t=−0.54) |
| matched null (random side) | −0.018/$ (n=46) | −0.052/$ (n=53) |
| unconditional buy-YES | −0.006/$ (n=978) | −0.003/$ (n=1190) |

After a hard one-day repricing there is **no continuation and no reversal**: the
forward move is statistically zero and neither side beats its own fees. Momentum
is the worse of the two and loses outright.

**Consequence for the product:** the BREAKING tab on `/predictions` is a **news
surface**, not a signal. It is where the LLM gets pointed first (a market the
news just hit is where an un-priced synthesis edge is most likely), not a thing
to trade mechanically.

## Result 4 — sports (added 2026-07-24 by operator request)

Sports/esports game lines are in the sample and reported as the `ALL` column
above; excluding them is what the `NON-SPORTS` column does. Including them makes
market calibration *worse* (Brier 0.146 vs 0.088) but also makes every rule
worse, because the extra markets are scoreboard-driven and priced by a fast,
crowded book. They are rendered on the board (`SPORTS` tab, LIVE first) and can
be forecast on demand (`run --lane sports`, 20pp threshold, own ledger lane), but
they are **not** in the daily forecast cron: there is no measured edge, and an
LLM read of an in-play game is stale before it lands.

## Result 5 — the ">= 3x" board (operator request)

`CRAZY ODDS` shows every tradeable market at or under 33c, across both
universes, ranked by our AI's disagreement first. The tab exists because it was
asked for; the number that has to ship next to it is Result 2's mirror: at
p≤0.20 **buying** the longshot is the losing side of the only cell that cleared
t=2 (≈−1%/$ after fees, n=89). A 3x payout is not a 3x edge. The one version of
this with a thesis is a longshot our brain independently prices far above the
market — which is why the tab ranks on `live_edge`, not on the multiple.

## Overall verdict

**No mechanical edge on this venue.** Every rule-based translation lands inside
noise after fees, and the single cell that clears t=2 is a best-of-12 pick that
inverts under a sample change. Consistent with W-Z1's read: the venue is
efficient where it is liquid, and the only hypothesis left standing is the LLM's
judgment on selected divergences — graded forward, per lane, against these
numbers.

## Reproduce

```bash
python -m services.polymarket_scout.backtest --pages 30 --min-volume 20000 \
    --out /tmp/poly-backtest/report_deep.json
```

First run costs ~35 min of Gamma/CLOB fetches; the series are cached to
`.state/polymarket_scout/backtest_cache.json`, so re-runs are free.

## Gotchas this run exposed (all fixed in the client)

- **Gamma caps a page at 100 rows** regardless of `limit`. The pre-existing
  `open_markets(limit=500, pages=6)` stepped the offset by 500 while receiving
  100 — it silently sampled a stride and missed ~80% of the universe. Every
  pager now steps by `PAGE_MAX`.
- **`order=volume` sorts the string column** and returns junk ordering. Use
  `volumeNum`.
- **`endDate` is not a clock.** Plenty of settled markets carry placeholder end
  dates years out (2028 on markets that closed in July 2026); the backtest ages
  markets off the last price bar instead.
