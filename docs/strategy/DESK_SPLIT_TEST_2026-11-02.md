# Desk split test, 2026-11-02

**Verdict: not 50/50. 70/30 (oil/silver) is the only split that clears
the three rules, and only just.** With the live 15% risk cap it beats
90/10 on 5 of 8 datasets, geo-mean final equity x1.05, mean max drawdown
43% vs 55%. 50/50 fails the plateau rule (its 30/70 neighbour beats live
on only 4/8). The 70/30 pass is thin: it survives only 3 of 8
leave-one-out drops, because its plateau rests on 50/50's geo-mean of
1.001. The part that holds is the drawdown cut, not the profit.

If you move, move to 70/30. If you weigh the two live silver trades
more than the replay, keep 90/10: on the real fills since 10-31, every
step toward silver loses more (table 6).

Operator, 2026-11-02: "what will be our success if we split the
portfolio 50/50 oil and silver? seems like our oil run lowkey ended, it
was fire for like a week but it fell off".

## 1. Splits, live 15% risk cap

$225 start, both desks on one 15-minute clock, each sized off its share
of the whole account's marked equity at every entry (as live). 8 scored
datasets.

| Split | Beats 90/10 | Geo-mean | Mean max DD | Worst DD | Worst day | Trades | Win |
|---|---|---|---|---|---|---|---|
| 100/0 | 2/8 | 0.94 | 59.5% | 88.4% | -61.7% | 182 | 43% |
| **90/10 live** | | 1.00 | 54.5% | 81.6% | -57.5% | 295 | 42% |
| **70/30** | **5/8** | **1.05** | **42.8%** | 63.2% | -48.0% | 295 | 42% |
| 50/50 | 5/8 | 1.00 | 36.4% | 50.4% | -36.8% | 295 | 42% |
| 30/70 | 4/8 | 0.88 | 37.9% | 77.6% | -42.8% | 295 | 42% |
| 0/100 | 4/8 | 0.64 | 43.6% | 96.8% | -58.1% | 113 | 41% |

Rules: beats 90/10 on most datasets; a plateau (each neighbour that is
not 90/10 also beats it on most, geo-mean 1 or more; 100/0's only
neighbour is live, so it can never be one); mean max drawdown no worse.

| Split | Most | Plateau | DD | Clears | Leave-one-out |
|---|---|---|---|---|---|
| 100/0 | no | no | no | no | 0/8 |
| 70/30 | yes | yes | yes | **yes** | 3/8 |
| 50/50 | yes | **no** | yes | no | 2/8 |
| 30/70 | no | no | yes | no | 2/8 |
| 0/100 | no | no | yes | no | 2/8 |

Final equity per dataset ($225 start):

| Dataset | 100/0 | 90/10 | 70/30 | 50/50 | 30/70 | 0/100 |
|---|---|---|---|---|---|---|
| Journals, 10-31 to now | 152 | 159 | 174 | 189 | 206 | 232 |
| Replayed theses | 414 | 464 | 556 | 631 | 680 | 692 |
| Daily sign, replay | 2423 | **2484** | 2444 | 2249 | 1936 | 1384 |
| Daily sign, 30d | 1453 | 1528 | **1586** | 1543 | 1410 | 1111 |
| Always long, replay | **3154** | 2193 | 953 | 359 | 116 | 14 |
| Always long, 30d | **1246** | 939 | 479 | 213 | 82 | 13 |
| Always short, replay | 47 | 77 | 176 | 360 | 665 | **1384** |
| Always short, 30d | 59 | 91 | 190 | 356 | 603 | **1111** |
| (Journals from 10-18, not scored) | 894 | 809 | 650 | 506 | 381 | 232 |

Always long and always short are mirror images: Brent rose in October
and silver fell, so one always picks oil and the other silver. They
cancel (2 for, 2 against). The 4 datasets left: 70/30 beats 90/10 on
3/4 (geo 1.08), 50/50 on 3/4 (geo 1.10). Both lose daily sign on the
replay window.

The journals row from 10-18 is not scored: silver had no calls before
10-31, so any silver share there just sits idle.

**Correlation of the desks' daily P&L** (70/30): replayed theses +0.18,
daily sign +0.42 / +0.22, always long -0.21 / -0.27, always short
+0.03 / +0.01, journals since 10-31 -0.74 (3 days, meaningless). Low,
mostly between 0 and 0.4. That is why drawdown falls as the split
evens out: the two desks rarely lose on the same day.

