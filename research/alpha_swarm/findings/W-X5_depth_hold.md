# W-X5 cell 1 — depth x hold frontier on the settled signal, plus the capital table

## Hypothesis
On the NEW live baseline (pct_k14, meme-excluded per W-X4/18622d3), does a deeper book
(k6/k8) or a longer hold (H20) dominate the live k4/H10 — and what funding-dex equity does
each expression actually require? W-X2-D scored k8/H20 at +4.28% gross UNfiltered; re-score
on the meme-excluded set. Expression question, not signal.

## Exact rule (pre-registered in hypotheses/W-X5_xs_implementation.py before first run)
- Data/engine: `W-X2_cache_daily.json` (2026-07-20, 401 bars), shared W-X2 engine. All
  books: pct_k14, top-50 minus the 20 declared MEME names, >=61-bar eligibility, start=66,
  equal weight, 25/50bps RT turnover-scaled, 2000-draw matched nulls, OOS count-halves.
- Reproduction gate: the k4/H10 grid point must equal W-X4 PRIMARY (+3.88/+3.68) — passed.
- Grid {k4,k6,k8} x {H10,H20}. Cross-H dominance convention (pre-registered): per-DAY net25
  EV AND ANNUALIZED Sharpe (Sh x sqrt(365/H)) must beat baseline in BOTH halves (4/4).
- $-lines: naive engine line at $76.8/leg (fixed) AND at the fixed margin envelope
  G_env = 0.8 x $65 x mean lev_eff = $415 (so depth cannot fake $-growth).
- Capital table from LIVE mechanics (executor.py:609,635): leg = frac x E x min(12, coin
  maxLeverage); margin/leg = frac x E exactly. Meta cached in W-X5_cache_meta.json.

## Results (offset-0 grid; $76.8/leg line = the old engine convention, $env = fixed-margin)
| k | H | n | gross | net25 | ann | OOS h1/h2 | Sharpe | null p | turn | $/wk @$76.8-leg | $/wk @env |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **4** | **10** | 33 | +3.88% | **+3.68%** | +134.4% | +4.34/+3.06 | +0.636 | 0.000 | 0.81 | +$15.83 | **+$10.69** |
| 4 | 20 | 16 | +6.08% | +5.85% | +106.8% | +6.53/+5.17 | +0.583 | 0.0005 | 0.89 | +$12.58 | +$8.50 |
| 6 | 10 | 33 | +3.25% | +3.06% | +111.6% | +3.68/+2.47 | +0.591 | 0.000 | 0.75 | +$19.73 | +$8.88 |
| 6 | 20 | 16 | +5.87% | +5.66% | +103.3% | +4.24/+7.08 | +0.610 | 0.000 | 0.84 | +$18.26 | +$8.22 |
| 8 | 10 | 33 | +2.75% | +2.58% | +94.0% | +3.23/+1.96 | +0.600 | 0.000 | 0.70 | +$22.15 | +$7.48 |
| 8 | 20 | 16 | +4.20% | +4.00% | +72.9% | +3.83/+4.16 | +0.571 | 0.000 | 0.81 | +$17.18 | +$5.80 |

Dominance vs k4/H10 (per-day EV + annualized Sharpe, both halves): **all five cells FAIL**
(k4/H20 0/4; k6/H10 1/4; k6/H20 1/4; k8/H10 1/4; k8/H20 0/4). Every cell is +EV with
p<=0.0005 — the signal re-confirms at every depth/hold — but nothing dominates the live cell.

Phase-mean per-day net25 (post-hoc, declared; offsets 0..H-1 — see W-X5_tranches.md for why
this matters): k4/H10 **+0.224%/day** > k6/H10 +0.188 > k8/H10 +0.177 > k4/H20 +0.172 >
k6/H20 +0.139 > k8/H20 +0.116. The offset-0 verdict is phase-robust: per-day EV falls
monotonically in BOTH k and H. The naive $76.8/leg column inverts this ordering (k8/H10
"+$22.15/wk") only because it hands deeper books 2x the margin — the envelope column is the
real comparison, and k4/H10 wins it.

