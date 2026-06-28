# asymmetric_exit — can the EXIT make the take-all-breakouts book +EV by riding the fat tail?

**Verdict: NO. The thesis is REFUTED.** A wider trail does NOT win here. Even though this
set is selected for breakouts, the fat tail is far too THIN (3.7% of entries reach +50% MFE,
0.8% reach +100%) to pay for the ~60% that fizzle. The tight profit-floor (bank fast, the
KAITO/LIVE policy) is the best exit at every horizon and every entry-strength — but it banks
ZERO runners (cap50=0%); it wins purely on a ~90% win rate clipping +2% moves. No exit makes
the take-all book robustly +EV both OOS halves at a realistic 12bps. The EXIT is not the
missing edge; the fizzle rate (entry selection) is the binding constraint.

## Setup
- Book: take **ALL** early breakouts — new 48h high + up-bar + volume >= 1.5x trailing-48h
  mean + not extended (gain over last 12h <= 30%). High false-positive by design.
- Data: `movers_dataset.json`, 159 coins (ex-BTC), 1h candles, ~2000 bars. n=1161 entries.
- Lookahead-safe: decide on bars <= i, fill i+1 open. Intrabar exits **conservative** —
  the trailing floor is computed from the peak BEFORE the current bar's high, then peak
  updates (a single bar can't ratchet the trail up on its high AND get stopped on its low).
- Net of fees+slippage at 6/12/25bps round-trip. Headline = 12bps. OOS = both time-halves.
- Horizon 168h (7d) by default so wide trails have room to RIDE.

## The ceiling: MFE distribution of the take-all set (7d horizon)
| MFE bucket | count | share |
|---|---|---|
| [0%, 10%)   | 696 | 59.9% |
| [10%, 20%)  | 264 | 22.7% |
| [20%, 50%)  | 158 | 13.6% |
| [50%, 100%) |  34 |  2.9% |
| [100%+)     |   9 |  0.8% |

60% of breakouts never get 10% above entry. The "+100% runner" is 9 trades in 1161. Survivorship
makes even this an **upper bound** (dead pumps absent) — the real tail is thinner.

## Exit-policy sweep (n=1161, horizon 168h, net at 3 slippage tiers)
`cap50` = share of trades whose realized exit banked a >=50% gain (runner-capture).
✅ = +EV both OOS halves @12bps.

| policy | net%@6 | net%@12 | net%@25 | win | avgW% | avgL% | W/L | cap50 | h1/h2 @12 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **floor gb=.10 (LIVE, tight)** | **0.50** | **0.44** | **0.31** | 0.90 | 2.0 | -13.7 | 0.15 | 0.0% | +1.11 / -0.24 |
| asym stop8%->wide gb.65 @20% | 0.19 | 0.13 | -0.00 | 0.39 | 10.7 | -6.7 | 1.58 | 0.9% | +2.72 / -2.47 |
| floor gb=.35 | 0.15 | 0.09 | -0.04 | 0.90 | 1.7 | -13.7 | 0.12 | 0.0% | +0.79 / -0.62 |
| scaleout 50%@+2ATR ride gb.50 | 0.13 | 0.07 | -0.06 | 0.92 | 1.3 | -13.9 | 0.09 | 0.0% | +0.62 / -0.49 |
| asym stop5%->wide gb.50 @10% | 0.10 | 0.04 | -0.09 | 0.35 | 9.2 | -4.9 | 1.89 | 0.4% | +1.63 / -1.56 |
| floor gb=.50 | 0.01 | -0.05 | -0.18 | 0.90 | 1.5 | -13.7 | 0.11 | 0.0% | +0.68 / -0.78 |
| scaleout 50%@+3ATR ride gb.65 | 0.01 | -0.05 | -0.18 | 0.92 | 1.2 | -13.9 | 0.08 | 0.0% | +0.48 / -0.58 |
| atr 2x | -0.03 | -0.09 | -0.22 | 0.35 | 2.8 | -1.7 | 1.67 | 0.0% | -0.03 / -0.15 |
| floor gb=.65 (widest) | -0.15 | -0.21 | -0.34 | 0.90 | 1.3 | -13.7 | 0.10 | 0.1% | +0.45 / -0.87 |
| atr 4x | -0.17 | -0.23 | -0.36 | 0.36 | 4.9 | -3.1 | 1.56 | 0.1% | +0.02 / -0.48 |
| asym stop5%->wide gb.65 @15% | -0.23 | -0.29 | -0.42 | 0.30 | 10.3 | -4.8 | 2.13 | 0.5% | +1.61 / -2.20 |
| fixed stop 5% | -0.54 | -0.60 | -0.73 | 0.26 | 11.7 | -4.8 | 2.42 | 0.7% | +1.28 / -2.50 |
| hold 168h | -0.89 | -0.95 | -1.08 | 0.44 | 10.4 | -9.9 | 1.06 | 0.9% | +2.84 / -4.75 |

(Full 19-policy table in `asymmetric_exit.py` output.)

**Read:** every wide trail (floor gb>=.50, atr>=2x, the asym "cut-fizzles-then-ride") is net
**negative** at 12bps. The tight LIVE floor is the only clearly positive policy — and it is the
one that captures **0%** runners. The asymmetric-payoff policies do have the favorable W/L shape
(avg win 9-11% vs avg loss 5-7%, W/L > 1.5) but the win rate collapses to ~35-40%: the wide give-back
converts the 60% of fizzles into losses faster than the rare runner pays.

## Why the wide trail's apparent EV is a mirage — OOS sign-flip
Every wide-trail / asym policy posts a strongly positive first half and a negative second half
(e.g. asym8%->wide: **h1 +2.72 / h2 -2.47**). That is the signature of a handful of clustered
tail events, not a stable edge. Confirmed directly: of the 43 runners (MFE>=50%), the 6 biggest
(ORDI +331%, SAGA +190%, BIO +164%, APE +161%, NIL +155%, kNEIRO +131%) are **all in time-half1**.
The wide trail "works" only by banking those 6 monsters; half2 has no comparable tail, so it bleeds.
With 43 runners total across 37 coins, the wide-trail EV is dominated by <6 trades — untradeable.

## Robustness sweeps (LIVE-tight vs wide vs asym, net 12bps)
**(A) Horizon — am I clipping the tail?** Longer horizon raises the runner base rate but never
rescues the wide trail:
| horizon | runners | LIVE tight net (h1/h2) | wide gb.65 net | asym net (h1/h2) |
|---|--:|--|--|--|
| 72h  | 1.5% | +0.49 (robust ✅) | -0.09 | -0.06 (+1.81/-1.94) |
| 168h | 3.7% | +0.44 (+1.11/-0.24) | -0.21 | +0.13 (+2.72/-2.47) |
| 336h | 7.4% | +0.64 (+1.85/-0.59) | -0.11 | +1.16 (+4.80/-2.49) |

At 336h the asym posts the highest raw EV (+1.16%) — but it is 100% the first half (h1 +4.80 / h2 -2.49).
Not a clip-the-tail artifact; it's overfit to clustered runners.

**(B) Entry strength — does a harder vol-surge select a fatter tail so the wide trail wins?**
No. A 10x vol-surge raises the runner rate to 6.0% and makes the TIGHT floor stronger and robust
(+1.17%, h1 +1.66 / h2 +0.67 ✅), but the wide trail stays ~0/negative and the asym goes negative:
| volx | runners | LIVE tight (h1/h2) | wide gb.65 | asym (h1/h2) |
|---|--:|--|--|--|
| 1.5x | 3.7% | +0.44 (+1.11/-0.24) | -0.21 | +0.13 (+2.72/-2.47) |
| 3x   | 4.9% | +0.72 (+1.47/-0.05) | -0.00 | +0.11 (+2.84/-2.63) |
| 5x   | 5.4% | +0.74 (+1.42/+0.05) ✅ | -0.06 | +0.13 (+2.92/-2.67) |
| 10x  | 6.0% | +1.17 (+1.66/+0.67) ✅ | +0.11 | -0.42 (+2.38/-3.24) |

Selecting harder for the fat tail helps the **tight** floor, not the wide trail. The opposite of the thesis.

**(C) Optimistic-intrabar bound** (lets a bar ratchet the trail on its high then stop on its low —
favors wide trails): even then wide gb.65 = **-0.34** and asym = +0.12 (h1 +2.70 / h2 -2.47).
The wide-trail loss is not a conservative-accounting artifact.

## Reconciliation with the KAITO "tight floor optimal" finding
Same answer, and the reason is the same: **both sets are dominated by fizzles.** The hope was
that this set, being explicitly the breakout population, would be selected enough for the fat
tail that asymmetric payoff flips it. It is not — 60% of these breakouts never clear +10% MFE,
and the +50% tail is 3.7% (the +100% tail 0.8%). The arithmetic of a wide give-back (you hand
back 50-65% of every peak) loses on the fat fizzle body and the thin tail can't pay for it. The
tight floor banks the small move before it reverts, which is exactly what wins when the modal
outcome is a small pop that fades. Selection by volume surge sharpens the tight floor; it does
not manufacture a tail big enough for a wide trail.

## Bottom line
- **Winner: the LIVE tight profit-floor (gb≈0.10), bank fast.** ~+0.4–1.2% net/trade depending
  on entry strength; robust both halves once entry is vol>=5x or horizon<=72h. It captures NO
  runners by design — and that is correct, because the runners are too rare to chase.
- **No wide-trail / scale-out-and-ride / cut-fizzles-then-ride policy is +EV both halves** at any
  horizon, entry strength, or intrabar convention. Their favorable avg-win/avg-loss shape is real
  but is bankrolled by <6 time-clustered monsters; out of sample it bleeds.
- **The exit is not the lever for the take-all book.** The binding constraint is the ~60% fizzle
  rate at entry. To make asymmetric exits pay you need a real continuation filter that lifts the
  runner base rate well above ~5% (no swarm angle has found one), or a venue with a genuinely
  fatter, un-survivorship-biased tail. On this survivor universe the EV ceiling for "ride the
  tail" is an upper bound and it still loses.

Scripts: `asymmetric_exit.py` (19-policy sweep), `asymmetric_exit_robust.py` (horizon / entry-strength / intrabar bounds).