## 2. Without the cap (full port, the rule before today)

| Split | Beats | Geo-mean | Mean max DD | Clears | Leave-one-out |
|---|---|---|---|---|---|
| 90/10 live | | 1.00 | 62.5% | | |
| 70/30 | 6/8 | 1.14 | 50.5% | **yes** | 6/8 |
| 50/50 | 6/8 | 1.11 | 45.2% | no (plateau) | 2/8 |
| 30/70 | 6/8 | 0.94 | 46.0% | no (plateau) | 2/8 |

Same answer, stronger without the cap: 70/30 clears and holds under 6
of 8 drops. The cap shrinks the bets on both desks, so the split matters
less.

## 3. Did oil's run fall off?

Each desk alone at 100%, live rules with the cap, % change of the week's
opening equity (weeks Monday to Sunday UTC). Real = closed fills, $.

| Brent week | Real $ | Journal | Replay | Daily sign | Always long |
|---|---|---|---|---|---|
| 09-28 | | | 0.0 | -64.2 | +85.1 |
| 10-05 | | | -6.4 | +30.7 | +12.1 |
| 10-12 | | 0.0 | -8.6 | +48.2 | +48.2 |
| 10-19 | -1.18 | +130.2 | -6.0 | +117.6 | +117.6 |
| 10-26 | **+70.90** | +139.5 | +128.5 | +139.5 | +139.5 |
| 11-02 (11h) | **-33.87** | -27.9 | | -36.0 | -36.0 |

| Silver week | Real $ | Journal | Replay | Daily sign |
|---|---|---|---|---|
| 09-28 | | | +28.4 | +28.4 |
| 10-05 | | | +29.1 | +76.1 |
| 10-12 | | | -8.5 | +37.1 |
| 10-19 | | | +55.5 | +55.5 |
| 10-26 | | 0.0 | +30.5 | +27.7 |
| 11-02 (11h) | -4.38 | +3.0 | | +3.0 |

Yes, in the sense you mean: the real desk made its money in one week.
Since 10-22 the real fills are -$1.18, **+$70.90**, -$33.87 by week. The
replayed Brent theses lost 6 to 9% a week for three weeks, then made
128% in the week of 10-26. The structural controls had two good weeks
(10-19 and 10-26), then lost 36% in the first 11 hours of this week.

Silver's replay was steadier (4 of 5 weeks up), but read why: 26 of the
30 replayed silver theses were short in a month silver fell 17%, and
all 30 replayed Brent theses were long in a month Brent rose. Both thesis
sets mostly followed the trend; their weekly rows match daily sign.
The sim also trades more than the live desk (every flat 15-minute tick):
the journal row made +130% on Brent the week the real fills made -$1.18.
Read the sim's level loosely; the comparison between splits is the
point.

## 4. Brent's regime: trend, then chop, now a down break

From 1h and 1d candles, per week. Efficiency = net move over the sum of
hourly moves (1 is a straight line, near 0 is chop). Breaks = 1h bars
past the prior 24h high / low. 20d trend at the week's last daily close.

| Week | Close chg | Efficiency | ATR 1h | 24h range | Breaks up/down | 20d trend |
|---|---|---|---|---|---|---|
| 09-28 | +3.9% | 0.07 | 0.70% | 4.2% | 20 / 12 | +0.9% |
| 10-05 | -0.9% | 0.02 | 0.55% | 3.0% | 21 / 22 | +5.3% |
| 10-12 | +3.9% | 0.11 | 0.49% | 3.1% | 12 / 12 | +6.9% |
| 10-19 | +5.0% | 0.11 | 0.54% | 3.3% | 18 / 15 | +10.4% |
| 10-26 | +0.8% | 0.02 | 0.52% | 2.5% | 8 / 11 | +8.1% |
| 11-02 (11h) | -2.7% | 0.66 | 0.45% | 2.3% | 0 / 9 | +8.1% |

Plainly: **the regime changed.** Brent trended up for two weeks (10-12,
10-19; 20-day trend to +10.4%). The week of 10-26 went flat (+0.8%,
efficiency 0.02, the fewest 24h breaks and the tightest range in the
window). The desk's best week came in that chop: buying near the 24h low
and selling near the 24h high pays in a range. This morning is the
opposite: -2.7% in a near straight line (efficiency 0.66), 9 breaks of
the 24h low and none of the high. That is the case a long range-buyer
loses, and it is the -$33.87.

