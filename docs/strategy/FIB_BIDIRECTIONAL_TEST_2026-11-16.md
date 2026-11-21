# Bidirectional fib sleeve test, 2026-11-16

**Verdict: no, do not make the sleeve bidirectional. Nothing adopted, all four hypotheses NOISE or FAIL. The counter leg does fire when it gets the chance, but it loses more than it wins (0% to 26% win rate on every dataset), the single best of 24 variants sits only at the 36th percentile of what 24 random entries would produce by chance, and on today's own tape the counter leg's level stayed 0.4 to 1.8 points away from the day's low the whole session because the 24h-range anchor resets lower every hour, almost as fast as price itself.** Research only. No live config, order, or rule changed. `services/fib_sleeve` untouched.

Operator, 2026-11-16: Brent fell 94.11 to 89.82 in a near-straight line; the live sleeve's with-swing short at the 38.2% bounce placed 5 times and filled 0. Should the sleeve also rest a counter-trend order (a long below a down-swing low, a short above an up-swing high), first fill cancels the other (OCO)?

## Pre-registration (written before results, not edited after)

Four hypotheses, economically motivated, grid capped near 100 variants:

- **H1.** Counter-trend entry at a fib extension beyond the swing's own extreme (23.6% / 38.2% / 61.8% of the swing size past the low on a down-swing, past the high on an up-swing): a resting limit that can fill when the with-swing order never does, because price kept going instead of retracing. Stop 1 ATR further, or beyond the next deeper extension. Target a modest 23.6% or 38.2% retracement back, not a full round trip. Built from the same `fib_price` / `target_price` / `stop_price` the live sleeve uses, just on the other side of the extreme.
- **H2.** Overextension filter: take the counter leg only when the swing itself is stretched, size >= k x ATR14, k in {3, 4, 5}. Mean reversion should live in stretched moves, not every swing.
- **H3.** Positioning filter: Hyperliquid funding z-score (trailing 24h mean vs trailing 30d distribution, the repo's own `backtest.funding_signal` definition) extreme AGAINST the move at the swing. **Prior evidence against, cited before running anything:** `docs/strategy/FUNDING_EXTREME_TEST_2026-11-03.md` already ran the closest analogue on xyz:BRENTOIL itself and refuted it. Over 244 days, z >= 2.0 (crowded long) fires 3 independent events; z <= -2.0 fires 11. "The crowded-long signal this rule needs essentially never happens on Brent." H3 is tested anyway because the operator asked the direct question, and a documented NOISE beats an unasked one.
- **H4.** The full bidirectional sleeve: the live with-swing rule exactly as it trades (`SleeveConfig` defaults: 38.2% retrace, 1-ATR stop, -27.2% extension target, be50 ratchet), plus the best principled counter leg from H1/H2/H3, both resting at once, one position at a time, first fill cancels the other. Compared against the with-swing rule alone on the same windows.

**Grid, named before any run:** 3 extension levels x 2 target retracements x 4 overextension settings (none, k3, k4, k5) = 24 H1/H2 variants, stop fixed at "atr1" and ratchet at "be50" (the live sleeve's own choices; "next"-stop and ratchet-off are checked only on the single reference variant as a sensitivity note, not a second grid). Plus 4 H3 variants (funding threshold 1.5 / 2.0 crossed with the overextension filter off / k4) and 3 H4 OCO variants (the reference counter leg, the reference + k4, the reference + funding 2.0). **24 + 4 + 2 + 3 = 33 variants x 2 risk sizes = 66 judged rows.**

**Reference variant, named before any result:** `ext382_atr1_T382_be50` (38.2% extension, 1-ATR stop, 38.2% retrace target, break-even ratchet, no filter), the middle of every axis, the same convention `fib_retrace_test.REFERENCE` used.

**Datasets, fill model, costs, sizing:** identical to `fib_retrace_test`, same candle/funding loaders, the measured xyz fee (0.90 / 0.31 bps), the same realistic fill (a limit fills on a trade-through by one tick, maker; a gapped resting order fills at the open; a stop is a taker fill with 3 bps slippage), `desk_tick_sim.risk_capped_notional` at 6% and 1.2% risk, the 20/15 leverage fit, weekend flat. Brent journal (primary, 29.6 days), Brent replay30 (30.0 days), WTI journal (29.6 days), each beside the live oil desk's long book and alone; Brent/WTI 15m 50-day sets and Brent/WTI 1h 208-day sets, alone only, with halves.

**Alone is the primary reading**, not beside the live book. The fib sleeve runs on its own $20 sub-account and never actually competes with the oil desk for a slot; "beside live" is reported too, for continuity with `fib_retrace_test`'s own bar, but the headline numbers below are the sleeve on its own equity.

**Validation, pre-registered:**

- Fixed chronological holdout: the last 30% of each 208-day set by calendar time, touched only after a variant is chosen.
- Walk-forward: both halves of the primary and of each 208-day set.
- Random-level baseline: the same filters and the same stop/target rules, entry at a uniformly random extension ratio in [0.15, 0.70] instead of the fixed fib ratio, 200 draws, seed 42. The real variant must beat the draws' distribution; percentile reported.
- Multiple-testing honesty: a max-statistic reality check over the 24-variant H1/H2 grid. 50 draws of 24 independently-randomized-entry variants each, the best of each draw's 24 forms a null distribution for "the best of 24 by chance"; the real grid's own best is checked against that, not against the single-variant baseline.
- Cost stress: taker fee, maker fee and slippage doubled.
- Cross-asset: must not lose on WTI.
- Regime split: Kaufman efficiency ratio per UTC day on 1h closes, >= 0.5 trending.
- Today's tape (2026-11-16) replayed under the reference and under H4.

**Adoption bar, pre-registered, matching `fib_retrace_test`'s own for continuity:** beats live on every dataset with a live book, drawdown no worse, both halves of the primary, every grid neighbour positive (plateau), survives dropping the best day, 10+ trades on the primary, plus net above zero ALONE on every long-history dataset, plus above the random baseline's 95th percentile, plus survives the cost stress. Under 10 trades: LEANING or NOISE, never PASS. "Nothing adopted" was pre-registered as the odds-on outcome, especially for H3.

## Results

0 PASS, 0 LEANING of 66 judged rows. 3 FAIL (the three H4 rows, which clear 10 trades and lose), 63 NOISE (everything else, mostly sub-10-trade H1/H2/H3 cells).

### H1 and H2: the counter leg alone, 24-variant grid + 2 sensitivity checks

| | Brent journal alone (26 variants) | WTI journal alone (26 variants) |
|---|---|---|
| median net $ | -39.12 | -14.54 |
| variants above zero | 1 of 26 | 1 of 26 |

The one variant above zero on the journal (`ext382_T236_be50_k5`, +$0.98) trades on 4 swings. The reference variant (`ext382_atr1_T382_be50`) loses on **every one of its 7 datasets**, with a 0% win rate on three of them:

| dataset | n | net $ | win % |
|---|---|---|---|
| brent_journal | 8 | -63.50 | 0.0 |
| brent_replay30 | 4 | -39.36 | 0.0 |
| brent_15m_50d | 10 | -77.22 | 0.0 |
| brent_1h_208d | 38 | -68.10 | 18.4 |
| wti_journal | 8 | -33.35 | 12.5 |
| wti_15m_50d | 10 | -50.39 | 10.0 |
| wti_1h_208d | 40 | -74.52 | 17.5 |

Walk-forward halves of the journal: -$10.49 (1 trade), -$21.63 (4 trades). 208-day halves: Brent +$26.78 then -$82.71; WTI +$9.79 then -$80.01. Loses in the second half of both 208-day sets.

**Fixed holdout (last 30% of the 208-day sets, never touched while picking a variant):**

| dataset | n | net $ | win % |
|---|---|---|---|
| brent_1h_208d holdout | 12 | -66.15 | 8.3 |
| wti_1h_208d holdout | 12 | -58.78 | 8.3 |

**Random-level baseline** (200 draws, same filters/stop/target, random extension ratio in [0.15, 0.70], brent_journal alone): the reference's observed -$63.50 sits at the **22.5th percentile** of the draws (mean -$45.79, p95 -$11.51). The specific fib ratio is worse than a random entry in the same band, not better.

**Reality check** (the multiple-testing-honest one: 50 draws, each drawing 24 independently-randomized variants and keeping the draw's own best): the real grid's own best (+$0.98, 4 trades) sits at the **36th percentile** of "the best of 24 by chance" (null mean $0.99, p95 $3.80). The grid's best does not even clear the bar that 24 tries alone would set.

**Verdict H1: NOISE, leaning FAIL.** The grid is economically negative and does not clear chance once the number of tries is accounted for.

### H2: the overextension filter

Folded into the grid above (k3/k4/k5 columns). It thins the sample (fewer swings qualify) without turning the sign: k5 variants trade 1 to 6 times and are still mostly negative. It does not rescue H1.

**Verdict H2: NOISE.** No overextension setting turns the counter leg positive.

### H3: the funding filter

| variant | brent_journal alone n |
|---|---|
| fz1.5, no k filter | 0 |
| fz1.5, k4 | 0 |
| fz2.0, no k filter | 0 |
| fz2.0, k4 | 0 |

Zero trades in every cell on the primary window. This is the pre-registered expectation realized exactly: Brent's crowded-long funding events are too rare (2 to 3 in 244 days per `FUNDING_EXTREME_TEST_2026-11-03.md`) to ever land on an eligible down-swing in a 30- to 50-day window. H3 cannot be judged beyond "it essentially never fires," which is itself the answer.

**Verdict H3: NOISE, confirms the prior.** The positioning filter is not a usable gate on a single-instrument swing anchor at this data density.

### H4: the full bidirectional (OCO) sleeve

The with-leg (live defaults) alone, replayed through this same harness, matches the sleeve's own documented number exactly: **23 trades, -$14.86 on brent_journal alone** (this is also the parity check against `fib_retrace_test.walk` in the test suite). Adding the counter leg:

| variant | brent_journal alone | wti_journal alone | brent_15m_50d alone | brent_1h_208d alone |
|---|---|---|---|---|
| with-leg only (reference) | 23 trades, -14.86, DD not worse | - | - | - |
| **oco, reference counter** | 28 trades, **-58.73**, 21.4% win, DD 56.4% | 33 trades, -54.99, DD 45.6% | 46 trades, -132.66, DD 73.9% | 177 trades, -139.30, DD 89.0% |
| oco, reference + k4 | 28 trades, -58.73, DD 56.4% | 31 trades, -38.63, DD 38.6% | 44 trades, -126.19, DD 70.5% | 173 trades, -143.67, DD 90.8% |
| oco, reference + funding z>=2.0 | 23 trades, **-14.86** (same as with-leg-only: the filter never let a counter trade through) | 27 trades, -0.74 | 39 trades, -106.32 | 155 trades, -123.71 |

The 5 extra counter fills the unfiltered OCO adds on the journal net to about **-$43.87** by themselves (-58.73 minus -14.86). When the counter leg gets a chance to fire, it makes the day worse, not better. The funding-filtered row is the cleanest result in the whole study: the filter is so restrictive that it never fires once in this window, so H4+fz2 collapses to exactly the with-leg's own number. That is proof the mechanism works as designed, and proof it offers nothing extra here.

Max drawdown alone: 56% to 91% across the four long-history datasets, against the live with-leg's own (lower) drawdown and the operator's 20% kill-switch ceiling. `judge` on the reference OCO row: FAIL. `dd_no_worse_every_dataset`, `walk_forward_both_halves`, `plateau`, `drop_best_day` and `alone_on_long_history` all false; only the "beside live" leg is true, and that leg is the one that does not really apply to a sub-account that never shares a slot with the desk.

**Bootstrap 95% CI on mean R per trade** (reference OCO, brent_journal alone, 28 trades, 2000 draws): mean R -0.162, CI [-0.739, +0.527]. The interval straddles zero: 28 trades is not enough to call this a loser with confidence, but it is nowhere near enough to call it a winner either, and the point estimate is negative.

**Cost stress** (fees and slippage doubled): reference OCO alone net on brent_journal goes from -$58.73 to -$66.28 (28 trades); the counter leg alone goes from -$63.50 to -$66.44 (8 trades). Both were already negative; doubling costs makes a loser a bigger loser. Neither needed the stress test to fail, but both are reported for completeness.

**Verdict H4: FAIL.** 28+ trades, clears the "judge enough data" bar, and loses on every alone dataset with drawdown far worse than the with-leg alone.

## Regime split (H4 reference, alone trades, Kaufman efficiency ratio by UTC day, >= 0.5 trending)

| regime | n | wins | net $ | avg R |
|---|---|---|---|---|
| trending | 5 | 1 | -23.43 | -0.421 |
| ranging | 23 | 5 | -35.30 | -0.106 |
| unknown | 0 | 0 | 0.00 | - |

Both buckets lose. The trending bucket loses WORSE per trade (-0.42R) than the ranging one (-0.11R), the opposite of the mean-reversion thesis's own premise (a counter-trend entry should do better when the move is genuinely stretched, i.e. trending). With only 5 trending trades this is not a strong claim, but it points the wrong way, not the right one.

## Today's tape, 2026-11-16, replayed

The range24 swing was a single, continuously-deepening down-swing all day: origin 94.108 fixed, extreme falling from 92.651 at 00:00Z to 89.819 by 19:00Z as the hourly window kept rolling in a new, lower low. Two things happened under H4:

1. **One with-leg trade fired**, early: short at 92.6817 (04:00Z, the 38.2% retrace off that hour's extreme of 91.80), stopped at 93.0857 two hours later for -$7.75 (alone, at this backtest's $182.09 reference equity; proportionally about -$0.85 on the sub-account's own $20). This is a DIFFERENT episode from the one the operator described (5 placements, 0 fills). That happened later in the day, as the decline kept making fresh lows without ever bouncing back up to the (also falling) 38.2% level: exactly the "0 fills" symptom.
2. **The counter-long never had a chance.** Tracked across the whole session, the counter-long's entry level (38.2% extension below the swing's low) stayed between 0.36 and 1.8 points away from the day's actual low at every single 15-minute check, all day. The reason is structural, not bad luck: the range24 anchor updates every closed hour, and on a smooth, continuous decline the anchor's own low falls almost as fast as the market's low does, so the extension level recedes ahead of price instead of waiting to be touched. A rolling 24h-range anchor is a poor foundation for a counter-trend extension precisely on the kind of day that motivated this question.

Under H4, today would have produced the same one with-leg loss the live sleeve already took the risk of, and the counter leg would have added nothing: not a fill, not a loss, not a save.

## What would change our mind

- A swing anchor that does NOT reset every hour (a confirmed pivot, like the zigzag the sleeve used before 2026-11-16, or a session-fixed range) would let a counter extension level sit still while price runs at it, instead of chasing it down. That is a different, larger study (this one stayed inside the live sleeve's own range24 anchor on purpose, per the question asked).
- A funding filter with real teeth needs either a cross-sectional panel (multiple correlated instruments, the shape that worked in the original D4/D5 research) or a much longer single-instrument history than 50 to 208 days; one instrument's z >= 2.0 events are simply too rare to filter on here.
- If a future test on a non-rolling anchor shows the counter leg filling more often AND winning more than it loses (not just filling more often), that is worth a second look. Filling more often alone is not the bar; this study's H4 proves more fills can mean more losses.

## Reproduce

    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.fib_bidirectional_test \
        --cache-dir <dir> --out-dir <dir>

    # a fast smoke run (6 variants, 2 datasets, ~10s)
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.fib_bidirectional_test \
        --cache-dir <dir> --out-dir <dir> --quick

    # gate tests (37, no network, well under 2s)
    .venv/bin/python -m pytest -q services/strategies/tests/test_fib_bidirectional.py

Outputs: `<out>/report.json` (datasets, the 33 variants at both risk sizes, the random baseline, the reality check, the bootstrap CI, the cost stress, the regime split, today's tape, the fixed holdout), `<out>/variants.csv` (one row per variant and risk size), `<out>/progress.log`. The full run (33 variants x 2 risk sizes x 7 datasets, plus halves, plus the extras) takes about 32 seconds on this machine; the quick run about 9.

## Where this would break

- **Intrabar tie-break is a convention, not a fact.** When both legs' levels are touched in the same 15-minute bar, the one closer to that bar's open is taken as having triggered first. OHLC data cannot say which one really did. This never changed a verdict here (ties were rare in the sample), but a different convention could, in principle, move a handful of trades between legs.
- **The counter leg's stop and target read the SAME closed-1h-bar data as the with-leg**, so it inherits every assumption `fib_retrace_test` already documents (ATR14 from closed 1h bars only, one entry per swing, no order in the weekend or the first bar after it).
- **Today's tape is one day.** It illustrates the mechanism (the anchor outruns price) clearly, but one day is an illustration, not a statistic; the 24-variant grid and the holdout carry the actual evidence.
- **H3's zero-trade result is itself evidence of rarity, not proof of absence.** A much longer Brent history might eventually produce enough crowded-long events to test properly; this repo's own `FUNDING_EXTREME_TEST_2026-11-03.md` already made that exact point for the plain fade-the-crowd trade.
