# Loss audit and fix backtest, 2026-11-02

**Verdict: no fix clears the bar. Keep the live rules.** We are not
unprofitable overall: since 10-20 the two desks made **+$31.47** on 10
closed trades. Since 10-31 they lost **-$30.42**, and one trade did more
than all of it: the Mon 11-02 Brent long, a wrong-way call at full 20x
size, **-$33.87 (13% of the account, 14% of the oil desk)**.

Size is the only lever that shrinks that kind of loss. No size cut beats
live on most datasets: October Brent trended up and the desk was long,
so a smaller bet made less. If you want to cap a stop's cost anyway, it
is one config value (end of the doc). That is a risk call, not a tested
edge.

Operator, 2026-11-02: "can u backtest the solutions to what made us
unprofitable, fix the issues and show me the results".

## 1. What made us unprofitable

Every closed xyz:BRENTOIL and xyz:SILVER trade since 2026-10-20, rebuilt
from the wallet's own fills (public `userFills`, `historicalOrders`,
`portfolio`; script `loss_audit.py`). Net = closedPnl less every fee on
the trade. % = of account equity when the position reached full size.

| # | Trade | Exit | Net | % acct | Tags |
|---|---|---|---|---|---|
| 1 | Brent L 10-22 16:13Z | target | +1.05 | +6.9 | |
| 2 | Brent L 10-23 07:34Z | target | +3.81 | +23.4 | |
| 3 | Brent L 10-23 16:34Z | stop 10-25 | -6.04 | -9.4 | round trip |
| 4 | Brent L 10-26 01:10Z | stop | -5.81 | -10.0 | stop too tight, size |
| 5 | Brent L 10-26 03:13Z | target | +19.86 | +33.5 | |
| 6 | Brent L 10-27 14:33Z | target 10-30 | +49.02 | +66.2 | |
| 7 | Brent L 10-31 16:18Z | by hand 11-01 | +7.83 | +2.8 | round trip |
| 8 | Silver S 11-02 00:51Z | stop | -1.82 | -0.7 | stop too tight, round trip |
| 9 | Silver S 11-02 01:18Z | stop | -2.56 | -1.0 | round trip |
| 10 | Brent L 11-02 03:50Z | stop | -33.87 | -13.0 | wrong direction, size |

Tag rules (code, not judgement): **stop too tight** = exited on its stop
order and price traded back to the entry within 12h; **wrong direction**
= a loss and the desk's thesis flipped side within 12h of the exit;
**round trip** = best price reached 1R or more, then closed under 0.5R;
**size** = a loss over 10% of account equity.

Cause table, primary cause (first tag in that order), so rows add up:

| Cause | Since 10-31 | Since 10-20 |
|---|---|---|
| Wrong direction | 1 trade, **-$33.87** | 1, -$33.87 |
| Stop too tight | 1, -$1.82 | 2, -$7.62 |
| Round trip | 2, +$5.27 | 3, -$0.77 |
| Clean | 0 | 4, +$73.75 |
| **Total** | 4, **-$30.42** | 10, **+$31.47** |

By tag (a trade counts under each tag) and what a fix could reach:

| Tag | Trades | Lost on them | $ a fix could reach |
|---|---|---|---|
| Size | 2 | -$39.68 | $7.79 above the 10% line |
| Wrong direction | 1 | -$33.87 | $33.87 |
| Round trip | 4 | -$10.42 | $16.69 (exit up to +0.5R) |
| Stop too tight | 2 | -$7.62 | $7.62 |

Ranked by dollars: **wrong direction x size**, then round trips, then
tight stops. The 11-02 long had both top tags: the short thesis came 5
minutes after the stop, and full port at 20x on a 0.76 stop is 12% of
the account per stop before fees and slip. The two tight stops were silver (already fixed live
by the 0.8% floor, 9224cbf) and the 10-26 01:10Z Brent long, whose
re-entry 17 minutes later made +$19.86.

The 11-02 long opened 2 minutes after its thesis was written (03:48Z) at
110.01, under the 24h low (110.14): it bought a break. The 02:00Z hour
had closed red. Those three facts are what the filters below test.

## 2. Fixes, one per cause

