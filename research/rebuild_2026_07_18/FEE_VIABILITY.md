# FEE_VIABILITY — can a $19 account trade these edges net of fees?

2026-07-18. Equity $17.83 (.agent-memory.json, live read). READ-ONLY analysis.
Calc script: scratchpad viability.py (session), all numbers reproduced below are deterministic.

## 0. Measured cost model (369 real closes, 06-15 to 07-18, .agent-memory.json)

All costs in bps of NOTIONAL. Leverage does not change fee $ at fixed notional; fees are charged on notional (dashboard.py:57-58, verified against fills).

| component | median | mean | p90 | source |
|---|---|---|---|---|
| round-trip taker fee | 5.0 | 4.4 | 5.0 | fee_usd/notional_usd over 369 closes; = 2.5bps/side (HL_TAKER_FEE_PCT=0.025, hermes_trader/dashboard.py:57) |
| round-trip slippage | 7.3 | 11.1 | 24.0 | entry_slip_bps + exit_slip_bps, n=187 |
| funding, per hour held | +0.121 | +0.176 | +0.796 | funding_cost_usd/notional/hold_h, n=150 (longs typically pay; crowded-long shorts collect) |

All-in median round trip ~12.3bps + 0.12bps/hour of hold. The alpha_swarm "net25" convention (25bps) is confirmed conservative at current size.

## 1. Per-edge table (surviving edges only)

Gross = backtest net25 + 25bps. Net = gross minus measured median cost at that hold. Sizing = what the live config actually produces today (.agent-config.json + executor.py:601-635 + risk_gates.py:277).

| edge | gross EV/ep | hold | RT cost @ $20 ntl | RT cost @ 12x, same $20 ntl | net EV/ep | notional now | eps/wk observed | $/wk at current sizing |
|---|---|---|---|---|---|---|---|---|
| extreme_fade (long −12% crash, 20% stop) | +4.35% | 3d | 21.0bps = $0.042 | identical (fee is on notional) | +4.14% = $2.07/ep @ $50 | $50 (eq×0.4×12=$86 clipped by strategy_book_notional_usd=50) | 5.7 signals/wk, but only ~0.8/wk ARMED (skew gate, 6 of 7 disarmed since 07-09) | $11.80 ceiling; ~$1.7-2.9 armed-only |
| funding_spike_short (z≥2, 15% stop) | +6.25% | 5d | 12.3bps = $0.025 (short collects funding, +0.19%/ep already in net25) | identical | +6.13% = $1.23/ep | $20 | 1.9/wk expected (n=25/90d, W-F2); 0 observed in 8.6d live | $2.38 expected, $0 observed |
| xs_momentum (8-leg residual book) | +1.40%/rebal (honest live est; +2.01% validated at LB14/K8, module docstring — live runs LB7/K4) | 5d | 26.8bps of book | identical | +1.13% = $0.95/rebal @ $84 book | cannot express: fallback sizes legs at $50 → $400 target book > $178 notional cap (10× equity); partial book breaks the long-short spread structure | 1.4 rebal/wk | ~$1.33 IF expressible; realistically ~$0 |
| **total** | | | | | | | | **$15.51/wk theoretical ceiling (87%/wk of equity); ~$4-6/wk honest current-tape** |

Reality check: realized last 7d = **−$22.77** on 33 closes ($0.96 fees); realized 32d = **−$199.32** ($32.11 fees, $1.08 funding). The gap between the +$15/wk ceiling and −$23/wk realized is not book fees. It is (a) the main engine (0.5eq×12x, median hold 54 min, mid-conf AI longs measured −2.13%@24h, W-G1), (b) the fade book mostly disarmed in this tape, (c) funding_spike not firing, (d) backtest EVs being survivorship-flattered upper bounds.

If "$20/12x" is read as $20 MARGIN at 12x ($240 notional): fees scale ×12 to $0.30/RT (still trivial vs edge) but one 15% stop = −$36 = 2× the whole account. Dead on the first stop. The config reads $20 as notional (executor.py:601 `strategy_book_notional`), which is the only sane reading at this equity.

## 2. Breakeven gross edge per episode, by hold (fee + slippage + funding)

| hold | breakeven @ median costs | breakeven @ p90 costs |
|---|---|---|
| 1h | 12.4bps | 29.8bps |
| 6h | 13.0bps | 33.8bps |
| 1d | 15.2bps | 48.1bps |
| 3d | 21.0bps | 86.3bps |
| 5d | 26.8bps | 124.5bps |

The surviving edges clear breakeven by 15-50×: extreme_fade 435bps gross vs 21bps cost, funding_spike 625bps vs 12bps. Fee viability per episode is NOT the problem at any hold ≥6h. This re-confirms W-G1 (findings/W-G1_meta_alpha.md): only holds ≥6h are fee-viable; sub-1h holds need >37bps and measured −0.67% gross at <15m (adverse selection, not fees). Live median hold is 54 minutes — the account's actual trading lives in the one bucket that is measured −EV.

