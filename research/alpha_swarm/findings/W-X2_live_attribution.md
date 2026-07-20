# W-X2 Part 1 — xs_momentum deep dive: settled knobs + live realized attribution

## A. Settled knobs (from existing findings — cited, not re-run)

| Knob | Winner | Loser(s) | Source |
|---|---|---|---|
| Ranker | **pct_k(14)** (keep) | raw-residual (paired −10.35bp/d, p=0.114, never beats), z_ext (near-tie, only defensible shadow) | ranker_ab (n=270 daily, overturns W-A4's thin 15-rebal result) |
| Within-leg weighting | **equal-weight** | rank (turnover eats the +0.15% gross), inv-vol (strictly worst, −30% EV) | W-A5 REFUTED |
| Vol-managed overlay (Barroso) | **none** | all 4 VM configs negative Sharpe lift (−0.23…−0.02) | A6 REFUTED |
| Factor combos | **best single alone** | mom+A13 (lift −0.084…−0.126 + imports down-beta), mom+funding (COMBO 4.47 ≤ MOM 4.56), any ensemble (A16) | W-A6, D3, A16 all REFUTED |
| 12-1 skip | **no skip** | skip 1/9 cells OOS-robust = chance | momentum_12_1_reversal REFUTED |
| Acceleration | (plain momentum) | ACCEL decays h1 +2.28 → h2 +0.06 dense-OOS | A7 MARGINAL, regime-loaded |
| Sector structure | **all-universe book** | sector rotation ≈0 + sign-flips; intra-sector < all-universe 4/4 cells | C14 REFUTED (re-confirmed in W-X2 cell B) |
| Beta handling | pct_k as-is; A13 satellite only if beta-neutral | short-deep leg = down-beta not alpha | W-A2/W-A3, W-A1 |
| Regime gate | **BTC low-vol gate** (edge lives in low vol) | — | edge_sweep3 (live vol_gate=True) |
| TS cousin | L30/H14 tsmom excess +1.17%/leg (MARGINAL, thin) | short holds H3-7 ≈ 0 | A2 |

Bottom line: the live construction (pct_k(14), equal-weight, market-neutral, low-vol-gated,
all-universe) is already at the settled optimum of the family. No overlay survived.

## B. LIVE book realized attribution (fills 2026-05-19 → 2026-07-19, 45d window)

**Attribution bug found:** `scripts/pnl_by_book.py:265` requires `not e.get("shadow", True)`,
but live `xs_rebalance` session events carry NO `shadow` key → all 5 live rebalance footprints
are treated as shadow and **every xs fill lands in "main-engine"**. The xs book is invisible in
`pnl_by_book --days 45` (0 attributed episodes despite 4 logged LIVE rebalances). Custom join
(session-log `xs_rebalance` open lists × fills, ±30min, coin+side): 15 episodes matched.

Live config as-run (hot `.agent-config.json`, drifted from committed defaults):
pct_k(14), lookback 7 (ignored by pct_k), **hold 5d, k=4/leg** (committed: 10d, k=8),
top-50 universe $5M floor, vol_gate on (all 5 rebalances fired "low"), vol_managed on
(scalar 1.0 — history too short), ~$50/leg notional.

Rebalances: 06-23 (16 legs, pre-log-rotation, no fills matched — likely blocked/shredded,
unverifiable), 06-26, 07-09, 07-14, 07-19.

| Slice | n closed | gross | fees | net | win |
|---|---|---|---|---|---|
| Long leg | 9 | +$6.83 | $0.99 | **+$5.89** | 7/9 |
| Short leg | 3 | −$0.40 | $0.11 | −$0.52 | 2/3 |
| BTC-up rebalances | 8 | +$3.13 | $0.90 | +$2.25 | 5/8 |
| BTC-down rebalances | 4 | +$3.30 | $0.20 | +$3.12 | 4/4 |
| **Total closed** | 12 | +$6.43 | $1.06 | **+$5.37** | 9/12 |

(3 legs still open; BTC longs of 07-09/07-19 and XPL short of 07-19 DID fill but the account
already held those coins → their PnL is mixed into main-engine episodes, unattributable.)

**The book never ran as designed.** Three structural mutilations, all with exact evidence:
1. **Shorts blocked** — 06-26: all 4 shorts rejected (`short floor $50M`); 07-09: all 4 rejected
   (`$20M`). The "market-neutral" book ran LONG-ONLY its first two cycles. Fixed by the $5M
   per-book override (9b87f36). 07-19 was the first cycle with a full short leg.
2. **Holds shredded** — median realized hold **0.06 days** (~1.4h) vs the 5d design. Legs were
   registered under the MAIN-ENGINE DSL (tight ATR stops 1.2-2.5% + TP scale-out at 1 ATR +
   30h timeout). Fixed by `dsl_exit_override` (e248c13, 2026-07-19 11:42) — AFTER the 07-19
   rebalance, so **no rebalance has yet held its 5d design**; the ~07-24 cycle is the first.
3. **Capital-capped** — k=4 not 8, $50 legs; 07-14 shorts died on `insufficient_free_margin`
   (main-dex equity $13.92 free at the time), 07-14/07-19 longs on `correlation cap 3/3`.

## C. WHY it earns (one paragraph, grounded)

The design earns from **cross-sectional extension dispersion harvested with discipline**: the
pct_k(14) channel rank picks coins at the top/bottom of their own 2-week range, and on our data
that spread pays only when BOTH legs run and the hold is long enough for the trend to work —
EV/rebalance grows monotonically with hold (W-X2 cell D: +0.83% @H3 → +4.28% @H20 at k8, all
p≤0.002 vs 2000 matched random books) while sub-5d holds collapse toward fees (A2/D3); equal
weight is optimal (W-A5), no overlay adds anything (A6/A16/W-A6/D3), the low-vol gate keeps it
out of the dead regime (edge_sweep3), and universe breadth is the fuel — sector-neutralizing
discards signal (C14). The realized +$5.37/45d (long leg +$5.89, shorts −$0.52) is NOT that
design edge: it is mostly quick TP-scale-out scalps on the long legs in an up-tape, because
until 2026-07-19 the executor shredded every hold to ~1.4h median and blocked the short leg
entirely for two cycles. The design's ~$9-19/wk paper EV at current sizing vs ~$0.8/wk realized
is an **execution gap, not a signal gap** — and the two fixes that close it (short-floor
override, dsl_exit_override) are both live as of e248c13 with zero rebalances yet run through
them.

## Follow-ups (not done here)
- Fix `pnl_by_book.py:265`: treat `xs_rebalance` events without a `shadow` key as LIVE
  (they are), or emit `book_open` events from `xs_momentum_live.py` like the other books.
- The next 2-3 rebalances (~07-24 onward) are the first clean test of the design live; grade
  them against the cell-D k4/H5 expectation (+1.10%/rebal net25) before touching sizing.