All runs: $64, live rules otherwise (market entry off the 24h range, 2R,
target 0.5% inside the far edge, limit target, 20x, 15m ticks; Brent with
funding and size slippage; silver 1.3 x ATR14 with the 0.8% floor). 16
datasets: Brent journal (18 live theses), 30 replayed theses, always
long, always short and daily sign on the replay window and the last 30
days; the same for silver (live journal: 5 theses, 2 days). Data to
2026-11-02 09:30Z. Rules: beats live on most datasets, a plateau on its
grid, stop inside half the way to liquidation (all variants here use the
live gate, so that one holds by construction).

Two more lenses on the 10 real trades: each sizing applied to the actual
fills; each filter checked at the actual fill minute.

### Size (cause: size)

20x full port (live) vs lower leverage, and a risk cap: size so a stop
costs at most X% of desk equity, still 20x. P(ruin) = share of 2,000
seeded bootstrap paths of each run's trades that dip under half the
start. Means over the 8 datasets per desk.

| Brent | Beats | Geo-mean | Journal $ | Worst trade | Max DD | P(ruin) |
|---|---|---|---|---|---|---|
| Live 20x | | 1.00 | 351 | -25.0% | 70% | 0.37 |
| 15x | 3/8 | 1.00 | 255 | -21.3% | 62% | 0.29 |
| 10x | 3/8 | 0.89 | 174 | -14.2% | 48% | 0.20 |
| 5x | 2/8 | 0.70 | 110 | -7.1% | 29% | 0.08 |
| Cap 20% | 6/8 | 1.10 | 330 | -20.4% | 69% | 0.34 |
| Cap 15% | 3/8 | 1.10 | 254 | -15.6% | 63% | 0.29 |
| Cap 10% | 2/8 | 0.96 | 176 | -10.4% | 49% | 0.21 |
| Cap 5% | 2/8 | 0.73 | 112 | -5.2% | 29% | 0.09 |

| Silver | Beats | Geo-mean | Theses $ | Worst trade | P(ruin) |
|---|---|---|---|---|---|
| Live 20x | | 1.00 | 253 | -20.4% | 0.32 |
| Cap 15% | 3/8 | 1.17 | 197 | -13.6% | 0.30 |
| Cap 10% | 3/8 | 1.16 | 147 | -9.0% | 0.29 |
| 10x | 3/8 | 1.11 | 151 | -10.9% | 0.29 |

Real trades, net $:

| | Live | 10x | Cap 20% | Cap 15% | Cap 10% | Cap 5% |
|---|---|---|---|---|---|---|
| Since 10-20 | +31.47 | +19.28 | +31.47 | +33.75 | **+39.74** | +19.91 |
| Since 10-31 | -30.42 | -15.28 | -30.42 | -28.14 | **-17.61** | -8.93 |
| 11-02 long | -33.87 | -17.07 | -33.87 | -31.59 | -21.06 | -10.53 |

**Verdict: none clears.** Every cut loses on both thesis sets (journal
$351 to $254 at a 15% cap); the geo-mean above 1 comes from the controls.
Cap 20% beats 6/8 but its neighbour 15% beats 3/8 (no plateau), and on
the real trades it never binds. The trade-off is plain: a 10% cap halves
the worst trade and cuts P(ruin) from 0.37 to 0.21, and costs 4% of
geo-mean final equity on Brent. On the 10 real trades a 10% cap would
have made $8.27 more: +$12.81 on the 11-02 long, -$5.05 on the 10-27
winner.

The oil brief's ladder says the same thing in its own words (20x
"avoid", P(liq) ~51-55%): live sizing is a bet on being right.

### Wrong direction (cause: thesis flip)

| Filter | Brent beats | Brent geo | Silver | Journal $ | Real: refused at the fill minute |
|---|---|---|---|---|---|
| No entry 15 / 30 / 60 min after a rewrite | 0 / 2 / 3 of 8 | 1.00 / 1.00 / 1.03 | ties | 351 (tie) | 2 losers, -$36.43 |
| 120 min | 2/8, loses 2 | 0.96 | ties | 338 | 5: -$42.24 saved, +$4.86 missed |
| Side held 2 / 4 / 8 / 12h | 2 / 2 / 3 / 2 of 8 | 1.02 / 1.02 / 1.04 / 0.98 | ties | 351 (tie) | none |
| Last 1h candle agrees | 0/8 | 0.42 | 3/8, 0.84 | 104 | 8: -$44.06 saved, +$32.55 missed |
| No buy under the 24h low (no break) | 1/8 | 0.66 | 3/8, 1.26 | 236 | 5: -$38.25 saved, +$56.85 missed |
| Thesis levels, limit at thesis entry | 0/8 | 0.70 | 0/8, 0.97 | 145 | n/a |
| Resting limit at the 24h edge | 2/8 | 0.52 | 2/8, 0.40 | 134 | n/a |

