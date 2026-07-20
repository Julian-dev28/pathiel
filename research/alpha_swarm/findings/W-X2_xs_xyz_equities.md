# W-X2 cell A — xs_xyz_equities: the xs recipe on the xyz tokenized-stock universe

## Hypothesis
The validated crypto xs-momentum recipe transfers to the never-momentum-tested xyz dex
(tokenized equities, 24/7): rank 7d residual momentum within xyz only, long top-k / short
bottom-k, 5d hold, market-neutral.

## Exact rule (pre-registered in hypotheses/W-X2_xs_widening.py docstring before first run)
- Data: HL daily candleSnapshot, ALL 87 xyz markets, fetched 2026-07-20
  (`W-X2_cache_daily.json`); xyz max history ~281d (XYZ100), median 118d. First rebalance
  with enough eligible names: 2026-01-27; 34 non-overlapping rebalances to 2026-07-11.
- Universe: xyz EQUITIES only — declared exclusion list `NON_EQUITY_XYZ` (indices,
  commodities, fx, and the PURRDAT/DRAM baskets). Eligible at decision bar i: >=61 completed
  daily bars AND 30d mean daily notional >= $250k (thin-book floor).
- PRIMARY: score = 7d trailing return residual vs xyz:XYZ100 (OLS beta on 30 daily rets,
  beta=1 fallback <8 pts); long top-5 / short bottom-5, equal weight; decide bar i, fill
  open[i+1], exit open[i+1+5]. Declared robustness variants: raw 7d, pct_k(14), k=8.
- Costs: tier bps = round-trip per replaced position, turnover-scaled. Null: 2000 matched
  random books, same dates/eligible sets/fills, p on gross EV. OOS = rebalance halves.
- Verdict gates as pre-registered (ROBUST = net25>0 AND both OOS halves>0 AND p<0.05 AND n>=15).

## Results (per-rebalance per-leg signed EV)
| variant | n | gross | net25 | net50 | ann net25 | OOS h1/h2 | Sharpe | null p | $/wk @live sizing |
|---|---|---|---|---|---|---|---|---|---|
| **resid7 k5 H5 (PRIMARY)** | 34 | +0.81% | **+0.65%** | +0.49% | +47.2% | **+0.18 / +1.12** | +0.217 | **0.0055** | +$6.95 |
| raw7 k5 H5 | 34 | +1.03% | +0.86% | — | +62.9% | +0.67 / +1.05 | +0.315 | 0.0010 | +$9.27 |
| pct_k14 k5 H5 | 34 | −0.08% | −0.24% | — | −17.7% | −0.30 / −0.18 | −0.095 | 0.60 | −$2.61 |
| resid7 k8 H5 | 30 | +0.40% | +0.25% | — | +18.5% | +0.36 / +0.15 | +0.108 | 0.087 | +$4.36 |

Floor sensitivity (primary): $250k → +0.65 (p=0.0055); $500k → +0.61 (p=0.010); $1M → +0.49
(p=0.033). Degrades gracefully, survives every floor and 50bps.

Exploratory (declared as such): k4 H5 +0.84% (p=0.003, OOS +0.53/+1.14); H10 +1.31% (p=0.02,
OOS +0.15/+2.35, n=17); H3 +0.20% (p=0.063) — same H-monotonicity as crypto (cell D).

## Diagnostics (the honest part)
- **Leg split:** long leg +1.30%/rebal, short leg +0.31% (signed). Both positive; long-dominant.
- **Driver is named: the semiconductor/memory supercycle.** Most-longed: SNDK(13), INTC(11),
  CRCL(10), AAPL(9), AMD(9), MU(9). Top contributors: INTC +9.5pp, SNDK +8.0pp, CRCL +5.7pp,
  AMD +5.0pp, SKHX +4.1pp of the +27.4% total. Most-shorted: HOOD(13), COIN(13), MSTR(13) —
  the crypto-beta equities. **Excluding all 14 semi names: net25 collapses to +0.08% (p=0.25).**
  The edge as observed IS the semis-dispersion trend — which is what momentum is supposed to
  ride, but the forward expectation dies with the theme unless a new dispersion theme replaces it.
- Concentration: top-2 of 34 rebalances = 40% of total EV; median rebalance +1.27%; worst −6.79%.
- Eligible depth: min 10 (= exactly 2k, early), median 28, max 45 — k=5 is the depth ceiling.
- pct_k(14) does NOT transfer to equities (−0.24%): the crypto channel ranker is not the thing
  that works here; plain/residual trailing return is.

## VERDICT: **ROBUST** (all four pre-registered gates pass on the PRIMARY) — with a named,
## pre-committed decay risk: the EV is currently carried by the semis-supercycle dispersion.
Deciding numbers: net25 **+0.65%/rebalance** (+47% annualized), OOS **+0.18/+1.12 both
positive**, null **p=0.0055** (2000 matched random books), n=34, survives 50bps and a 4x
liquidity-floor tightening. Caveats: ~6 months of data on a young universe (survivorship mild
— xyz has few delistings, but young listings enter as they season); books are thin ($250k/d
median-floor names — fine for $50 legs, not for size); 24/7 bars include stale weekend prices.

## SPEC — implementable block (operator pre-authorized wiring of a ROBUST cell A)
```
book            xs_xyz_equities        (new claim book — MUST join _ACTIVE_CLAIM_BOOKS and
                                        pnl_by_book.py BOOK_PRIORITY, and emit book_open events)
universe        xyz-dex perps, EQUITIES ONLY: exclude NON_EQUITY_XYZ (indices/commodities/fx +
                PURRDAT/DRAM baskets) — list in hypotheses/W-X2_xs_widening.py
eligibility     >=61 completed daily bars AND 30d mean daily notional >= $250k, per rebalance
score           r7(coin) − beta·r7(xyz:XYZ100); beta = OLS on last 30 daily rets (1.0 if <8)
book            long top-5 / short bottom-5, EQUAL weight (k=4 acceptable if margin-tight)
hold            5 days, non-overlapping; the rebalance owns exits on its own clock
exits           dsl_exit_override wide-only, exactly like e248c13: 20% disaster stop,
                hard_timeout = hold_days·1440 min, NO breakeven/trail/stale-flat/ATR
short floor     min_short_volume_usd_override = 250_000 (the global $20M floor would block
                EVERY xyz short — the exact failure that ran the crypto book long-only)
sizing          strategy_book_equity_frac 0.1 × 12x ⇒ ~$50-77/leg at current equity
                (~$500 gross / ~$42 margin at k=5); per-coin lev = min(12, xyz coin cap)
expected        ~+$7/wk at current sizing (net25 +0.65%/rebal × ~$768 gross × 7/5);
                scale linearly with equity, capacity-capped by thin books well before $1k legs
KILL (pre-committed)
                1. cumulative fwd net25 EV < 0 after 12 rebalances (~60d) → shadow_only same day
                2. any single rebalance book EV < −8% (worst observed −6.79%) → shadow_only
                3. semis-theme check at rebalance 6: if the no-semis ablation of FORWARD data
                   is also ≈0 and semis dispersion has compressed, halve size preemptively
```

Fees note: assumed 25bps RT/position (survives 50bps: +0.49%/rebal). xyz HIP-3 builder fees
differ from main-dex — verify realized fee bps on the first live rebalance against this.
