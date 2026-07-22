# W-Y3 — young_mover_short: entry-age window + exit geometry — REFUTED (no timing edge at any age or geometry)

Lane Y, 2026-07-22. Scripts: `hypotheses/W-Y3_fetch.py` (log parse + 1h/funding
caches), `hypotheses/W-Y3_geometry.py` (sweeps + nulls), `hypotheses/W-Y3_addendum.py`
(age-cell nulls + ledger grade). Data: `W-Y3_episodes.json` (150 PIT block
episodes from logs/trading_loop.log, 2026-06-28..07-22), `W-Y3_cache_1h.json`
(30 coins, through 07-22 05:00 UTC), `W-Y3_cache_funding.json` (14,582 hourly
rates), `W-Y_cache_xyz_daily.json` (W-Y0, full listing history, 86 coins).
Results: `W-Y3_results.json`.

## Question

The live book (`mover_recorders.young_short_live`, $20 / 10x / 6% stop / 1d
hold, xyz-equities-only) shorts EVERY history-floor-blocked young listing.
Is the edge concentrated in an age window, and is 1d/6% the right geometry?

## Method (rigor bar)

- **Population = the live signal, exactly**: every `history_floor_preflight`
  block in the loop's own log, deduped to (coin, UTC day), first block ts.
  135 equity episodes / 28 coins (+15 crypto episodes, reported at zero
  weight). Age = the log line's own `(Nd < 60d)` — PIT, no estimation.
- Entry = open of first 1h bar at/after the block (lookahead-safe; live
  enters at block-time mid, ~0-59 min earlier). One open episode per coin.
  Incomplete forward windows DROPPED unless the stop fired first.
- Costs: 50 bps RT + REAL accrued hourly funding (short receives +rate;
  sample mean +0.15 bp/h ≈ +3.7 bp/d to the short — negligible).
- OOS = chronological halves (h1 older / h2 newer).
- Null = 2000-iter same-coin random-entry-time shorts, restricted to bars
  where the coin was still <60d old, same window, same mechanics incl.
  funding. **EXCESS = real − null**; mc_p one-sided.
- Track B = 9-month daily-cache check (proxy signal |move|≥8% + $3M dvol,
  the dailyMover trigger; narrower than the live scan — stated caveat).

## (b) Hold × stop EV surface (Track A, xyz equities, net of costs+funding)

| | stop 6% | stop 10% | stop 15% | stop 20% |
|---|---|---|---|---|
| **hold 1d** | −0.38% n=97 | −0.31% n=96 | −0.13% n=96 | −0.10% n=96 |
| **hold 2d** | −0.63% n=70 | −0.73% n=70 | +0.21% n=68 | −0.00% n=68 |
| **hold 3d** | −0.33% n=62 | +0.47% n=61 | +2.23% n=59 | +2.38% n=59 |
| **hold 5d** | +0.01% n=53 | +1.55% n=52 | **+4.02% n=49** | **+4.38% n=49** |

Raw EV rises monotonically with hold and stop width — and it is 100% cohort
drift, 0% timing. The same-coin random-timing null rises right alongside:

| cell | n | real | null | **excess** | mc_p |
|---|---:|---:|---:|---:|---:|
| LIVE 1d/6% | 97 | −0.38% | +0.20% | **−0.58%** | 0.838 |
| 1d/15% | 96 | −0.13% | +0.29% | −0.42% | 0.740 |
| 2d/15% | 68 | +0.21% | +1.15% | −0.94% | 0.828 |
| 3d/15% | 59 | +2.23% | +2.22% | +0.02% | 0.500 |
| 5d/15% | 49 | +4.02% | +4.31% | −0.29% | 0.587 |
| 5d/20% | 49 | +4.38% | +5.07% | −0.69% | 0.676 |

**No geometry beats random-timing shorts on the same coins** (best excess
+0.02pp, p=0.50; the live cell is 0.58pp WORSE than random). The pretty
5d/20% raw number is the July xyz collapse (tape −9.43%/14d), harvested
equally well by entering at random hours. Stop-harvest rates: 6% stop is hit
on 37% of 1d trades and 66% of 5d trades; 20% stop on 3–14%.

## (a) Age-bucket EV (Track A, live 1d/6% geometry)

| age bucket | n | EV | win | h1 / h2 | excess vs null | mc_p |
|---|---:|---:|---:|---|---:|---:|
| 2–15d | 22 | −2.33% | 41% | −2.77 / −1.55 | −2.28pp | 0.953 |
| 15–25d | 20 | +0.84% | 45% | +0.78 / +0.89 | — | — |
| 25–40d | 25 | −1.21% | 40% | −2.09 / −0.52 | — | — |
| 40–60d | 30 | +1.25% | 67% | −0.24 / +3.21 | +1.16pp | 0.081 |

The pattern ZIGZAGS (− + − +). A real age effect would be monotonic; a
zigzag is noise partitioning — the same artifact W-Y1's runner study and the
history-floor validation both hit ("15-30d +0.52 then 30-60d −0.18 = noise").

The two best-LOOKING age cells, hunted explicitly and killed:

| cell | n | real | excess | mc_p | why it dies |
|---|---:|---:|---:|---:|---|
| 15–25d, 3d/15% | 12 | +10.17% | +6.99pp | 0.0125 | n=12 (< 30 = weak by our own bar); post-hoc best of a 36-cell scan → Bonferroni 0.0125×36 ≈ 0.45; **Track B 9-month same bucket/geom = −1.54% (n=22)** — sign flip |
| 40–60d, 3d/15% | 19 | +3.84% | +2.74pp | 0.038 | n=19; Bonferroni ≈ 1.0; **Track B 40–60d 3d/15% = −1.11% (n=30)** — sign flip |

Both nominal winners invert in the longer sample. No age window is credible.

## Track B — 9-month daily-cache check (proxy signal, net 50 bps, funding=0)

Pooled young window [2,59]: 1d/6% **−0.69%** (n=185, mc_p 0.625); 1d/15%
−0.64% (n=185); 3d/15% +0.24% (n=134); 5d/20% −0.07% (n=118, excess +1.67pp,
mc_p 0.0625 — not significant). Split by signal direction: shorting AFTER
up-days is the only faintly positive slice (+0.46..+2.86%), shorting after
down-days loses −1.89..−2.71% everywhere — the exact H2-short refutation from
W-Y1, reproduced. The live book is direction-blind at entry, so it owns both
slices.

## Reconciliation with the −2.71% prior that motivated the book

The prior (−2.71%/next-day for the blocked cohort, n=126, vs −0.13% mature
baseline) compared the young cohort to the MATURE tape — it proved the cohort
fell, not that block-timing adds anything. Re-run on the prior's own window
vs after:

| window | n | 1d unstopped net | excess vs same-coin null | mc_p |
|---|---:|---:|---:|---:|
| PRE 07-20 (the prior's sample) | 88 | +0.67% | +0.39pp | 0.291 |
| POST 07-20 (the bounce) | 10 | −7.87% | −8.11pp | 1.000 |

Even inside its own window the timing edge was never significant (p=0.29);
the cohort-drift component then reversed violently on the momentum-crash
bounce the 07-20 audit already named.

## Live forward ledger (the mandatory ≥8-episode review — now at 17)

`.state/shadow_ledger/young_mover_short.jsonl`: 27 rows, 17 resolved at
1d/6% from the 1h cache: **mean −5.33%/episode, win 12%, 14/17 STOPPED at
−6%.** Live arm (shadow=false): n=14, −5.12%/ep ≈ −$1.02/trade at $20
notional ≈ −$14 realized, each stop ≈ −51% of the $2 margin at 10x. Crypto
shadow rows (CASHCAT, GRAM) all stopped too (Track A crypto: 1d/6% n=9,
−5.16%, win 11% — stays zero-capital). 10 rows (07-22) still unresolved.

## (c) Best config

None promotable. The nominal best cell (age 15–25d, hold 3d, stop 15%:
EV +10.17%, mc_p 0.0125, n=12, OOS +11.33/+7.85) fails every robustness
check it faces: n<30, Bonferroni ≈ 0.45, and a sign-flip to −1.54% in the
9-month sample. The honest best-supported statement is: **there is no
(age, hold, stop) cell with a replicable timing edge.**

## (d) VERDICT: REFUTED — take the live arm to shadow

The live 1d/6% geometry is the single worst expression measured: negative EV
on its own population (−0.38%/ep backtest, mc_p 0.84), −5.33%/ep on the
forward ledger with an 82% stop-harvest rate, and worse than random-timing
shorts on the same coins. Wider/longer geometries only load more cohort beta,
which the 9-month sample says is not a stable edge (pooled 1d cells negative;
best pooled cell p=0.0625). Proposed config-diff (operator/parent to apply —
this lane is read-only on live config):

```
.agent-config.json  ->  mover_recorders.young_short_live.shadow_only: false -> true
```

Keep `mover_recorders.enabled=true` and the recorder itself: the zero-capital
ledger keeps accruing forward evidence (record-only rows continue, including
the crypto arm), and the REFUTED grade stays reversible if 60+ forward days
disagree. No age gate, no geometry change is worth wiring on this evidence.

## (e) Caveats

- Track A spans 25 calendar days dominated by one regime cycle (xyz collapse
  then bounce). The OOS halves are time-splits of that same cycle; effective
  independent sample is closer to ~18 block-days than 135 episodes.
- Track A is largely in-sample vs the prior that wired the book (same log
  window + 3 new days). The genuinely new evidence is (i) the same-coin
  random-timing null — which the wiring analysis never ran — and (ii) the
  post-07-20 forward ledger. Both refute.
- Track B's proxy (|move|≥8% + $3M) is a subset of the live scan surface
  (trend/vol triggers also admit); its age buckets use bar-index age, not
  log age. Funding=0 there (bias bounded: ~+4 bp/day pro-short).
- Entry at next-1h-open, not block-time mid: understates live fills by up to
  59 min. The ledger grade (entry_ref_px = actual recorded mid) closes that
  gap and is WORSE, so the approximation is not flattering the refute.
- Survivorship: universe = currently-listed coins; delisted xyz names
  invisible. Cuts against the short thesis (delistings would have paid it) —
  the refute stands on timing-null grounds regardless.
- 6 coins have no fundingHistory rows (NOK, NOW, PURRDAT, RKLB + partial
  pages); their funding = 0 in the sim. At ~4 bp/day the effect is noise.