## 3. Churn budget at $19 equity

Best-case weekly edge = $15.51 (table above; honest current-tape ~$5). Round trips per week that fully consume it:

| churn profile | cost/RT | RT/wk to erase $15.51/wk |
|---|---|---|
| fee only, $20 ticket | $0.010 | 1,551 |
| fee+slip, $20 ticket | $0.025 | 631 |
| fee only, main-engine ticket ($107 = eq×0.5×12) | $0.054 | 290 |
| fee+slip, main-engine ticket | $0.132 | 118 |
| all-in sub-1h churn @ measured −0.4% gross, main ticket | $0.428 | **36** |
| all-in sub-1h churn @ −0.4%, $25 median recent ticket | $0.100 | 155 |

Observed churn: **80 closes/wk** (32d avg), 33/wk recent, 22 of last 33 held under 6h. Against the honest ~$5/wk edge, the budget is ~12 sub-1h main-engine round trips per week. The system runs 5-7× over budget. This is the same mechanism as the −$206/8wk baseline (.monitor-journal.md:4879, $115 of it churn fees) and the churn backtest (journal:4814: fees $1088 = 74% of the simulated loss) — at $200 equity it showed up as fee bleed; at $19 it shows up as adverse selection on 54-minute holds.

## 4. Ruin math and minimum account

Fees don't kill this account. Stops do:

| position | stop loss $ | % of $17.83 equity |
|---|---|---|
| extreme_fade, $50 ntl, 20% stop | −$10.00 | **56%** |
| funding_spike, $20 ntl, 15% stop | −$3.00 | 17% |
| $20 ntl, 20% stop | −$4.00 | 22% |
| min ticket $10.5, 20% stop | −$2.10 | 12% |

One stopped extreme_fade episode = −56% of the account. Two = gone. At the validated 62% win rate that is not a tail event, it is an expected event within weeks. The kill switch (max_daily_loss_usd = −100) is unreachable: the account cannot lose $100. Margin is cross (exchange.py), so the 15-20% stops are real per-position, but the 10×-equity notional cap ($178) allows an aggregate 10% adverse move to wipe the account.

Minimum account size, by constraint (min order = $10.50, exchange.py:24 MIN_ORDER_USD):

| constraint | minimum equity |
|---|---|
| clear fees 2:1 per episode, any surviving book | any size ≥ min ticket (edges clear 15-50×) |
| per-stop loss ≤ 5% of equity at min ticket ($10.5, 20% stop) | $42 |
| per-stop loss ≤ 5% of equity at $20 tickets | $80 |
| express all books concurrently at min ticket (~12 legs = $124 ntl) at ≤1.5× gross | $83 |
| same at $20 tickets (incl. full 8-leg xs_momentum book) | $157 |

**The number: ~$80 minimum, ~$150 to run the validated $20-ticket structure with per-stop risk ≤5% and the book set fully expressible.** (Consistent with the 07-11 handoff plan: at $100, kill −$25 and $30 clips.)

## 5. Verdict

**A $19 account is mathematically able to clear fees on the surviving edges — fee viability was never the binding constraint at ≥6h holds — but it cannot compound as currently run, and the fix is (a)+(b), in that order.**

1. Fees at $20 notional and 3-5d holds are 12-27bps against 435-625bps edges. Round-trip cost is a rounding error on the books.
2. What actually burns the account: the main engine's 54-minute median holds (measured −EV bucket, 80 RT/wk vs a ~12/wk budget), and per-stop losses of 17-56% of equity. That is churn plus ruin variance, not fee arithmetic.
3. (a) fewer/longer trades: mandatory. Sub-6h trading at this equity is measured −EV before fees and −EV after. The main engine at 0.5eq×12x is the leak; the three surviving books alone would have a positive expected week (~$4-6 honest, $15 ceiling).
4. (b) more capital: mandatory to run the validated structure. **$80 minimum, $150 comfortable.** Below that, position minimums ($10.50) force per-stop risk ≥12% of equity and xs_momentum's 8-leg book cannot exist inside the 10×-equity notional cap.
5. (c) stop: not required by the math, but at $19 the honest expected path even with perfect discipline is ~$4-6/wk with per-trade drawdowns of 12-22% of equity. That is a coin-flip random walk with a positive drift smaller than its noise. Compounding begins at (b).

Sources: hermes_trader/dashboard.py:57, hermes_trader/client/exchange.py:24, hermes_trader/agents/executor.py:601-635, hermes_trader/agents/risk_gates.py:277-284, hermes_trader/agents/extreme_fade_live.py:270-306, .agent-memory.json (369 closes), .agent-config.json, .state/shadow_ledger/*.jsonl, research/alpha_swarm/findings/{extreme_surface.md,W-F2.md,W-G1_meta_alpha.md}, .monitor-journal.md:4814,4879, HANDOFF-CLAUDE.md.
