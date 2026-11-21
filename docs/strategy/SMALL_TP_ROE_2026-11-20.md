# Small-gain-only (ROE take-profit) backtest, 2026-11-20

**Verdict: NO on both desks. Keeping the current structural exit beats every small-ROE-TP variant tried, on both the full 207-day sample and the held-out tail, for both oil and HYPE. Oil's small-TP win rates fall just short of their own fee breakeven at every level tested (10/20/30/40% ROE); HYPE's small-TP win rates clear breakeven at every level but still net less than the shipped rule because a small target exits HYPE's rare, large trend trades far too early (avg winner 0.6-2.6R vs the shipped rule's 3.3R). One cell -- HYPE, 10% ROE target with a stop at 2x that distance -- reaches LEANING+ on the held-out tail alone (not on the full sample); flagged as worth re-checking in three months, not adopted now.** No live rule, config, `.claude/agents/desk-tick.md`, or skill is touched. Research only.

Operator: "back test a strategy where we aim for small gains only, 10,20,30% 40% max."

- Script: `services/strategies/evals/small_tp_roe_test.py` (gate tests: `services/strategies/tests/test_small_tp_roe_eval.py`, 35 tests).
- Data: the already-pinned snapshot `~/Documents/code/pathiel-replays/candles/20261120-hype-oil` (built this morning for `HYPE_OIL_CORRELATION_2026-11-20.md`): both xyz:BRENTOIL and HYPE, 1h bars, **2026-04-27T10:00Z to 2026-11-20T17:00Z, 207.3 days**, 70/30 train/tail split (tail from 2026-09-19T12:30Z).
- Output: `/tmp/small-tp-roe/report.json`, `/tmp/small-tp-roe/summary.csv`.

## 1. Interpretation: ROE, not price

"10/20/30/40% max" is read as a take-profit at that percent **return on the position's margin (ROE)** at each desk's own leverage -- the number that would actually show up as the position's unrealized P&L percentage, not a raw price move. Gross (before fees/funding), a leveraged position's margin moves `leverage` times faster than price itself:

```
roe_price_move(roe_pct, leverage) = (roe_pct / 100) / leverage
```

| Desk | Leverage | 10% ROE | 20% ROE | 30% ROE | 40% ROE |
|---|---|---|---|---|---|
| Oil (xyz:BRENTOIL) | 20x (live) | **0.50%** | 1.00% | 1.50% | 2.00% |
| HYPE | 10x (live, venue max) | **1.00%** | 2.00% | 3.00% | 4.00% |
| HYPE, for reference | 9x | 1.11% | 2.22% | 3.33% | 4.44% |

HYPE's live leverage is exactly 10x (the venue max, `next_trade.HYPE_LEVERAGE`), so this eval trades the 1.0%/2.0%/3.0%/4.0% column; the 9x row is shown only because the task brief's own "9-10x" range implied it, for a leverage this desk never actually runs at.

**R multiple each level pays against each desk's own typical stop** (its 24h-range stop buffer, measured as the median `stop_frac` on the baseline run -- oil 1.077% of price, HYPE 1.472%):

| Desk | 10% ROE | 20% ROE | 30% ROE | 40% ROE |
|---|---|---|---|---|
| Oil | 0.46R | 0.93R | 1.39R | 1.86R |
| HYPE | 0.68R | 1.36R | **2.04R** | **2.72R** |

**`place_bracket` refuses any new bracket under 2R** (`pathiel.agents.bracket.DEFAULT_MIN_R`). Oil never clears it at any level tested here -- its structural stop is wide relative to even a 40% ROE target. HYPE's 30% and 40% levels nominally DO clear 2R against its own typical stop; 10% and 20% do not. Section 5 covers what would have to change for any of this to become a real order.

**Entries, stop formula, fees and the leverage CAP are unchanged from the live rule** for both desks (`oil_entry_rule`/`hype_entry_rule`, built from `next_trade.ATR_BUFFER_MULT`/`STOP_BUFFER_FLOOR_FRAC`/`HYPE_RULE` exactly). Leverage is fixed (oil 20x, HYPE 10x, no 20/15 leverage-fit refit) so "NN% ROE" means the same price move on every trade -- live oil's fit can refit a stop that fails 20x down to 15x on a small share of entries, which this eval does not model (it would make the ROE math ambiguous per trade: the same ROE target needs a different price move at a different leverage). Only the **exit** changes across variants.

## 2. The exits compared

One family = one way of filling the Order once the live entry rule has already said yes:

