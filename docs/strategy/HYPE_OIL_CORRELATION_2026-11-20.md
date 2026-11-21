# HYPE / oil correlation and strategy research, 2026-11-20

**Verdict: no alpha. HYPE and Brent oil are weakly, negatively, and durably
correlated (1h Pearson -0.117, 95% CI [-0.150, -0.083], 208 days, n=4,999) --
a real, statistically unmistakable relationship (t=-8.3), but far too small
to trade (r^2 = 1.4% of variance explained). Every strategy built on top of
it -- a portfolio sizing guard, four cross-signal entry filters, and a
frozen-beta pair trade -- either fails the repo's own EDGE/LEANING/NOISE bar
outright, or clears it on paper while actually performing WORSE than the
unfiltered live rule out of sample. Ranked by out-of-sample expectancy, the
best number in this whole research is HYPE's own already-shipped live rule
(+0.202R/trade, n=40) -- nothing built here beats it.** No live config, rule,
`.claude/agents/desk-tick.md`, or skill is touched. Proposals only.

Operator: "build correlations and strateges between hype and oil."

## Ranked by out-of-sample expectancy (the held-out tail, net of fees, in R)

| Rank | Candidate | n (tail) | Mean R / trade | Net R | Label | Beats its own baseline OOS? |
|---|---|---|---|---|---|---|
| 1 | `b_hype_x_oil_shock_4pct` (HYPE, no new entry on a day Brent moves >4%) | 37 | **+0.215** | +7.95 | LEANING | **No** (baseline +0.202/trade, +8.06 net -- the filter removed 3 trades and barely moved the average) |
| 2 | **HYPE's own live rule (trend-gated 24h range, unchanged)** | 40 | +0.202 | +8.06 | LIVE (already shipped) | n/a |
| 3 | `b_oil_x_hype_drawdown_2pct` (oil, no new entry on a day HYPE falls <=-2%) | 28 | +0.141 | +3.96 | NOISE | Yes on both train and tail, but still a net LOSER overall (full-sample net R stays negative) |
| 4 | `b_hype_x_oil_trend_oppose_72h` (HYPE, only enter against oil's 72h trend) | 26 | +0.071 | +1.83 | LEANING | **No** (baseline +0.202/trade, +8.06 net -- this filter is markedly worse on the tail) |
| 5 | Oil's own live rule (no-thesis stand-in, unchanged) | 33 | -0.025 | -0.83 | LIVE (already shipped) | n/a |
| 6 | `pair_z2_lb30d` (the pair trade, both legs pooled) | 131 | -0.104 | -13.69 | NOISE | n/a (a new strategy, no baseline to beat) |
| 7 | `b_oil_x_hype_trend_agree_168h` (oil, only enter with HYPE's 168h trend) | 30 | -0.320 | -9.59 | NOISE | **No** -- looked great in-sample (train net R -30.78 to -3.18), reversed hard out of sample (tail -0.83 to -9.59): textbook overfit |

**No candidate clears NOISE with real out-of-sample alpha.** Rows 1 and 4 carry the LEANING label only because `verdict()`'s bar checks a candidate's own absolute profitability, not whether it beats doing nothing -- and the HYPE rule they barely touch was already profitable on its own. Once checked against that baseline (`hype_oil_strategy_test.baseline_delta`), both are flat-to-worse. This is not a close call dressed up as a win: it is a plain NOISE finding across the board.

Two notes on the baseline rows themselves: HYPE's row is its real shipped rule (`next_trade.HYPE_RULE`, trend-gated, exactly as in `HYPE_BACKTEST_2026-11-20.md`). Oil's row is the backtest's standard no-thesis stand-in (`sp500_strategy_test.oil_as_is`, "whichever 24h edge is closer") -- every oil/sp500/hype eval in this repo uses the same substitute when there is no journaled thesis to replay, and it is NOT a claim about the real oil desk's actual (thesis-driven) performance, which this window's commit message puts at +$6.17 over 28 real trades. The mechanical stand-in's own full-sample result here (-31.61R, 137 trades) is a known property of that substitute rule on this coin, unrelated to the correlation question.

## 1. The correlation, in full

Script: `services/strategies/evals/hype_oil_correlation.py`. Data: one pinned snapshot (`services.strategies.backtest.snapshot`), `~/Documents/code/pathiel-replays/candles/20261120-hype-oil` -- HYPE and xyz:BRENTOIL, both fetched the same day, both returning the venue's full available history at 1h resolution: identical window, **2026-04-26 11:00Z to 2026-11-20 17:00Z, 208.2 days, 4,999 aligned hourly return pairs** (well past the 60-day minimum the task set; this is literally all the 1h history Hyperliquid's `candleSnapshot` serves for either coin, capped at 5,000 bars). 4h bars are built by aggregating four consecutive 1h bars to UTC-aligned buckets (1,248 pairs).

### Headline number

| Window | n | Pearson | Spearman | t-stat | 95% bootstrap CI (Pearson) |
|---|---|---|---|---|---|
| 1h | 4,999 | **-0.1166** | -0.0754 | -8.30 | **[-0.150, -0.083]** |
| 4h | 1,248 | -0.0830 | -0.0947 | -2.94 | [-0.138, -0.028] |

Beta of HYPE's hourly return on oil's: **-0.201** (95% CI [-0.257, -0.144]) -- a 1% Brent move associates with about a -0.2% HYPE move, on average, net of everything else. Both the correlation and the beta CIs sit entirely on the negative side of zero: this is not a coin flip. It is also not large: r^2 = 0.0136, so oil's hourly return explains about 1.4% of HYPE's hourly variance. For comparison, HYPE's own correlation to BTC (measured in `HYPE_BACKTEST_2026-11-20.md`) is 0.585 -- more than 30x the r^2. Oil is a measurable but minor factor in HYPE's hourly noise, not a second BTC.

### Lead/lag: no real signal

Cross-correlation swept from -24h to +24h (`lead_lag`, joined by exact timestamp, not list index): **the peak sits at lag 0** (contemporaneous, r=-0.1166, t=-8.30). Every non-zero lag is far weaker. Scanning 48 non-zero lags for the biggest |t| is itself a multiple-testing problem: at the |t|>2 threshold, pure chance alone produces about 2.18 false crossings in 48 tries. The actual count was **3** (lag -15h, t=+3.78; lag +7h, t=-2.52; lag -10h, t=+2.45) -- `lead_lag_significance_check` calls this **consistent with chance, not a real lead/lag effect** (`passes_multiple_testing: False`), and none of the three crossings has an economic story: they are isolated single-lag spikes with no consistent neighbours building toward them, the opposite of what a genuine lead/lag relationship looks like. **Neither coin reliably leads the other on an hourly clock.**

### Stability: the sign is remarkably durable, the size is not

| Window | n windows | Negative | Positive | Same-sign share | Range |
|---|---|---|---|---|---|
| Rolling 7d | 202 | 176 | 26 | 87% | [-0.415, +0.155] |
| Rolling 30d | 179 | **179** | **0** | **100%** | [-0.196, -0.035] |

Every single 30-day rolling window over the full 208-day sample reads negative. This is the closest thing to a "hard fact" in this research: whatever drives the HYPE/oil relationship, its DIRECTION has not flipped once in over 200 days. Its magnitude, though, wanders between essentially zero (-0.035) and modest (-0.196) -- never strong enough on its own to trade, which the strategy section below confirms directly.

### Regimes

| Regime | n | Pearson | Spearman |
|---|---|---|---|
| Oil shock days (\|1d move\| > 3%) | 1,008 | **-0.161** | -0.132 |
| Weekend (Brent's futures weekend, HYPE trades through it) | 1,462 | -0.146 | -0.087 |
| US session (13:00-21:00 UTC) | 1,669 | -0.126 | -0.090 |
| Weekday | 3,537 | -0.112 | -0.073 |
| HYPE drawdown days (1d move <= -3%) | 1,074 | -0.048 | -0.045 |
| Asia session (00:00-08:00 UTC) | 1,664 | -0.044 | -0.031 |

The correlation roughly doubles on oil shock days (-0.161 vs the -0.117 baseline) and is weakest during the Asia session and on HYPE's own drawdown days. It never flips sign in any regime. Session buckets are fixed UTC-hour approximations (documented in the script), not exact exchange hours.

### Tail co-movement

Of oil's worst 5% of hours (250 of 4,999), HYPE was down only **38.4%** of the time -- LESS than HYPE's own unconditional down-hour rate of 49.3%. Of oil's best 5% of hours, HYPE was up only **34.4%** of the time -- well under HYPE's own unconditional UP-hour rate (the complement of its 49.3% down rate, so roughly 50-51%). The reverse direction, as the task asked: of HYPE's worst 5% of hours, oil was down **46.4%** of the time, against oil's own unconditional down-hour rate of 49.1% -- barely different from oil's base rate. All three comparisons agree with the negative correlation's direction (oil's tail hours mildly tilt HYPE the OTHER way, and vice versa), but every one is a small tilt, not a rule: HYPE still finishes down on 38% of oil's worst hours, up on 34% of its best, and oil moves the "expected" direction only slightly less than its own base rate when HYPE has its worst hours.

### Plain verdict on the correlation

**Yes, correlated -- negative, small, and durable.** Across 208 days, every honest statistical test (overall, bootstrap, rolling, regime-split, tail) agrees on the sign and none crosses zero. It is NOT large enough to be a second BTC-style driver of HYPE, and there is no lead/lag either coin could trade on. The regime split says the relationship is strongest around oil-specific shocks and on weekends, weakest during Asian hours -- consistent with oil being the more "macro-driven" leg and HYPE, a continuously-traded token, absorbing a small negative tilt from it rather than the reverse.

## 2. Strategy candidates

Script: `services/strategies/evals/hype_oil_strategy_test.py`. Reuses the desks' real rules (`sp500_strategy_test`'s engine -- `Costs`, `Tick`/`Order`/`Trade`, `build_ticks`, `simulate`, `summarize`, `verdict`, `pick`, `split_ms`, `edge_rule`, `oil_as_is` -- unmodified; HYPE's own live constants from `hype_strategy_test.live_variant()`), the same 70/30 train/tail split, and the SAME EDGE/LEANING/NOISE bar every other eval in this repo uses, unaltered.

### (a) Portfolio risk: does correlation make a joint drawdown worse?

Both desks' real live rules were simulated independently on their own 1h ticks (oil: 137 trades over 208 days; HYPE: 135), then combined onto one shared account (`portfolio_equity`: each trade moves total equity by `r x min(risk_pct%, stop_frac x leverage) x that desk's own equity_share`, 32%/60%, `risk_pct=6%`). Overlaps are frequent: of oil's 137 entries, 59 (43%) opened while HYPE already held a position (31 same-direction, 28 opposite); of HYPE's 135, 114 (84%) opened while oil already held one (51 same, 63 opposite).

| Guard | Full x | Full DD | Train x | Train DD | Tail x | Tail DD |
|---|---|---|---|---|---|---|
| None (baseline) | 1.495x | 32.1% | 1.291x | 29.4% | 1.158x | 32.1% |
| Block the 2nd SAME-direction entry | 0.977x | 42.5% | 1.001x | 33.2% | 0.976x | 31.8% |
| Half-size the 2nd SAME-direction entry | 1.258x | 28.1% | 1.171x | 24.3% | 1.075x | 28.1% |
| Block the 2nd OPPOSITE-direction entry | 1.112x | 44.3% | 0.775x | 44.3% | 1.434x | 22.6% |
| Half-size the 2nd OPPOSITE-direction entry | 1.348x | 35.6% | 1.039x | 35.6% | 1.298x | 23.8% |

Two findings, both against the brief's working assumption:

1. **Guarding SAME-direction overlaps is a consistent small net loss.** Since the price correlation is negative, two same-side bets (both long, or both short) are already a mild natural HEDGE -- the same logic `PULLBACK_AND_CORRELATION_TEST_2026-11-02.md` found for oil/silver's own (much stronger, -0.36) negative correlation. Blocking or halving them removes that diversification benefit: return falls in every window (full, train, and tail), and drawdown only improves modestly (half-sizing) or gets WORSE (blocking outright, full DD 32.1% to 42.5%). **Do not size down or block same-direction entries.**
2. **Guarding OPPOSITE-direction overlaps is NOT robust.** On the 70% train portion it clearly hurts (block: 1.291x to 0.775x, DD 29.4% to 44.3%); on the 30% held-out tail it looks clearly good (block: 1.158x to 1.434x, DD 32.1% to 22.6%). A rule whose sign flips between train and tail is not a rule, it is noise that happened to land favorably in the most recent third of the sample. **Not adopted** -- flagging it plainly rather than cherry-picking the tail number, per the task's instruction to be strict about this.

**No portfolio guard is proposed.** The desks already run independently; this data gives no reason to change that.

### (b) Cross-signal filter: does the other coin's trend or a shock day improve entries?

Four sub-families, each a small grid (trend filter: agree/oppose x 72/120/168h span; shock/drawdown filter: 2/3/4% threshold), each judged against its own live-rule baseline (`baseline_delta`), not just in isolation:

| Family | Picked | Label | Train delta net R | Tail delta net R | Real improvement? |
|---|---|---|---|---|---|
| HYPE filtered by oil's trend | oppose, 72h | LEANING | -2.64 | **-6.23** | No |
| HYPE filtered by an oil shock day | block on >4% days | LEANING | -5.01 | -0.11 | No (indistinguishable from baseline, fewer trades) |
| Oil filtered by HYPE's trend | agree, 168h | NOISE | +27.60 | **-8.76** | No -- textbook in-sample overfit, reverses hard out of sample |
| Oil filtered by a HYPE drawdown day | block at <=-2% days | NOISE | +8.47 | +4.79 | Technically yes on both legs, but the underlying rule is STILL a net loser overall |

None of the four is real, durable alpha. The two "LEANING" rows exist because the absolute verdict bar only checks whether the FILTERED rule is itself profitable and robust -- not whether the filter improved on doing nothing -- and HYPE's underlying live rule was already profitable on its own. The one family that looked like a large in-sample win (oil + HYPE's 168h trend, train net R -30.78 to -3.18) is the clearest overfitting example in this research: a 6-variant grid search on 208 days of data will occasionally find a filter that happens to remove exactly the right losing trades in the first 70% and the wrong ones in the last 30%. **No cross-signal filter is proposed.**

### (c) Lead/lag entry: not built

The task's own gate -- "only if (1) finds a lead with t > 2" -- is read together with `lead_lag_significance_check`'s multiple-testing correction, since scanning 48 lags for the single best one is exactly the kind of search that produces a lone |t|>2 crossing by chance alone roughly 2 times in 48 tries, and this research found 3. None of the three crossings forms a plausible causal story (an isolated lag -15h spike with flat neighbours on both sides) and the finding does not survive the correction (`passes_multiple_testing: False`). **`LEAD_LAG_ENTRY_GATE_MET = False`** is hard-coded in `hype_oil_strategy_test.py` with a gate test pinning it, so a future data refresh that genuinely changes this finding fails loudly here rather than silently.

### (d) Pair / relative-value trade: built, NOISE

Justified by the correlation's stable SIGN (100% of 30-day windows negative) even though its magnitude is weak. Spread = HYPE's cumulative log return minus a FROZEN train-only beta (-0.629, a separate number from the hourly-return OLS beta above -- this one is fit on cumulative paths, not hourly returns, and documented as such in the code) times oil's cumulative log return; entry on a trailing (no-look-ahead) z-score crossing a threshold, mean-reversion on both legs at once. Grid: entry z in {1.5, 2.0, 2.5}, lookback in {14, 30} days. Picked cell (z=2.0, 30d): **131 pooled trades on the tail, mean R -0.104, net -13.69R -- a clear loser**, failing every leg of the verdict bar. The weak magnitude of the underlying relationship (r^2=1.4%) was never going to support a mean-reversion trade with a real edge once entry/exit costs are applied; this was tested rather than assumed, and the answer is no.

## 3. The one proposed rule

**None earns a place in the desk rules.** Every candidate either fails the repo's own NOISE/LEANING/EDGE bar outright, or passes it only because it barely touches an already-profitable rule without actually improving on it. The honest recommendation is: change nothing. Keep oil and HYPE running as two fully independent desks, with no cross-asset guard, filter, or combined entry rule between them.

## 4. What would change the verdict

- **A much larger sample.** 208 days is all the venue serves at 1h resolution; a doubling of history (if HYPE's main-dex history grows past Hyperliquid's 5,000-bar `candleSnapshot` cap) would tighten the correlation's confidence interval and could reveal whether the regime split (oil-shock-day correlation nearly 40% stronger than baseline) holds up or was itself a small-sample artifact.
- **A real oil thesis to replay.** Every oil-side result here uses the no-thesis "whichever 24h edge is closer" stand-in, which has its own well-documented NOISE-to-LOSER track record independent of this correlation question (see `SP500_BACKTEST_2026-11-19.md`'s oil_as_is rows and `OIL_STRATEGY.md`). A genuine thesis-driven oil trade stream, re-run through the SAME cross-signal and portfolio tests here, could land differently -- this research only speaks to the mechanical rule.
- **A real stress event.** The correlation strengthens on oil shock days (-0.161 vs -0.117). This sample's oil-shock days were mostly the ordinary >3% daily moves oil already has regularly; a genuine supply/demand shock (an OPEC+ surprise, a Hormuz event) at a time crypto risk appetite is also moving sharply could show a materially different, stronger, and possibly differently-signed relationship than 208 days of normal conditions captured. The portfolio guard section's train/tail sign-flip on opposite-direction overlaps is itself a small preview of how unstable this relationship can look across different slices of even ordinary history.
- **A HYPE operator thesis genuinely citing oil** (the way the oil/silver guard test waited for silver theses to cite "Iran" or "Hormuz" before B3 could be tested at all) would let a real cross-desk driver tag be tested directly, instead of inferring one from price alone.

## 5. How this was run

```
.venv/bin/python -m services.strategies.backtest.snapshot --coin HYPE --days-back 600 \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil
.venv/bin/python -m services.strategies.backtest.snapshot --coin xyz:BRENTOIL --days-back 600 \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil
.venv/bin/python -m services.strategies.evals.hype_oil_correlation \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil
.venv/bin/python -m services.strategies.evals.hype_oil_strategy_test \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil
```

Writes `/tmp/hype-oil-correlation/report.json` and `/tmp/hype-oil-strategy/{report.json,summary.csv}`. Read only: no account, no live config, no pathiel MCP tool, no LLM call. Gate tests (offline, synthetic candles plus a committed real-data fixture slice -- `services/strategies/tests/fixtures/hype_oil_corr_*.json`): `services/strategies/tests/test_hype_oil_correlation.py` (35 tests, including the required synthetic known-lag recovery -- both a positive "oil leads" and a negative "HYPE leads" case, plus a case with a deliberate gap in one series to prove the join is by timestamp, not list index) and `services/strategies/tests/test_hype_oil_strategy_eval.py` (37 tests: the overlap guard, the cross-signal filters, the pair-trade z-score and rule, the baseline-delta check, and the OOS ranking logic, including the exact train/tail numbers from the real `b_hype_x_oil_shock` run as a named regression case for "mean R up, net R down is not an improvement"). `.venv/bin/python -m pytest -q`: 6,826 passed, 2 skipped, 0 failed (full repo suite, including the 72 new tests here).