**Verdict: none clears.** The delay is the only filter that refused just
losers on the real fills (the 11-02 Brent long, 2 minutes after its
rewrite, and silver #2, 15 minutes after), but the sim cannot test it:
its 15-minute ticks saw the 11-02 entry under the previous long thesis,
and the replayed theses rewrite once a day. n = 2. A refused entry is
not a saved loss either: in the sim, no-break and confirm-1h only moved
the 11-02 long later (04:00Z, 05:15Z) and still lost on the 08:40Z flip,
-9.3% and -10.5% of equity instead of -16.5%. The call was wrong; no entry
rule in this data fixes a wrong call without cutting the winners.

### Round trip

The profit lock (stop to entry + Y R once X R in favour), 24/7 and
weekend only.

| Lock | Brent | Silver | Journal $ | Real since 10-20 |
|---|---|---|---|---|
| Live | | | 351 | +31.47 |
| Weekend, 1R to +0.5R | 6/8, 1.12 | 2/8, 1.24 | 351 (tie) | **+41.96** |
| Weekend, break-even + fees at 1R | 6/8, 1.11 | 2/8, 1.19 | 351 (tie) | +39.49 |
| Weekend, 1.5R or 2R trigger | 1/8, 0.98 to 1.02 | 2/8 | 351 (tie) | same as 1R |
| 24/7, 1R to +0.5R | 1/8, 0.50 | 2/8, 0.61 | 211 | -15.94 |
| 24/7, 2R to +0.5R | 3/8, 1.20 | 2/8, 1.32 | 351 (tie) | -18.50 |
| 24/7, trail 1.5R / 0.5R | 2/8, 0.55 | 2/8, 1.14 | 171 | +29.70 |

**Verdict: none clears.** Every 24/7 lock loses on the real trades ($2
to $55): the +$49.02 winner (10-27) went +2R, back to +0.1R, then +7.5R,
and every 24/7 lock cuts it to between -$0.35 and +$17. The weekend lock
helped the real trades by $10.49 (+$10.04 of it from #3, the 10-23 long
that went +3.25R over the weekend and stopped Sunday) and beats live on 6 of 8 Brent sets, but it
ties every thesis set and its 1.5R / 2R neighbours do not win (no
plateau). Same as BRENT_STOP_BUFFER_TEST_2026-11-02.md, now with the
journal.

### Stop too tight

Silver only: fixed live on 11-02 by the 0.8% floor (9224cbf,
SILVER_STOP_FLOOR_TEST_2026-11-02.md); the silver runs here already use
it. Brent #4 was followed by a re-entry that won. Nothing more to test.

## 3. Combined stack vs live

| Stack | Brent | Silver | Journal $ | Real since 10-20 | Real since 10-31 |
|---|---|---|---|---|---|
| Live | | | 351 | +31.47 | -30.42 |
| Cap 20% + weekend b/e lock | 6/8, 1.19 | 2/8, 1.26 | 330 | +39.47 | -30.42 |
| Cap 15% + weekend b/e lock | 3/8, 1.16 | 3/8, 1.31 | 254 | +41.74 | -28.15 |
| Cap 10% + weekend b/e lock | 2/8, 0.99 | 3/8, 1.25 | 176 | **+47.07** | -17.62 |
| Cap 15% + weekend lock + side 8h | 3/8, 1.18 | 3/8, 1.31 | 254 | +41.74 | -28.15 |
| Cap 15% + weekend lock + no break | 2/8, 0.71 | 3/8, 1.31 | 168 | +20.87 | 0 (refused) |
| Cap 10% + no break | 2/8, 0.68 | 3/8, 1.22 | 128 | +13.37 | 0 (refused) |

**Verdict: no stack clears.** The best on the real trades (cap 10% plus
the weekend lock, +$15.60 over live) loses on 6 of 8 Brent sets and on
both thesis sets. Cap 20% plus the lock wins 6 of 8 on Brent but 2 of 8
on silver and has no plateau.

## 4. Prior tests, re-run with the live journals

The stop-buffer eval had no journal. Run once with both (`brent_stop_
buffer_test.py`, same candles as that doc):

| Question | With the journal | Verdict |
|---|---|---|
| A. Wider Brent stop (0.90) | journal $243 vs $351 live (-31%) | keep 0.75: the doc's condition for 0.90 fails |
| B. No Brent entries on weekends | journal $420 vs $351, DD 29% vs 40%; 5/8 beats, geo 1.08 (hold) | clears on Brent; silver loses 4/8 |
| C. Weekend-only lock | journal ties | no plateau, as before |

B is the one rule in either run that passes the three rules on Brent.
It has nothing to do with this loss (every loss here came after the
Sunday reopen), and 4 October weekends carry it. Its own decision, not
part of this fix.

## 5. Live changes

**None recommended by the rules.** If you choose to cap what one stop
can cost anyway, live sizing already supports it: `place_bracket` sizes
through `services/risk/size.py size_trade` on the desk's slice, with
`risk_profile.risk_per_trade_pct` as the cap. The contract notes
(`contracts/risk/models.py:30`, 2026-10-23) that it was widened to 100,
no cap; this audit did not read the live config. The one line:

```
risk_profile.risk_per_trade_pct: 15     # PUT /api/risk/profile, keep the other fields
```

15 is the cap with geo-mean at or above live on both desks; 10 is the
one that would have cut today's loss to about $21. `size_trade` counts
the stop distance only, so live 15 is about 17.5 in this sim's terms
(the sim adds both fees and slip). It also covers the open case: the
oil short's stop now risks $95.92 (47% of the desk); at 15% it would be
sized to about $30. No other live constant changes.

## Caveats

- **One month.** 10 real trades; the replay window and the last 30 days
  share 27 days; Brent rallied and the desk was long. The sizing verdict
  in particular is a statement about October, not about the rule.
- **Real-trade lenses are what-ifs on actual fills**, not sims: a refused
  entry counts as 0, though the desk may have entered later; sizing
  scales the actual net; locks replay 5m bars from the fill at the
  trade's own fee rate (xyz fills paid about 0.9 bps a side; the sim
  charges 4.5).