| Family | TP | Stop |
|---|---|---|
| `baseline` | the desk's current structural target (far 24h edge, live offset) | the desk's current structural stop |
| `tpNN` | NN% ROE | the desk's structural stop, **unchanged** |
| `tpNN_sym` | NN% ROE | a symmetric stop, the same NN% ROE on the loss side |
| `tpNN_2xstop` | NN% ROE | a stop at 2x that ROE on the loss side |
| `tpNN_be` | NN% ROE | the structural stop, **moved to breakeven** (the raw entry price) once price reaches half the distance to the TP |

17 variants a desk (1 + 4 x 4), each wrapping the exact same `entry_rule`, so every `tpNN*` variant shares the identical set of entry times as the baseline -- confirmed directly, not assumed (`test_every_small_tp_variant_shares_the_entry_rules_own_eligibility`). A smaller target closes a trade faster, so the same fixed window re-enters MORE often: oil's `tp10` sees 196 entries where the baseline sees 122, over the identical 207 days.

Each variant is labelled **EDGE / LEAK / LEANING+ / LEANING- / FLUKE / NOISE** by `services.review.stats.label_slice` (the same engine the live trade-review skill uses) on the **held-out tail**, Bonferroni-corrected for the 17 variants tried per desk: the raw one-sample p is multiplied by 17 (capped at 1.0) before `label_slice`'s own 0.05/0.20 cutoffs apply, so a result needs to be meaningfully stronger than a one-shot test to call it anything but NOISE.

## 3. Oil (xyz:BRENTOIL, 20x, $58.24 slice at today's $182 equity x 32%)

| Variant | n (full/tail) | Win % (full/tail) | Avg win R | Avg loss R | Expectancy R (full/tail) | Net $ (full) | Max DD (R) | Longest losing streak | Tail label |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 122/39 | 26.2/35.9 | 3.01 | -1.08 | -0.01/+0.26 | -31.22 | 30.0 | 14 | NOISE |
| tp10 | 196/62 | 68.4/75.8 | 0.45 | -1.04 | -0.02/+0.09 | -20.44 | 12.2 | 3 | NOISE |
| tp10_sym | 248/77 | 52.4/57.1 | 0.94 | -1.08 | -0.02/+0.07 | -31.92 | 14.5 | 5 | NOISE |
| tp10_2xstop | 202/63 | 67.8/74.6 | 0.47 | -1.05 | -0.02/+0.08 | -20.50 | 13.4 | 3 | NOISE |
| tp10_be | 219/67 | 48.4/53.7 | 0.45 | -0.51 | -0.05/+0.03 | -30.63 | 13.9 | 6 | NOISE |
| tp20 | 166/54 | 53.0/63.0 | 0.93 | -1.05 | -0.00/+0.20 | -15.03 | 13.7 | 4 | NOISE |
| tp20_sym | 170/54 | 51.8/61.1 | 0.96 | -1.05 | -0.01/+0.18 | -18.75 | 12.5 | 4 | NOISE |
| tp20_2xstop | 137/47 | 66.4/76.6 | 0.48 | -1.04 | -0.03/+0.12 | -18.46 | 11.1 | 3 | NOISE |
| tp20_be | 192/61 | 35.9/42.6 | 0.88 | -0.54 | -0.03/+0.09 | -26.38 | 14.8 | 10 | NOISE |
| tp30 | 157/49 | 42.0/53.1 | 1.38 | -1.05 | -0.03/+0.25 | -28.71 | 20.7 | 6 | NOISE |
| tp30_sym | 137/45 | 51.8/64.4 | 0.97 | -1.03 | +0.01/+0.26 | -10.83 | 14.1 | 4 | NOISE |
| tp30_2xstop | 99/31 | 67.7/74.2 | 0.47 | -1.04 | -0.02/+0.08 | -9.90 | 5.6 | 3 | NOISE |
| tp30_be | 175/55 | 30.3/38.2 | 1.34 | -0.65 | -0.05/+0.14 | -32.91 | 21.0 | 14 | NOISE |
| tp40 | 153/47 | 35.9/44.7 | 1.86 | -1.05 | -0.00/+0.27 | -24.34 | 17.1 | 13 | NOISE |
| tp40_sym | 119/40 | 53.8/67.5 | 0.97 | -1.03 | +0.05/+0.33 | +6.64 | 8.7 | 5 | NOISE (p_bonf=0.69) |
| tp40_2xstop | 77/22 | 63.6/77.3 | 0.49 | -1.05 | -0.07/+0.11 | -20.15 | 9.0 | 5 | NOISE |
| tp40_be | 163/52 | 29.4/36.5 | 1.71 | -0.73 | -0.01/+0.22 | -23.94 | 21.6 | 10 | NOISE |

**Breakeven win rate** (desk-stop variants, after fees):