The 20-day trend is still up (+8.1%, 3% over its 20-day mean at the
11-01 close), so this is a stalled uptrend breaking down, not yet a
downtrend. Volatility is shrinking: 1h ATR 0.70% to 0.45%, 24h range
4.2% to 2.3%. The fixed 0.75 stop is now about 30% of a 24h range
(0.75 on 110 vs a 2.3% range), up from about 18% at the start of the
window.

Silver for contrast: 20-day trend -17.4%, a steady fall all month (32
24h-low breaks in each of two weeks), turning up 0.9% this morning with
3 breaks of the 24h high. The live silver desk is short.

## 5. Sample size

| What | Size |
|---|---|
| Silver live trades | 2, both stops, both before the 0.8% stop floor (9224cbf) |
| Silver live journal | 5 theses over 2.4 days (one window, 3 sim trades on oil, 0 on silver) |
| Silver replay | 30 theses, one month, one regime (26 short in a falling market) |
| Brent replay | 30 theses, one month, all long in a rising market |
| Scored datasets | 8, of which 4 are two mirrored pairs |

70/30's pass leans on the replay month (silver short into a downtrend)
and on the 2.4-day journal window, where Brent lost and silver sat on
an open short. Drop any one of 5 datasets and it no longer clears.

## 6. The real fills, rescaled

The real closed trades since 10-31, each net scaled by the split's share
over the share it traded at (linear, no compounding):

| 100/0 | 90/10 | 70/30 | 50/50 | 30/70 | 0/100 |
|---|---|---|---|---|---|
| -28.93 | **-30.42** | -33.39 | -36.37 | -39.34 | -43.80 |

Both silver trades were stopped under the old, tighter stop. n = 4.

## 7. What would change the call

- **For 70/30 or more silver:** 3 to 4 weeks of the live silver desk
  under the 0.8% floor with a positive standalone result, 15 or more
  closed silver trades, and a silver month that is not one-way (the
  replay so far only shows silver shorting a fall).
- **For back to 90/10 or 100/0:** silver's live trades keep losing
  under the floor, or silver turns up and the short theses keep fighting
  it (this morning's 3 up-breaks are the first sign to watch).
- Rerun this test weekly; it takes about 10 seconds.

## 8. How it was run

- `services/strategies/backtest/portfolio.py`: N desks on one clock on
  top of `desk_tick_sim.simulate_steps`. Each entry, resting fill and
  top-up check sizes off `share x account equity` (the other desks'
  marks from the end of the previous tick; the account includes their
  open P&L, as live `_desk_slice` reads account value). A desk can lose
  more than the start while the other carries the account (a cushion
  keeps the sim's own "equity <= 0 is liquidation" net out of it; a
  gate test pins this, found while building the test). The account is
  ruined at 0. No liquidation and no ruin in any run.
- `desk_tick_sim.py` got `simulate_steps` (the same loop, one tick at a
  time) and the `size_equity` hook; with no hook every existing result is
  byte-identical, and 100/0 and 0/100 equal the single-desk sim byte for
  byte (gate test; the eval checks it for both desks on every dataset,
  cap on and off).
- Live rules: market entry off the 24h range, 2R gate, target 0.5%
  inside the far 24h edge, limit target, fixed stop, 20x. Brent stop 0.75
  beyond the edge, size slippage and funding. Silver max(1.3 x ATR14_1h,
  0.8% of the mid), its own funding, venue max 25x as in the earlier
  silver tests. Cap on: `risk_cap_frac` 0.15. Cap off: none (a 100% cap
  never binds at 20x).
- Top-ups: a desk under its full size rests one add at its entry, as the
  desk-tick agent does. With two desks the full size moves with the other
  desk's P&L, so splits top up (47 of 295 trades at 90/10) where a
  single desk never does. The comparisons against 90/10 all carry it.
- Data: public xyz:BRENTOIL and xyz:SILVER candles and funding to
  2026-11-02 11:00Z. Live journals read only.

```
python3 -m services.strategies.evals.desk_split_test \
    --oil-theses-dir ~/Documents/code/pathia/.state/strategies/oil/theses \
    --metals-theses-dir ~/Documents/code/pathia/.state/strategies/metals/theses
```

Writes `/tmp/desk-split/report.json`, `splits.csv` (every dataset x split
x cap), `weekly.csv`, `regime.csv`. Gate tests:
`services/strategies/tests/test_portfolio.py`,
`services/strategies/tests/test_desk_split_eval.py`.