## Capital table (from live executor mechanics + real HL leverage caps, meta 2026-07-20)
Eligible (meme-excluded) lev_eff = min(12, cap): min **3x** (ACE, AZTEC, SUSHI, VVV),
mean 7.98x. Per-leg margin = frac x E exactly; util budget U = 0.8 (the live k4 envelope);
frac_k = 0.4/k (/tranches). Min-order $10.50 binds FIRST on the 3x-cap legs.
| k | tranches | frac/leg | E_min strict | E_min clean (2x min-order) | legs @$65 (3x/10x/12x) | @$150 | @$300 |
|---|---|---|---|---|---|---|---|
| 4 | 1 | 0.1000 | **$35** | **$70** | $20/$65/$78 | $45/$150/$180 | $90/$300/$360 |
| 4 | 2 | 0.0500 | $70 | $140 | $10/$32/$39 | $22/$75/$90 | $45/$150/$180 |
| 6 | 1 | 0.0667 | $52.50 | $105 | $13/$43/$52 | $30/$100/$120 | $60/$200/$240 |
| 6 | 2 | 0.0333 | $105 | $210 | $6/$22/$26 | $15/$50/$60 | $30/$100/$120 |
| 8 | 1 | 0.0500 | $70 | $140 | $10/$32/$39 | $22/$75/$90 | $45/$150/$180 |
| 8 | 2 | 0.0250 | $140 | $280 | $5/$16/$20 | $11/$38/$45 | $22/$75/$90 |

Structural facts the 12x mental model hides:
- **Depth is margin-gated, not equity-gated.** At the literal live per-leg frac (0.10), 2k
  legs consume 0.2k x E margin: k>5 exceeds equity at ANY size. Expressing k6/k8 is a
  paired (k_per_leg, strategy_book_equity_frac) config change (frac = 0.4/k), not a
  capital milestone.
- **The real capital cliff is BELOW us, not above:** under $35 equity the 3x-cap legs
  (ACE-class) fall below the $10.50 min order and the executor starts silently dropping
  book legs (`below_min_order_notional`). $70 is the clean tier (every leg >= 2x min order).
- **Live legs are lev-cap-weighted, not equal-weight** (a 3x coin gets $20 where a 12x coin
  gets $78): sims score equal-weight books. Scored directly (post-hoc, k4/H10, 10 offsets):
  equal-weight +2.24%/rebal vs lev-weighted replica +2.28%, delta +0.035%, 6/10 offsets.
  **Divergence benign — concern closed**, no follow-up needed.

## VERDICT: **REFUTED as an upgrade — k4/H10 meme-excluded IS the depth/hold frontier.**
Deciding numbers: 0/5 cells pass the pre-registered 4/4 dominance gate, and the phase-mean
per-day EV ordering (k4/H10 +0.224%/day, monotone decline in k and H) confirms it is not an
offset-0 artifact. H20 cells earn more per rebalance (+5.85% at k4) but less per day and per
$ of margin, at n=16. Dominance and significance separated: every cell IS significantly +EV
(p<=0.0005) — the family is real; the live expression is already the best of it.

## GROWTH LADDER (pre-committed — the cell 1 deliverable)
- **$65 (now):** k4/H10, frac 0.10 — the live config. Strict-expressible (all legs >=
  $10.50); not yet clean (3x-cap legs $19.50 < $21).
- **$70+:** same config crosses the CLEAN tier. No action, just note it.
- **$150:** k8 and 2-tranche-k4 become clean-expressible ($140) — **do not flip**: k8 loses
  per-day EV (this cell) and tranching is refuted (W-X5 cell 2). Capital goes into leg
  size at k4/H10, not depth.
- **$300:** same. No equity threshold flips the config on current evidence; revisit depth
  only if a future cell shows a deeper book dominating phase-robustly.
- **Guard (the one actionable cliff):** if funding-dex equity ever drops below ~$35, the
  book starts silently losing its low-cap legs to the min-order gate — treat <$40 as a
  "book integrity" alert, not a sizing detail.

Caveats: survivor-biased top-50 cache (upper bounds); H20 cells n=16; funding not modeled;
count-halves approximate calendar halves; capital table assumes the current top-50 cap
distribution (re-check W-X5_cache_meta.json if HL re-tiers leverage).

## Scoreboard line
W-X5 depth_hold: REFUTED as upgrade — 0/5 of {k4,k6,k8}x{H10,H20} dominate live k4/H10
meme-excluded (net25 +3.68%, Sh 0.636); phase-mean per-day EV falls monotonically in k and
H (k4/H10 +0.224%/day best). Capital table from real executor mechanics + HL caps: depth is
margin-gated not equity-gated (k6/k8 need frac=0.4/k, any equity); real cliff is E<$35
(3x-cap legs breach $10.50 min order); $70 = clean tier. Lev-cap-weighted live legs vs
equal-weight sim: +0.035% delta, benign, closed. Growth ladder: k4/H10 at every rung.
