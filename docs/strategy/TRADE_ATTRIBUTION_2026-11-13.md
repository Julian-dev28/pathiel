# Trade attribution, 2026-11-13

**Verdict.** 23 closed trades across both wallets since 2026-10-15 (pathiel
19, +$20.56; the operator's watch wallet 4, -$58.40). The winners bought AT
the 24h low on a thesis that was already hours old; the losers bought
THROUGH the low (blocked since 11-05) or inside two hours of a fresh
thesis. **No short made money in either wallet**: the only shorts are the
five silver shorts, 0 wins, -$14.41. Of three mechanical candidates built
from the winner traits, one clears the stated bar on the journal replay
(a 120-minute entry delay after a thesis rewrite: +$44.56 over the live
rule, 90 and 180 beside it, WTI +$42.71), but 2.23R of its 2.24R edge is
one afternoon (2026-11-04: two stops skipped). By the review's own FLUKE
rule that is one day, not an edge. **The delay is built and OFF.** A 10%
Brent sleeve cannot be split from the 90% Brent desk on one wallet (the
venue nets positions per coin), so the only honest switch is the whole
oil desk; the one-line command is below. Gut call: leave it off until a
second November-style day confirms it.

Data as of 2026-11-13T01:38Z. Reproduce commands at the end.

## 1. What made wins win and losses lose

Three entry-time traits separate the 9 winners from the 14 losers. All
three are LEANING (n under 10): leads, not rules.

| trait | winners' side | losers' side |
|---|---|---|
| where in the 24h range | bottom third, AT the low: 5 trades, 3 wins, **+$80.63** (LEANING+, p=0.024) | THROUGH the low / over the high: 11 trades, 2 wins, **-$58.90** (LEANING-, p=0.027); mid-range: 3 trades, 1 win, -$70.96 |
| age of the thesis at entry (pathiel oil only) | older than 2h: 4 trades, 3 wins, **+$70.68** (LEANING+, p=0.029) | inside 2h of a rewrite: 10 trades, 4 wins, **-$35.70** (NOISE; holds both November winners too) |
| how the order went in | resting limit at the edge (the rule until 10-29): 5 trades, 3 wins, **+$60.85** (LEANING+, p=0.033) | market IOC at the mid: 18 trades, 6 wins, **-$98.69** (LEANING-, p=0.032) |

Also true, and already acted on or tautological:

- October vs November: October 11 trades, 7 wins, +$11.34 (LEANING+;
  +$69.74 on pathiel's 7 oil longs, -$58.40 on the watch wallet's 4);
  November 12 trades, 2 wins, -$49.18 (LEANING-).
- The 12 stop exits are the whole loss column (-$149.40, LEAK by
  definition); the 5 target exits made +$103.76. Holding 12h+ won 7 of 12
  (+$49.64): winners run, stops are quick. Exit type and hold time are
  outcomes, not entry rules.
- Hour 12-18Z: 8 trades, 5 wins, +$73.33; 00-06Z: 9 trades, 2 wins,
  -$53.25. Monday: 8 trades, 1 win, -$80.65. All NOISE (p 0.30 to 0.62).
- Funding sign, stop width in ATR, planned R, event proximity: no signal
  at this n. Five entries sat inside a blackout window as the calendar
  stands today (the midterms row was added on 11-05, after the fact); 1
  of those 5 won (+$58.36, the 11-04 bounce).

The watch wallet (0x8deb...5019): 4 Brent longs 10-17 to 10-23, all market
entries mid-range (0.51 to 0.69 of the 24h range), no stop or target on
the venue, closed by hand: -52.64, -26.22, +7.89, +12.56. It traded XPL
and ETH before the window; nothing since.

## 2. Did shorts make money?

No. Neither wallet has a Brent short since 2026-10-15. The only shorts are
pathiel's five silver shorts, every one stopped.

| wallet | shorts | wins | net $ | coins | made money? |
|---|---|---|---|---|---|
| all | 5 | 0 | -14.41 | xyz:SILVER | no |
| pathiel | 5 | 0 | -14.41 | xyz:SILVER | no |
| watch | 0 | 0 | +0.00 | - | no |

## 3. The brief's facts, recomputed from the fills

| fact | stated | computed | match |
|---|---|---|---|
| october_oil_longs | {"n": 7, "wins": 5, "net_usd": 69.72} | {"n": 7, "wins": 5, "net_usd": 69.74} | yes |
| november_through_low_longs_11_02_03 | {"n": 3, "net_usd": -74.95} | {"n": 4, "net_usd": -110.68, "entries": ["2026-11-02T03:50Z", "2026-11-02T18:08Z", "2026-11-03T13:36Z", "2026-11-03T13:53Z"]} | NO |
| silver_shorts | {"n": 5, "wins": 0} | {"n": 5, "wins": 0, "net_usd": -14.41} | yes |
| last_trade | {"entry_px": 89.97, "exit_px": 92.52, "net_usd": 30.2} | {"entry": "2026-11-12T03:58Z", "entry_px": 89.966, "exit_px": 92.517, "net_usd": 30.01, "closed_pnl": 30.2} | yes |

The one mismatch: four longs, not three, were bought through the 24h low
on 11-02/11-03 (03:50Z -33.87, 18:08Z -27.34, 13:36Z -25.80, 13:53Z
-23.67; -$110.68 together). The NO_BREAK_ENTRY rule has refused that
entry since 11-05. The last trade's +$30.20 is the venue's closed PnL;
net of fees it is +$30.01.

## 4. Slices that lean (the review's labels)

| slice | n | wins | win % [95%] | mean R [95%] | net $ | p | label |
|---|---|---|---|---|---|---|---|
| all | 23 | 9 | 39% [22%, 59%] | +0.41 [-0.56, +1.55] | -37.84 | 0.505 | NOISE |
| desk=oil | 18 | 9 | 50% [29%, 71%] | +0.95 [-0.30, +2.42] | -23.42 | 0.124 | LEANING+ |
| desk=metals | 5 | 0 | 0% [0%, 43%] | -1.09 [-1.13, -1.05] | -14.41 | 0.132 | LEANING- |
| side=long | 18 | 9 | 50% [29%, 71%] | +0.95 [-0.32, +2.43] | -23.42 | 0.122 | LEANING+ |
| side=short | 5 | 0 | 0% [0%, 43%] | -1.09 [-1.13, -1.05] | -14.41 | 0.124 | LEANING- |
| month=2026-11 | 12 | 2 | 17% [5%, 45%] | -0.45 [-1.13, +0.54] | -49.18 | 0.046 | LEANING- |
| month=2026-10 | 11 | 7 | 64% [35%, 85%] | +1.88 [-0.09, +4.05] | +11.34 | 0.046 | LEANING+ |
| entry=market | 18 | 6 | 33% [16%, 56%] | -0.29 [-0.99, +0.57] | -98.69 | 0.032 | LEANING- |
| entry=resting | 5 | 3 | 60% [23%, 88%] | +2.37 [-0.42, +5.16] | +60.85 | 0.033 | LEANING+ |
| range=through own edge | 11 | 2 | 18% [5%, 48%] | -0.60 [-1.15, +0.20] | -58.90 | 0.027 | LEANING- |
| range=bottom third (at edge) | 5 | 3 | 60% [23%, 88%] | +2.46 [-0.34, +5.25] | +80.63 | 0.024 | LEANING+ |
| range=top third | 4 | 3 | 75% [30%, 95%] | +0.72 [-1.04, +2.29] | +11.39 | 0.757 | NOISE |
| range=middle third | 3 | 1 | 33% [6%, 79%] | - [-, -] | -70.96 | - | NOISE |
| ret_5d=5d down >2% | 10 | 2 | 20% [6%, 51%] | -0.33 [-1.16, +0.89] | -11.70 | 0.180 | LEANING- |
| ret_5d=5d up >2% | 8 | 4 | 50% [22%, 78%] | +2.37 [-0.42, +5.16] | -10.11 | 0.036 | LEANING+ |
| ret_5d=5d flat | 5 | 3 | 60% [23%, 88%] | -0.19 [-1.04, +0.65] | -16.03 | 0.622 | NOISE |
| stop_atr=<1.5 ATR | 15 | 5 | 33% [15%, 58%] | +0.48 [-0.64, +1.94] | +55.25 | 0.836 | NOISE |
| stop_atr=1.5-2.5 ATR | 3 | 1 | 33% [6%, 79%] | +0.06 [-1.07, +2.29] | -42.52 | 0.739 | NOISE |
| hour=00-06Z | 9 | 2 | 22% [6%, 55%] | +0.05 [-1.11, +1.40] | -53.25 | 0.621 | NOISE |
| hour=12-18Z | 8 | 5 | 62% [31%, 86%] | +1.20 [-0.62, +3.65] | +73.33 | 0.302 | NOISE |
| weekday=Mon | 8 | 1 | 12% [2%, 47%] | -0.35 [-1.14, +1.19] | -80.65 | 0.336 | NOISE |
| funding=receives | 14 | 5 | 36% [16%, 61%] | +0.53 [-0.60, +2.02] | +37.95 | 0.763 | NOISE |
| funding=pays | 9 | 4 | 44% [19%, 73%] | +0.08 [-1.23, +1.77] | -75.79 | 0.770 | NOISE |
| rewrite=rewrite <2h before | 10 | 4 | 40% [17%, 69%] | +0.21 [-0.81, +1.30] | -35.70 | 0.747 | NOISE |
| rewrite=thesis older than 2h | 4 | 3 | 75% [30%, 95%] | +2.78 [-0.32, +5.88] | +70.68 | 0.029 | LEANING+ |
| event=event 12-48h away | 13 | 6 | 46% [23%, 71%] | +0.88 [-0.68, +2.84] | -42.99 | 0.475 | NOISE |
| event=inside blackout | 5 | 1 | 20% [4%, 62%] | -0.30 [-1.23, +1.42] | +1.76 | 0.514 | NOISE |
| event=no event in 48h | 3 | 1 | 33% [6%, 79%] | +0.69 [-1.16, +4.27] | +8.02 | 0.817 | NOISE |
| event=event <12h away | 2 | 1 | 50% [9%, 91%] | -0.34 [-1.07, +0.39] | -4.63 | 0.685 | NOISE |
| hold=held 12h+ | 12 | 7 | 58% [32%, 81%] | +1.84 [-0.06, +3.87] | +49.64 | 0.023 | LEANING+ |
| hold=held 2-12h | 6 | 1 | 17% [3%, 56%] | -0.52 [-1.10, +0.62] | -51.58 | 0.286 | NOISE |
| hold=held <2h | 5 | 1 | 20% [4%, 62%] | -0.75 [-1.25, +0.10] | -35.90 | 0.241 | NOISE |
| exit=stop | 12 | 0 | 0% [0%, 24%] | -1.12 [-1.17, -1.08] | -149.40 | 0.000 | LEAK |
| exit=manual | 5 | 3 | 60% [23%, 88%] | +3.10 [+3.10, +3.10] | -0.04 | 0.171 | NOISE |
| exit=target | 5 | 5 | 100% [57%, 100%] | +3.54 [+1.83, +5.81] | +103.76 | 0.001 | LEANING+ |

Dropping the biggest trade flips none of the LEANING rows (`one_trade`
False), but every n is under 10: nothing here is EDGE or LEAK.

## 5. Every trade, with what was true at entry

Range pos: 0 = the side's own 24h edge, under 0 = through it. 5d/24h: the
close-to-close return into the entry. Stop ATR: the opening stop's
distance in 1h ATR14. Event: hours to the nearest impact-2+ event or EIA
report (negative = it was before the entry), "blackout" = inside a window.

| wallet | coin | side | entry (UTC) | type | range pos | 5d % | 24h % | stop ATR | plan R | hour | day | funding | rewrite<2h | event | hold h | exit | R | net $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| watch | BRENTOIL | long | 10-17T02:09 | market | 0.62 | +6.4 | +4.2 | - | - | 02Z | Sat | pays | - | -34h | 52.0 | manual | - | -52.64 |
| watch | BRENTOIL | long | 10-19T21:46 | market | 0.55 | +2.4 | -1.1 | - | - | 21Z | Mon | pays | - | +41h | 16.7 | manual | - | -26.22 |
| watch | BRENTOIL | long | 10-20T20:42 | market | 0.51 | +2.3 | -0.5 | - | - | 20Z | Tue | pays | - | +18h | 42.6 | manual | - | +7.89 |
| watch | BRENTOIL | long | 10-22T15:53 | market | 0.69 | +0.2 | +1.6 | - | - | 15Z | Thu | pays | - | -25h | 20.2 | manual | - | +12.56 |
| pathiel | BRENTOIL | long | 10-22T16:13 | market | 0.79 | +0.1 | +2.2 | 1.5 | 0.9 | 16Z | Thu | pays | yes | -26h | 1.5 | target | +0.91 | +1.05 |
| pathiel | BRENTOIL | long | 10-23T07:34 | resting | 0.73 | +2.9 | +2.3 | 2.0 | 2.3 | 07Z | Fri | receives | yes | -41h | 8.2 | target | +2.29 | +3.81 |
| pathiel | BRENTOIL | long | 10-23T16:34 | resting | 0.85 | +4.9 | +4.6 | 0.8 | 3.6 | 16Z | Fri | receives | no | -50h | 45.0 | stop | -1.04 | -6.04 |
| pathiel | BRENTOIL | long | 10-26T01:10 | resting | 0.02 | +4.3 | -1.0 | 1.1 | 2.9 | 01Z | Mon | receives | yes | +61h | 1.8 | stop | -1.16 | -5.81 |
| pathiel | BRENTOIL | long | 10-26T03:13 | resting | 0.00 | +2.6 | -1.8 | 0.9 | 4.4 | 03Z | Mon | receives | no | +59h | 12.6 | target | +4.27 | +19.86 |
| pathiel | BRENTOIL | long | 10-27T14:33 | resting | 0.00 | +2.8 | -0.1 | 0.7 | 8.6 | 14Z | Tue | receives | no | +24h | 59.1 | target | +7.50 | +49.02 |
| pathiel | BRENTOIL | long | 10-31T16:18 | market | -0.23 | +0.2 | -1.3 | 2.6 | 4.7 | 16Z | Sat | receives | no | +8h | 26.0 | weekend | +0.39 | +7.83 |
| pathiel | SILVER | short | 11-02T00:51 | market | -0.12 | -8.6 | +0.8 | 1.0 | 3.4 | 00Z | Mon | receives | - | +47h | 0.2 | stop | -1.09 | -1.82 |
| pathiel | SILVER | short | 11-02T01:18 | market | -0.15 | -8.2 | +1.0 | 1.3 | 3.0 | 01Z | Mon | receives | - | +47h | 4.2 | stop | -1.09 | -2.56 |
| pathiel | BRENTOIL | long | 11-02T03:50 | market | -0.06 | -0.6 | -1.7 | 1.9 | 2.4 | 03Z | Mon | receives | yes | -28h | 4.8 | stop | -1.05 | -33.87 |
| pathiel | SILVER | short | 11-02T05:34 | market | -0.25 | -7.1 | +1.3 | 1.0 | 4.2 | 05Z | Mon | receives | - | +42h | 8.1 | stop | -1.15 | -2.89 |
| pathiel | BRENTOIL | long | 11-02T18:08 | market | -0.01 | -6.6 | -6.9 | 0.6 | 13.2 | 18Z | Mon | pays | yes | +30h | 19.4 | stop | -1.16 | -27.34 |
| pathiel | BRENTOIL | long | 11-03T13:36 | market | -0.14 | -4.8 | -3.4 | 0.6 | 10.3 | 13Z | Tue | pays | yes | blackout | 0.1 | stop | -1.35 | -25.80 |
| pathiel | BRENTOIL | long | 11-03T13:53 | market | -0.27 | -4.8 | -3.4 | 0.8 | 9.2 | 13Z | Tue | pays | yes | blackout | 23.7 | stop | -1.12 | -23.67 |
| pathiel | SILVER | short | 11-04T05:59 | market | -0.29 | -3.0 | +0.6 | 1.2 | 4.7 | 05Z | Wed | receives | - | blackout | 0.3 | stop | -1.08 | -3.53 |
| pathiel | SILVER | short | 11-04T06:22 | market | -0.20 | -0.0 | +3.8 | 1.4 | 4.6 | 06Z | Wed | receives | - | blackout | 11.2 | stop | -1.03 | -3.61 |
| pathiel | BRENTOIL | long | 11-04T17:32 | market | -0.03 | -17.2 | -8.8 | 0.5 | 14.6 | 17Z | Wed | pays | yes | blackout | 17.2 | manual | +3.10 | +58.36 |
| pathiel | BRENTOIL | long | 11-10T06:27 | market | 0.10 | -4.0 | -6.1 | 1.5 | 4.2 | 06Z | Tue | receives | yes | -6h | 2.4 | stop | -1.07 | -12.46 |
| pathiel | BRENTOIL | long | 11-12T03:58 | market | 0.05 | -7.3 | -0.8 | 1.4 | 2.8 | 03Z | Thu | receives | yes | +13h | 13.4 | target | +2.75 | +30.01 |

## 6. Part 2: three candidates against the bar

Each is one `desk_tick_sim.Params` override on the live Brent rule
(`trend_gate_test.base_params`: market entry off the 24h range, 0.75
stop, 0.5% offset target, 2R, 20x, no entry after a break, weekend flat,
6% risk, $182.09 start), scored on the live journal (71 theses, 10-18 to
2026-11-13 01:30Z) exactly as the trend veto was. The bar, all five:
net beats the baseline, max DD no worse, both neighbours beat it, last
third not negative and not below the baseline, WTI (the journal's sides on
xyz:CL) not negative.

| candidate | rule | real-fill trait |
|---|---|---|
| zone 0.33 (0.25 / 0.50) | market entry only in the bottom third of the 24h range (`entry_zone_frac`, new) | range=bottom third, 3/5 +$80.63 |
| delay 120 min (90 / 180) | no entry in a thesis's first two hours (`entry_delay_min`) | thesis older than 2h, 3/4 +$70.68 |
| edge 0.25 ATR (0.15 / 0.50) | rest a limit AT the 24h edge instead of taking the mid (`entry_mode="edge"`) | entry=resting, 3/5 +$60.85 |

### Journal replay

| variant | n | wins | net $ | max DD | thirds | WTI sides net $ (n) | replay30 Brent | replay30 WTI |
|---|---|---|---|---|---|---|---|---|
| baseline | 16 | 8 | +142.82 | 28.9% | +40.66 / +48.95 / +27.24 | -21.78 (20) | +85.51 | +131.98 |
| zone | 16 | 8 | +142.82 | 28.9% | +40.66 / +48.95 / +27.24 | -21.78 (20) | +85.51 | +131.98 |
| zone_25 | 16 | 8 | +142.82 | 28.9% | +40.66 / +48.95 / +27.24 | -21.78 (20) | +85.51 | +131.98 |
| zone_50 | 16 | 8 | +142.82 | 28.9% | +40.66 / +48.95 / +27.24 | -21.78 (20) | +85.51 | +131.98 |
| delay | 14 | 8 | +187.38 | 27.5% | +40.66 / +48.95 / +55.95 | +42.71 (15) | +88.91 | +91.96 |
| delay_90 | 14 | 8 | +187.41 | 27.5% | +40.66 / +48.95 / +55.97 | +28.97 (16) | +72.16 | +91.96 |
| delay_180 | 12 | 8 | +237.40 | 17.7% | +40.66 / +48.95 / +88.17 | +123.72 (13) | +88.91 | +63.02 |
| edge | 18 | 5 | +48.88 | 41.9% | +23.22 / +19.64 / +2.81 | +19.95 (18) | +79.09 | +107.29 |
| edge_rest15 | 15 | 5 | +86.42 | 38.4% | +35.82 / +26.06 / +14.19 | +68.17 (14) | +93.82 | +165.70 |
| edge_rest50 | 21 | 5 | -39.83 | 66.2% | +23.22 / +7.98 / -61.21 | -40.92 (24) | +40.33 | +86.19 |

### The bar

| candidate | net | DD | plateau | last third | WTI | bar | one day carries it? |
|---|---|---|---|---|---|---|---|
| zone | no (+0.00) | yes | no (zone_25 no, zone_50 no) | yes (+27.24 vs +27.24) | no (-21.78) | fail | n/a (no trade changed) |
| delay | yes (+44.56) | yes | yes (delay_90 yes, delay_180 yes) | yes (+55.95 vs +27.24) | yes (+42.71) | PASS | yes: 2026-11-04 is +2.23R of +2.24R |
| edge | no (-93.94) | no | no (edge_rest15 no, edge_rest50 no) | no (+2.81 vs +27.24) | yes (+19.95) | fail | yes: 2026-11-12 is -6.00R of -11.72R |

- **zone** is a no-op: on every dataset it changes no trade. With the
  0.75 stop and the 2R gate, a market long above the bottom third of the
  range already fails R or the 20x line (the "leverage" refusals turn into
  "zone" refusals, trade for trade). The bottom-third trait is the live
  rule's own filter, not something to add.
- **edge** loses on the journal (-$93.94) and its neighbours disagree; the
  resting limit misses the 11-10 bottom (-6R of its -11.72R). Same answer
  as LOSS_AUDIT_2026-11-02 (2/8).
- **delay** clears all five legs. What it changed on the journal:

| run | entry | px | exit | R | net $ |
|---|---|---|---|---|---|
| baseline only | 2026-11-04 14:00Z | 100.63 | 2026-11-04 15:45Z stop | -1.13 | -17.46 |
| baseline only | 2026-11-04 16:00Z | 99.243 | 2026-11-04 16:30Z stop | -1.10 | -16.27 |
| baseline only | 2026-11-04 17:30Z | 94.178 | 2026-11-06 21:00Z weekend | +3.42 | +46.70 |
| baseline only | 2026-11-09 07:45Z | 95.602 | 2026-11-09 11:15Z stop | -1.14 | -18.29 |
| baseline only | 2026-11-09 13:00Z | 93.127 | 2026-11-09 14:15Z stop | -1.12 | -17.10 |
| baseline only | 2026-11-10 06:45Z | 89.78 | 2026-11-10 08:45Z stop | -1.12 | -16.05 |
| delay only | 2026-11-05 01:45Z | 93.678 | 2026-11-06 21:00Z weekend | +3.39 | +53.28 |
| delay only | 2026-11-09 11:00Z | 95.575 | 2026-11-09 11:15Z stop | -1.12 | -20.68 |
| delay only | 2026-11-09 14:00Z | 92.946 | 2026-11-09 19:15Z stop | -1.09 | -19.32 |
| delay only | 2026-11-10 08:30Z | 89.605 | 2026-11-10 08:45Z stop | -1.13 | -18.27 |

Baseline-vs-delay R by exit day:

| exit day | R delta | $ delta |
|---|---|---|
| 2026-11-04 | +2.23 | +33.73 |
| 2026-11-06 | -0.03 | +6.58 |
| 2026-11-09 | +0.05 | -4.61 |
| 2026-11-10 | -0.01 | -2.22 |
| 2026-11-12 | +0.00 | +11.08 |

The 11-04 afternoon is the whole edge: the two stops at 100.63 and 99.24
(the election crash, thesis minutes old) are skipped, the bounce is still
bought at 01:45Z the next day. On 11-09 and 11-10 the delay moves the same
losing entries two hours later for the same loss; the dollars after 11-04
are compounding on the saved stops, not new signal. The quarter-R test
(`winner_traits_test.one_day_flips`): the other days add +0.01R.
Robustness across candle ends: the same delta on the 11-10 snapshot
(+$35.71), the 11-12 snapshot (+$42.76) and today (+$44.56), because it is
the same two trades each time.

On the real fills the picture is weaker still: the ten pathiel entries
inside 2h of a rewrite include both November winners (11-04 17:32Z +$58.36,
11-12 03:58Z +$30.01); a delay would have postponed those too, and whether
the postponed entry still fills is what the sim answers, not the ledger.

### No-thesis cross-checks (context, not the bar)

Tournament engine, 198.4 days of 1h Brent, the zone gate on the
mechanical dip-buy (delay needs a call start and edge a resting fill,
which that engine does not model):

| strategy | trades | wins | net $ | max DD | thirds | WTI net $ | bar |
|---|---|---|---|---|---|---|---|
| G_flat | 0 | 0 | +0.00 | 0.0% | [0.0, 0.0, 0.0] | +0.00 | fail |
| A_long | 118 | 32 | -149.43 | 87.6% | [-150.92, -0.34, 1.83] | -154.97 | fail |
| Z_zone25 | 118 | 32 | -149.43 | 87.6% | [-150.92, -0.34, 1.83] | -154.97 | fail |
| Z_zone33 | 118 | 32 | -149.43 | 87.6% | [-150.92, -0.34, 1.83] | -154.97 | fail |
| Z_zone50 | 118 | 32 | -149.43 | 87.6% | [-150.92, -0.34, 1.83] | -154.97 | fail |

The sim's controls (net $, baseline / delay / zone / edge / edge_rest15):

| dataset | baseline | delay | zone | edge | edge 0.15 |
|---|---|---|---|---|---|
| brent_always_long_30d | +64.96 | +64.96 | +64.96 | +41.55 | +90.11 |
| brent_always_short_30d | -52.97 | -65.62 | -52.97 | -43.27 | -54.94 |
| brent_daily_sign_30d | -37.82 | -37.82 | -37.82 | -29.08 | -6.65 |
| brent_always_long_50d | +103.67 | +103.67 | +103.67 | +89.99 | +159.27 |
| brent_always_short_50d | -106.80 | -106.80 | -106.80 | -110.45 | -108.15 |
| brent_daily_sign_50d | -66.52 | -66.52 | -66.52 | -70.73 | -28.84 |
| wti_always_long_30d | -49.62 | -49.62 | -49.62 | -1.27 | +41.89 |
| wti_always_short_30d | -75.81 | -75.78 | -75.81 | -62.67 | -51.13 |
| wti_daily_sign_30d | -85.46 | -85.46 | -85.46 | -61.01 | -48.70 |
| wti_always_long_50d | -16.00 | -16.00 | -16.00 | +1.59 | +90.15 |
| wti_always_short_50d | -61.48 | -61.48 | -61.48 | -91.94 | -93.77 |
| wti_daily_sign_50d | -74.82 | -74.82 | -74.82 | -56.02 | -65.21 |

With no thesis the delay fires 6 to 84 times and changes almost nothing
(a control's "call" starts once); zone changes nothing anywhere; the
resting edge at 0.15 ATR beats the baseline on always_long and daily_sign
but loses on the journal, where it matters.

### The 10% sleeve in dollars

Each candidate replayed at 10% of today's equity ($213.80 from
account_overview, so $21.38), same 6% risk, journal window 25.9 days:

| variant | trades | wins | net $ | per 30 days | max DD |
|---|---|---|---|---|---|
| baseline | 16 | 8 | +16.77 | +19.42 | 28.9% |
| zone | 16 | 8 | +16.77 | +19.42 | 28.9% |
| delay | 14 | 8 | +22.00 | +25.48 | 27.5% |
| edge | 18 | 5 | +5.74 | +6.65 | 41.9% |

At $21.38 the delay makes about $25 a month on this window against the
baseline's $19; the $6 difference is the two 11-04 stops scaled down.

## 7. Part 3: the sleeve. Built, OFF.

**Why a 10% Brent sleeve cannot exist beside the 90% Brent desk.**
Hyperliquid nets positions per coin per account: a second Brent entry
tops up the oil desk's position or opens the one position the account can
hold. `services/strategies/desk_share.desk_for_coin` gives each coin one
desk (primary first), `place_bracket` holds one position per desk, and
`scripts/desk_slot.py` reads the slot per desk. `desk_equity_share`
splits equity between desks on DIFFERENT coins (oil 0.95 / metals 0.05
was that), never between two rules on one coin. The metals slot is
"inactive" today because `desk_equity_share` has no metals key; it is a
gold/silver slot, not an oil one. A true 10% sleeve would need a second
coin (xyz:CL as a new desk's primary, `desk_equity_share` oil 0.9 / wti
0.1); the WTI leg above is a 15-trade cross-check with the journal's
Brent sides, not a WTI test. Not built, not recommended.

**What is built (OFF).** The delay as a live rule, switched by config:

- `services/strategies/next_trade.py`: `entry_delay(thesis_age_min,
  delay_min)` (pure), `entry_delay_for(coin, config)` (reads
  `oil_entry_delay_min`, oil coins only, None/0 = off), `thesis_age_minutes`,
  and `next_trade(..., thesis_age_min, entry_delay_min)`: a young thesis
  sets `ok` False, puts "thesis N min old: no entry in the first M min of a
  call (K min to go)" first in `blockers`, and keeps the levels the tick
  will use once the delay passes. `ENTRY_DELAY_MIN = None`.
- Callers pass the thesis age and the config delay: `services/strategies/
  api.desk_next_trade` (the brief's "Live rule" line and GET /api/desks/
  oil/next), `scripts/trigger_watch.py evaluate` (the price watcher never
  fires inside the delay), `scripts/oil_levels.py --next --thesis-age-min`.
- `pathiel/agents/config_store.DEFAULT_CONFIG["oil_entry_delay_min"] = None`.
  The `config` MCP tool already accepts any key present in the merged
  config through its generic path, so the operator's tool surface is
  unchanged (`tests/test_mcp_operator_unchanged.py` pins it).
- `desk_tick_sim.Params.entry_zone_frac` (the zone gate, off) and
  `Params.entry_delay_min` (already there) are the same two rules in the
  simulator; `test_entry_delay.py` pins the sim/live boundary.
- Nothing in `.agent-config.json`, `place_bracket`, the desk-tick agent
  text or the live constants changed. Every existing `next_trade` answer
  is byte-identical (`test_entry_delay.py`).

**The one command to turn it on** (the operator's call; it applies to the
whole oil desk, not 10%):

    config {"oil_entry_delay_min": 120}

or the one-line equivalent in `.agent-config.json`: `"oil_entry_delay_min": 120`.
Then restart the pathiel MCP server (reconnect `pathiel` in `/mcp`) and
the price watcher (`pkill -f scripts/trigger_watch.py`, then the
desk-loops start line), so both read the new code. `config
{"oil_entry_delay_min": 0}` turns it off again.

**Recommendation: NONE for the sleeve; the delay stays off.** It passes a
bar written for a plateau and fails the fluke test the review applies to
every slice. Re-run `winner_traits_test` after the next November-style
week; if the 11-04 shape repeats on a second day, that is the yes.

## Reproduce

    # the ledger and the slices (public reads, read only; saves a replayable bundle)
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.trade_attribution \
        --oil-theses-dir <pathia>/.state/strategies/oil/theses --work-dir <dir> --out-dir <dir> --save-bundle <dir>/bundle
    .venv/bin/python -m services.strategies.evals.trade_attribution --offline services/strategies/tests/fixtures/attribution_2026-11-13

    # the candidates against the bar (exit 0 = a candidate passes)
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.winner_traits_test \
        --oil-theses-dir <pathia>/.state/strategies/oil/theses --oil-replay-dir /tmp/thesis-replay-30b/oil/theses \
        --cache-dir <fresh dir> --tournament-cache-dir <the tournament's 1h cache> --out-dir <dir> --equity 213.80

    # gate tests
    .venv/bin/python -m pytest -q services/strategies/tests/test_trade_attribution.py \
        services/strategies/tests/test_winner_traits.py services/strategies/tests/test_entry_delay.py

Outputs this doc was built from: `<out>/attribution.md`, `trades.csv`,
`report.json` (attribution); `<out>/report.json` with `journal_trades`
(candidates). The saved bundle is the test fixture
`services/strategies/tests/fixtures/attribution_2026-11-13/`.