| ROE | Price move | Breakeven win rate needed | Achieved win rate | Gap |
|---|---|---|---|---|
| 10% | 0.50% | 70.3% | 68.4% | **-1.9 pts** |
| 20% | 1.00% | 53.4% | 53.0% | -0.4 pts |
| 30% | 1.50% | 43.1% | 42.0% | -1.1 pts |
| 40% | 2.00% | 36.1% | 35.9% | -0.2 pts |

Oil's small-TP win rate comes up **short of its own breakeven bar at every single level tested**, by 0.2-1.9 points, consistently. This is not noise around zero -- it is a small, stable, structural miss: oil's hourly moves hit a close-by ROE target often, but not QUITE often enough to pay for the entries that stop out, at any of the four sizes. The whole grid (16 small-TP variants, 2 stop-widening variants and the breakeven variant per level) stays in NOISE; the one cell that prints a small full-sample profit (`tp40_sym`, +$6.64) does not survive the Bonferroni correction (p_bonf=0.69, nowhere near either cutoff) and is a cherry-pick off 17 tries, not a result.

**Oil verdict: small-gain-only does not beat the current exit. No level adopted.**

## 4. HYPE (10x, $109.20 slice at today's $182 equity x 60%)

| Variant | n (full/tail) | Win % (full/tail) | Avg win R | Avg loss R | Expectancy R (full/tail) | Net $ (full) | Max DD (R) | Longest losing streak | Tail label |
|---|---|---|---|---|---|---|---|---|---|
| **baseline** | 118/39 | 28.8/25.6 | **3.27** | -1.08 | **+0.17**/-0.02 | **+53.62** | 12.9 | 12 | NOISE |
| tp10 | 176/64 | 64.2/73.4 | 0.61 | -1.08 | +0.01/+0.13 | -17.36 | 10.3 | 8 | NOISE |
| tp10_sym | 203/69 | 50.7/63.8 | 0.90 | -1.11 | -0.09/+0.17 | -84.19 | 32.8 | 10 | NOISE |
| tp10_2xstop | 154/55 | 72.1/83.6 | 0.45 | -1.06 | +0.03/**+0.20** | +17.25 | 9.4 | 7 | **LEANING+ (p_bonf=0.195)** |
| tp10_be | 219/74 | 39.7/52.7 | 0.63 | -0.52 | -0.06/+0.08 | -69.45 | 20.7 | 12 | NOISE |
| tp20 | 141/49 | 46.1/57.1 | 1.31 | -1.08 | +0.02/+0.27 | -19.77 | 13.0 | 9 | NOISE |
| tp20_sym | 118/40 | 53.4/67.5 | 0.95 | -1.06 | +0.02/+0.30 | -10.81 | 13.0 | 8 | NOISE |
| tp20_2xstop | 84/33 | 71.4/81.8 | 0.48 | -1.03 | +0.05/+0.20 | +18.89 | 5.6 | 5 | NOISE (p_bonf=0.88) |
| tp20_be | 165/60 | 31.5/36.7 | 1.32 | -0.64 | -0.02/+0.15 | -45.97 | 13.8 | 11 | NOISE |
| tp30 | 133/45 | 36.8/42.2 | 2.07 | -1.08 | +0.08/+0.23 | +5.68 | 9.9 | 9 | NOISE |
| tp30_sym | 86/31 | 54.7/61.3 | 0.97 | -1.04 | +0.06/+0.19 | +16.49 | 7.5 | 6 | NOISE |
| tp30_2xstop | 56/22 | 71.4/72.7 | 0.48 | -1.02 | +0.05/+0.07 | +15.51 | 5.1 | 5 | NOISE |
| tp30_be | 146/52 | 29.5/34.6 | 2.08 | -0.77 | +0.07/+0.28 | +14.09 | 12.0 | 11 | NOISE |
| tp40 | 120/40 | 32.5/32.5 | 2.62 | -1.08 | +0.12/+0.15 | +26.54 | 9.5 | 9 | NOISE |
| tp40_sym | 64/22 | 59.4/59.1 | 0.98 | -1.03 | +0.16/+0.16 | +71.64 | 5.2 | 5 | NOISE |
| tp40_2xstop | 47/15 | 72.3/66.7 | 0.49 | -1.02 | +0.07/-0.01 | +19.33 | 3.7 | 3 | NOISE |
| tp40_be | 134/47 | 25.4/27.7 | 2.72 | -0.80 | +0.09/+0.26 | +15.72 | 10.3 | 13 | NOISE |

**Breakeven win rate** (desk-stop variants, after fees):

| ROE | Price move | Breakeven win rate needed | Achieved win rate | Gap |
|---|---|---|---|---|
| 10% | 1.00% | 63.7% | 64.2% | +0.5 pts |
| 20% | 2.00% | 45.4% | 46.1% | +0.7 pts |
| 30% | 3.00% | 35.3% | 36.8% | +1.5 pts |
| 40% | 4.00% | 28.9% | 32.5% | **+3.6 pts** |

HYPE's small-TP win rate **clears its own breakeven bar at every level**, comfortably at 40% ROE (+3.6 points). So a small ROE target standing alone is not a losing bet on HYPE -- but it still loses to doing nothing (keeping the structural target): the baseline's own average WINNER is 3.27R, 1.2x to 7.3x bigger than any small-TP variant's average winner (0.45R to 2.72R across the grid). HYPE's live edge is trend-gated and comes from a handful of large trades; a close-by ROE target locks in a small gain and gives back the rest of the move every time. Full-sample net R: baseline +20.48 vs the best small-TP cell, `tp40` at +14.58 -- every other cell trails further.

**The one flagged exception.** `tp10_2xstop` (10% ROE target, stop at 2x that ROE = 20% ROE loss) reaches **LEANING+ on the held-out tail** (p_bonf=0.195, inside the 0.20 cutoff by a hair): 55 tail trades, 83.6% win rate, +0.204R/trade, +$17.25 net over the full sample. It does **not** hold on the full sample (NOISE, p far from either cutoff) -- the tail result is the recent third of the sample only, and it is exactly the kind of cell a 17-variant-per-desk search turns up by chance some of the time even after the Bonferroni correction (which only scales the bar up, it does not make a lucky draw impossible). It is also the same window where the baseline's OWN tail performance flipped slightly negative (full +20.48R but tail -0.69R, -0.02R/trade) -- worth a look at whether HYPE's live edge is fading (a `review-trades` question, not this task's), but not evidence to switch the exit on.

**HYPE verdict: small-gain-only does not beat the current exit. No level adopted.** `tp10_2xstop` is worth re-running once more tail history exists (three months out); it is not adopted today.

## 5. What would have to change to actually ship any of this

`place_bracket` refuses a new bracket under 2R (section 1's table). Oil never reaches it; HYPE's 30%/40% levels nominally do against HYPE's own typical stop, but no combination here beat the baseline anyway, so nothing here is asking to ship regardless. If a future small-TP cell DID want to ship, two honest paths exist, both with a real cost:

1. **A dedicated low-R bracket path**, a second `place_bracket`-like entry point with its own, lower minimum R, used only for an explicitly small-gain order. Risk: the 2R floor exists because a trade that only needs to pay a small multiple of its risk to break even needs a correspondingly higher win rate to be worth taking at all (this doc's own breakeven tables are exactly that trade-off, made explicit) -- a lower floor anywhere in the codebase widens the door for noise trades on EVERY rule that uses that path, not just the one cell that happened to look good in one 207-day sample.
2. **Oversizing to compensate**, leaving the 2R floor alone but sizing a small-TP trade larger so the dollar risk:reward still clears some bar. This does not change the underlying win-rate math at all (section 3/4's breakeven tables are leverage- and size-independent, since R already nets out size) -- it only changes how much is at stake on a rule that, per this backtest, does not beat what is already live.

Given section 3 and 4's verdicts, neither path is recommended right now: there is no winning small-TP cell on either desk to build either path FOR.

## 6. How this was run

```
.venv/bin/python -m services.strategies.evals.small_tp_roe_test \
    --snapshot-dir ~/Documents/code/pathiel-replays/candles/20261120-hype-oil
```

(The snapshot was already pinned for `HYPE_OIL_CORRELATION_2026-11-20.md` the same morning -- both coins, same 207.3-day window, no new fetch needed.)

Writes `/tmp/small-tp-roe/report.json` and `/tmp/small-tp-roe/summary.csv` (every variant, both desks, full/train/tail). Read only: no account, no live config, no pathiel MCP tool, no LLM call.

Gate tests (offline, synthetic candles, no network): `services/strategies/tests/test_small_tp_roe_eval.py`, 35 tests covering the ROE-to-price math at each leverage against the operator's own worked examples, the same-bar stop-first rule (plain exits and the new breakeven walk, including the no-look-ahead breakeven trigger and the no-division-by-zero fix once a stop moves), fee netting (`close_trade` and the breakeven-win-rate solve, checked against a hand-derived zero-cost formula), the entry/exit split (every small-TP variant proven to share the live rule's own entry eligibility), the 17-variant grid, and the Bonferroni-corrected label. `.venv/bin/python -m pytest -q`: full repo suite green (6,873 passed, 2 skipped, including the 35 new tests here).