- **Sim tick timing.** 15m ticks miss the minutes between a rewrite and
  an entry, so the delay filter is only seen on the real fills.
- **Equity** is the venue's portfolio samples plus outside money since the
  sample; unrealized P&L on the other desk is in it.
- **After-exit windows** end at the data (09:40Z): the 11-02 long had 1h
  of bars after its stop.

## How to rerun

```
python3 -m services.strategies.evals.loss_audit \
    --oil-theses-dir <pathia>/.state/strategies/oil/theses \
    --metals-theses-dir <pathia>/.state/strategies/metals/theses
python3 -m services.strategies.evals.loss_fix_test \
    --oil-theses-dir <pathia>/.state/strategies/oil/theses \
    --metals-theses-dir <pathia>/.state/strategies/metals/theses
```

About 30 seconds after the first fetch. Account reads cache under
`/tmp/loss-audit/raw` (`--refresh` refetches), candles under
`/tmp/loss-audit/cache`. Writes `/tmp/loss-audit/trades.csv` (every
trade, every column), `causes.csv`, `audit.json`, `fix_report.json`
(every study, dataset, variant, score, verdict, real-trade row and the
11-02 sim), `fix_summary.csv` (one row per study, desk, dataset and
variant) and `real_trades.csv`.

Sim options, all off by default, in `desk_tick_sim.py`: `risk_cap_frac`
(`risk_capped_notional`), `entry_delay_min`, `side_age_h`, `confirm_1h`,
`no_break_entry` (`entry_filter`, `call_start`). With them off the
stop-buffer eval's `summary.csv` and `entries.csv` are byte-identical
to before (cmp). Gate tests: `test_loss_fix.py` (sim, golden, the 11-02
long on its real bars), `test_loss_audit.py` (fills, orders, equity,
tags, the 10-31 and 11-02 longs), `test_loss_fix_eval.py` (grids,
bootstrap, verdicts, real-trade lenses).

No live config, order or thesis was touched. No LLM calls; public reads
only.
