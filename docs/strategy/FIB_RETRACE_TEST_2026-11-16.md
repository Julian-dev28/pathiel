# Fibonacci retracement test, 2026-11-16

**Verdict: no Fibonacci retracement rule passes, nothing is adopted. 3,690 variants in nine families, at 6% and 1.2% risk: 0 PASS, 0 LEANING. Two families FAIL, seven are NOISE. Beside the live rule the median variant loses on every dataset. Alone, the only swing with anything in it is the 1h zigzag, and it made its money in the last 100 days only: in the first half of the 208-day history 80% of its long variants lose. No fib level wins on both Brent and WTI. The operator's ratchet at 75% of the way to target is a small plus; at 50% it cuts winners.** Research only. No live config, order or rule changed.

Operator, 2026-11-16: "also test fib retrace cuz thats what im doing". The operator holds a hand long of 26.84 at 92.96, stop 92.695, target 93.709, and ratchets the stop up as price nears the target.

Data: Brent and WTI 15m candles 2026-09-27 to 2026-11-16 06:15Z (the venue serves its last 5,000 bars per interval), 1h candles 2026-04-21 to 2026-11-16, 77 journal theses from 2026-10-18, the 30-thesis replay, $182.09 start. Every number is from `services/strategies/evals/fib_retrace_test.py`; reproduce commands at the end.

**Fees: the account's measured xyz fee, 0.90 bps taker and 0.31 bps maker** (review ledger, 10 taker round trips, 3 maker targets), for the fib trades and for the live book they are compared with. The sim's constants (4.5 / 1.5 bps) are five times too high for xyz, as `INTRADAY_5M_TEST_2026-11-16.md` found. That is why the live numbers below are higher than in the earlier docs.

The live long book on the same windows (the rule as it trades, `short_ladder_test.prepare`):

| dataset | bars | window | trades | wins | net $ | max DD | net at the sim's fee |
|---|---|---|---|---|---|---|---|
| brent_journal (primary) | 15m | 10-18 to 11-16, 29.1 days | 17 | 9 | +174.12 | 27.9% | +143.47 |
| brent_replay30 | 15m | 09-30 to 10-30, 30.0 days | 32 | 17 | +194.18 | 23.1% | +141.24 |
| wti_journal | 15m | 10-18 to 11-16, 29.1 days | 21 | 6 | -35.31 | 49.2% | -42.07 |
| brent_15m_50d, wti_15m_50d | 15m | 09-30 to 11-16, 47.2 days | alone only | | | | |
| brent_1h_208d, wti_1h_208d | 1h | 04-26 to 11-16, 203.3 days | alone only | | | | |

Halves of the primary (split 2026-11-01 17:15Z): 9 trades, +$191.76; 8 trades, -$8.59. Halves of the 208-day sets split at 2026-08-06 14:00Z.

## What was tested

- **Swing (the anchor), three definitions:** (a) the 24h range low and high, the later one is the swing's end; (b) a zigzag on 1h candles, a pivot confirmed after a reversal of k x ATR14, k 2 / 3 / 4; (c) the low and high since the last weekend reopen.
- **Direction:** with the swing (buy a retrace of an up-swing, sell a retrace of a down-swing); the same on the journal thesis side only; long only.
- **Entry:** 38.2 / 50 / 61.8 / 78.6% retracement. A resting limit (filled when a bar trades through the level by one tick, not on a touch), or a confirmation (first 15m close back beyond the level, market at the next open).
- **Stop:** beyond the next deeper fib level; beyond 100% (the swing origin); the level less 1 ATR.
- **Target:** the swing extreme (0%), the -27.2% extension, the -61.8% extension.
- **Ratchet:** off; stop to break-even at 50% of the way to target (be50); at 75% (be75); stop to the 38.2% level at 75% (fib75).

The full cross less its duplicates is **3,690 variants**, each at two risk sizes: 7,380 judged rows. Family = swing definition x direction: 9 families.

## The nine families (6% risk)

| family | variants | verdict | Brent 50d alone: trades / win % / avg R | variants above zero alone: Brent 50d / Brent 208d / WTI 50d / WTI 208d | 208d first half above zero, Brent / WTI | beside live, above zero: journal / replay / WTI | median $ beside live: journal / replay / WTI | above zero on all three beside live and both halves |
|---|---|---|---|---|---|---|---|---|
| a. 24h range, long only | 246 | **NOISE** | 2,502 / 25% / -0.07 | 81 / 106 / 139 / 157 of 246 | 74 / 101 | 38 / 42 / 28 of 246 | -21.92 / -31.62 / -20.51 | 0 |
| a. 24h range, thesis side | 246 | **NOISE** | 2,218 / 23% / -0.17 | 40 / 54 / 133 / 122 of 246 | no theses | 47 / 42 / 40 of 246 | -21.35 / -31.62 / -16.02 | 0 |
| a. 24h range, with the swing | 246 | **FAIL** | 5,975 / 21% / -0.25 | 28 / 31 / 29 / 29 of 246 | 74 / 71 | 139 / 20 / 66 of 246 | +13.04 / -49.16 / -20.51 | 3 |
| b. 1h zigzag, long only | 738 | **NOISE** | 3,848 / 45% / +0.72 | 470 of 693 / 382 of 729 / 401 of 711 / 378 of 720 | 150 / 178 of 738 | 84 of 525 / 232 of 693 / 12 of 468 | -21.01 / -10.58 / -13.58 | 0 |
| b. 1h zigzag, thesis side | 738 | **NOISE** | 3,590 / 40% / +0.38 | 369 of 693 / 389 of 693 / 365 of 711 / 428 of 702 | no theses | 84 of 525 / 232 of 693 / 12 of 468 | -21.01 / -10.58 / -13.58 | 0 |
| b. 1h zigzag, with the swing | 738 | **FAIL** | 7,512 / 32% / +0.19 | 334 of 711 / 261 of 729 / 202 of 711 / 276 of 729 | 212 / 238 of 738 | 212 of 561 / 239 of 693 / 96 of 504 | -6.03 / -12.25 / -12.53 | 8 |
| c. session, long only | 246 | **NOISE** | 1,034 / 34% / -0.01 | 127 / 129 / 129 / 130 of 246 | 33 / 49 | 19 / 38 / 18 of 246 | -21.38 / -21.57 / -15.57 | 0 |
| c. session, thesis side | 246 | **NOISE** | 905 / 32% / -0.09 | 95 / 96 / 128 / 136 of 246 | no theses | 25 / 38 / 27 of 246 | -18.44 / -25.48 / -13.88 | 0 |
| c. session, with the swing | 246 | **NOISE** | 3,047 / 24% / -0.25 | 26 / 24 / 61 / 36 of 246 | 27 / 27 | 35 / 31 / 11 of 246 | -40.73 / -34.40 / -28.32 | 0 |

How to read it:

- **alone**: the variant on its own $182.09, no live book. **beside live**: the only way it runs on the account (one position per desk): no fib order while the live long is open, an open fib trade closed when the live long enters.
- **Verdict on a variant**: the repo's bar (`short_ladder_test.judge`: beat live on every dataset, drawdown no worse, both halves, every grid neighbour, without the best day) with 10 or more trades beside live on the primary, plus one leg of this test: above zero alone on all four long-history sets. Under 10 trades: LEANING or NOISE, never PASS.
- **Verdict on a family**, written in the script before the first run: PASS needs a passing variant and more than half of the family's 10-trade variants above zero beside live on every dataset. LEANING: a variant passes or leans. FAIL: variants with 10 trades exist, none passes or leans. NOISE: no variant has 10 trades beside live.
- 7,380 rows: **0 PASS, 0 LEANING, 39 FAIL, 3,651 NOISE at each risk size.** Only "with the swing" on the 24h range (30 variants) and on the zigzag (9) reach 10 trades beside live; all 39 fail.
- **The whole grid beside live, 6% risk:** above zero 683 of 3,087 traded variants on the Brent journal (median -$17.96), 914 of 3,555 on the replay (-$19.25), 310 of 2,916 on WTI (-$14.27).
- **The whole grid alone:** 41% to 47% of the traded variants are above zero on each of the four long-history sets; the median variant loses on every one (-$7.27, -$10.62, -$7.29, -$3.79).
- **Thesis side** equals long only in the journal window (73 of 77 theses are long), so those two rows are near copies. The 208-day sets have theses for the last 47 days only.

At 1.2% risk the trades are the same and a fifth the size: the same verdicts and the same counts; the median beside live is negative in 26 of the 27 family and dataset cells, as at 6%.

With place_bracket's 2R gate on (`--min-r 2`): 0 PASS, 0 LEANING, 9 FAIL, 3,681 NOISE. The same picture with fewer trades.

## The textbook setup, one row per family

Named before any run, so it is not picked from the grid: 61.8% level, resting limit, stop beyond 78.6%, target the swing extreme, no ratchet, zigzag k 3. At 6% risk:

| family | Brent journal beside live: n / win % / net $ / max DD vs live | replay beside live | WTI beside live | Brent 208d alone: n / win % / net $ / avg R / max DD | WTI 208d alone | 208d halves alone $, Brent / WTI |
|---|---|---|---|---|---|---|
| a, long only | 4 / 0% / -45.72 / 31.5% vs 27.9% | 7 / 0% / -69.58 | 3 / 0% / -36.24 | 55 / 26% / -56.38 / -0.05 / 55.1% | 59 / 27% / -11.33 | -96.05, +83.96 / -45.06, +44.82 |
| a, thesis side | 3 / 0% / -27.39 / 30.0% | 7 / 0% / -69.58 | 2 / 0% / -24.12 | 7 / 14% / -29.83 | 6 / 0% / -55.35 | no theses in the first half |
| a, with the swing | 7 / 43% / +42.69 / 23.6% | 12 / 8% / -69.82 | 5 / 0% / -49.91 | 103 / 20% / -150.07 / -0.24 / 87.3% | 117 / 26% / -78.06 | -135.88, -55.92 / -80.50, +4.36 |
| b, long only | 2 / 0% / -17.96 / 29.2% | 5 / 60% / +17.29 | 0 trades | 22 / 41% / +76.49 / +0.38 / 44.9% | 24 / 38% / +66.76 | **-48.01**, +169.09 / **-21.79**, +100.58 |
| b, thesis side | 2 / 0% / -17.96 / 29.2% | 5 / 60% / +17.29 | 0 trades | 7 / 71% / +168.78 | 5 / 60% / +69.56 | no theses in the first half |
| b, with the swing | 2 / 0% / -17.96 / 29.2% | 5 / 60% / +17.29 | 0 trades | 33 / 36% / +64.16 / +0.25 / 48.8% | 34 / 50% / +469.73 | -11.35, +80.52 / +358.98, +37.27 |
| c, long only | 3 / 0% / -50.30 / 31.9% | 4 / 25% / -51.74 | 3 / 0% / -35.27 | 36 / 33% / +44.35 / +0.21 / 43.4% | 34 / 35% / +72.79 | -56.05, +145.06 / +38.07, +28.72 |
| c, thesis side | 2 / 0% / -32.24 / 30.4% | 4 / 25% / -51.74 | 2 / 0% / -29.62 | 3 / 33% / +13.23 | 2 / 0% / -20.97 | no theses in the first half |
| c, with the swing | 4 / 0% / -68.34 / 31.9% | 5 / 20% / -64.94 | 5 / 0% / -51.86 | 73 / 30% / -7.67 / +0.07 / 55.6% | 74 / 35% / +291.57 | +47.55, -43.79 / +140.23, +85.50 |

- Beside the live rule the textbook setup loses on the Brent journal in eight of nine families. The one that adds money (24h range, with the swing, +$42.69) loses $69.82 on the replay and $49.91 on WTI.
- Alone at 6% risk the long-only and with-the-swing rows draw down 43% to 87% on the 208-day history.

## The best variant per family (the ceiling, not the expectation)

With 246 to 738 tries per family the best row is what luck allows. All are NOISE.

| family | variant | Brent journal beside live: n / win % / net $ / max DD vs 27.9% | replay beside live: net $ / max DD vs 23.1% | WTI beside live | why it is not a result |
|---|---|---|---|---|---|
| a, long only | 38.2%, limit, 1 ATR stop, -27.2% target, be50 | 7 / 57% / +123.50 / 26.0% | +126.95 / 39.6% | 6 trades, -8.40 | all 7 trades in the first half; DD worse on replay; 208d second half -77 and -102 |
| a, thesis side | the same, thesis side | 6 / 67% / +152.51 / 25.1% | +126.95 / 39.6% | 5 trades, +5.26 | all 6 trades in the first half; DD worse on replay and WTI; neighbour at 50% loses |
| a, with the swing | 61.8%, confirm, next-level stop, -27.2%, be50 | 5 / 60% / +141.79 / 19.0% | -55.70 / 37.9% | 4 trades, -40.91 | loses on replay, WTI and both 208d sets (-111, -74) |
| b, long only | k 2, 38.2%, limit, 1 ATR stop, -61.8%, be50 | 2 / 100% / +103.69 / 26.7% | +109.38 / 28.0% | 1 trade, -0.60 | 2 trades; 208d second half -18 and -49 |
| b, thesis side | the same variant, the same trades as long only | 2 / 100% / +103.69 / 26.7% | +109.38 / 28.0% | 1 trade, -0.60 | 2 trades; WTI 208d alone -22.15 |
| b, with the swing | the same, with the swing | 3 / 100% / +240.50 / 17.3% | +95.64 / 31.2% | 1 trade, -0.60 | 3 trades; WTI 208d alone -3.01; neighbour at 50% loses |
| c, long only | 38.2%, limit, 1 ATR stop, swing extreme, be75 | 6 / 50% / +39.95 / 25.3% | +1.61 / 30.4% | 5 trades, -35.34 | no trade in the second half; loses on WTI and both 208d sets |
| c, thesis side | the same, thesis side | 5 / 60% / +63.42 / 24.0% | +1.61 / 30.4% | 4 trades, -23.30 | no trade in the second half; loses on WTI |
| c, with the swing | the same, with the swing | 7 / 57% / +129.85 / 21.0% | +7.58 / 30.4% | 7 trades, -52.00 | loses on WTI and on both 208d sets (-126, -132) |

75 of 3,690 variants are above zero beside live on all three datasets. 16 of them have 10 to 12 trades (all 24h range, with the swing) and each fails another leg of the bar. 11 of the 75 also hold in both halves of the primary: they have 6 to 8 trades, and every one loses alone on both 208-day sets (-$52 to -$141).

## Does it make money alone? Only the zigzag, and only lately

The whole grid alone at 6% risk, by swing setting (trades / win % / avg R / variants above zero):

| swing | Brent 15m 50d | Brent 1h 208d | WTI 15m 50d | WTI 1h 208d |
|---|---|---|---|---|
| 24h range | 10,695 / 22% / -0.19 / 149 of 738 | 34,685 / 30% / -0.04 / 191 of 738 | 10,936 / 29% / -0.08 / 301 of 738 | 36,185 / 31% / +0.00 / 308 of 738 |
| session | 4,986 / 27% / -0.17 / 248 of 738 | 20,468 / 31% / -0.07 / 249 of 738 | 4,884 / 27% / +0.07 / 318 of 738 | 19,979 / 30% / -0.02 / 302 of 738 |
| zigzag k 2 | 7,469 / 33% / +0.06 / 361 of 738 | 23,659 / 32% / -0.04 / 267 of 738 | 7,931 / 27% / -0.15 / 222 of 738 | 24,779 / 32% / -0.06 / 262 of 738 |
| zigzag k 3 | 4,711 / 41% / +0.68 / 478 of 711 | 13,160 / 35% / +0.21 / 469 of 729 | 4,759 / 32% / +0.30 / 378 of 711 | 12,596 / 36% / +0.25 / 487 of 729 |
| zigzag k 4 | 2,770 / 43% / +0.69 / 334 of 648 | 7,941 / 35% / +0.21 / 296 of 684 | 3,138 / 32% / +0.33 / 368 of 684 | 8,324 / 30% / +0.12 / 333 of 684 |

- **24h range and session swings lose alone** on Brent: a fifth to a third of their variants are above zero.
- **Zigzag k 3 is the one row that is positive on all four sets**: 53% to 67% of its variants above zero, avg R +0.21 to +0.68. k 2 is negative; k 4 is positive on avg R and a coin flip by count on the 208-day sets.
- **It does not hold walk-forward.** Zigzag, long only, 738 variants, 208-day history split at 08-06: above zero in the first half 150 (Brent) and 178 (WTI); in the second half 531 and 552. The edge is the Brent trend of the last 100 days (113 to 89 to 93), not the retracement.
- **24 of 3,690 variants** (0.65%) are above zero alone on every dataset and in both halves of both 208-day sets with 10 or more trades there. They are three setups repeated across targets and ratchets: zigzag k 3 long, 61.8% with confirmation, next-level stop (9); zigzag k 4 with the swing, 61.8% limit, next-level stop (11); session long, 78.6% with confirmation, origin stop (4). A grid this size produces that by chance. None is above zero beside live on all three datasets.

## Which fib level fills most, which wins most

Resting limit, no ratchet, alone, 6% risk, all families pooled. "Resting" = swings on which the order rested; fill % = fills / resting.

| level | Brent 15m 50d: resting / fills / fill % / win % / avg R | Brent 1h 208d | WTI 15m 50d | WTI 1h 208d |
|---|---|---|---|---|
| 38.2% | 3,810 / 1,021 / 26.8% / 23.2% / -0.10 | 12,872 / 3,464 / 26.9% / 27.7% / -0.07 | 3,726 / 1,047 / 28.1% / 24.3% / -0.18 | 13,053 / 3,728 / 28.6% / 30.4% / +0.06 |
| 50.0% | 4,897 / 1,201 / 24.5% / 23.0% / -0.08 | 15,964 / 3,926 / 24.6% / 28.3% / +0.06 | 4,417 / 1,054 / 23.9% / 32.7% / +0.47 | 15,471 / 3,818 / 24.7% / 29.4% / +0.17 |
| 61.8% | 5,553 / 1,153 / 20.8% / **39.5% / +0.74** | 18,518 / 3,920 / 21.2% / 29.9% / +0.19 | 5,555 / 957 / 17.2% / **20.9% / -0.35** | 18,513 / 3,976 / 21.5% / 27.3% / +0.02 |
| 78.6% | 4,396 / 629 / 14.3% / 16.7% / -0.34 | 14,968 / 2,633 / 17.6% / 21.3% / +0.01 | 4,333 / 818 / 18.9% / 25.9% / +0.31 | 15,372 / 2,887 / 18.8% / 21.1% / -0.00 |

- **Fills most: 38.2%** (27% to 29% of resting orders), then 50% (24% to 25%), 61.8% (17% to 21%), 78.6% (14% to 19%). The deeper the level, the fewer the fills, on both markets.
- **Wins most: no level, on both markets.** On Brent 61.8% is the best (39.5% wins, +0.74R on 15m; 29.9%, +0.19R on 208 days). On WTI over the same 47 days 61.8% is the worst (20.9%, -0.35R) and 50% and 78.6% are the best. A level that is the best on one oil contract and the worst on the other is not a property of the level.
- 38.2% loses on three of four sets: it fills most and is the shallowest dip.

Other axes, whole grid alone: a resting limit beats the confirmation entry on avg R on all four sets (+0.07 to +0.13 against -0.06 to +0.05); the confirmation wins more often (32% to 36% against 26% to 28%) and pays 6.6 bps median slippage for it. The next-level stop is the best of the three on Brent 50d (+0.25R) and near zero elsewhere. Extension targets beat the swing extreme (0%: -0.03 to -0.06R on every set; -61.8%: +0.04 to +0.22R).

## The ratchet: helps at 75%, cuts winners at 50%

Each ratcheted variant against its twin with the ratchet off, every other axis equal, alone, 6% risk. "Better / worse" = pairs whose net moved up / down.

| ratchet | dataset | pairs | better / worse | sum of the change $ | win % off -> on | avg R off -> on | targets hit off -> on | full stops off -> on | ratchet exits |
|---|---|---|---|---|---|---|---|---|---|
| be50 | Brent journal | 888 | 273 / 117 | +1,277.81 | 30.2 -> 27.7 | +0.003 -> +0.035 | 1,057 -> 932 | 3,143 -> 2,745 | 620 |
| be50 | Brent replay | 945 | 335 / 213 | -160.22 | 28.3 -> 26.4 | +0.004 -> +0.008 | 1,240 -> 996 | 4,458 -> 3,898 | 972 |
| be50 | Brent 15m 50d | 951 | 340 / 216 | -1,047.15 | 30.2 -> 28.6 | +0.067 -> +0.065 | 1,735 -> 1,450 | 5,430 -> 4,801 | 1,093 |
| be50 | Brent 1h 208d | 969 | 454 / 281 | +1,922.50 | 32.1 -> 29.6 | -0.005 -> +0.000 | 6,343 -> 5,260 | 17,305 -> 15,286 | 3,936 |
| be50 | WTI journal | 942 | 316 / 243 | -626.80 | 28.8 -> 23.2 | -0.069 -> -0.082 | 1,028 -> 770 | 3,395 -> 2,928 | 944 |
| be50 | WTI 15m 50d | 963 | 420 / 230 | +1,334.71 | 29.7 -> 25.0 | -0.005 -> +0.010 | 1,795 -> 1,457 | 5,626 -> 4,817 | 1,426 |
| be50 | WTI 1h 208d | 969 | 471 / 283 | +4,222.68 | 32.0 -> 29.7 | +0.017 -> +0.032 | 6,590 -> 5,713 | 17,640 -> 15,388 | 3,950 |
| **be75** | Brent journal | 888 | 119 / 32 | +1,156.02 | 30.2 -> 29.9 | +0.003 -> +0.029 | 1,057 -> 1,038 | 3,143 -> 2,988 | 203 |
| **be75** | Brent replay | 945 | 167 / 91 | +299.96 | 28.3 -> 28.0 | +0.004 -> +0.013 | 1,240 -> 1,160 | 4,458 -> 4,241 | 357 |
| **be75** | Brent 15m 50d | 951 | 178 / 92 | +541.31 | 30.2 -> 30.0 | +0.067 -> +0.077 | 1,735 -> 1,646 | 5,430 -> 5,181 | 401 |
| **be75** | Brent 1h 208d | 969 | 341 / 165 | +2,152.61 | 32.1 -> 31.3 | -0.005 -> +0.005 | 6,343 -> 5,930 | 17,305 -> 16,549 | 1,412 |
| **be75** | WTI journal | 942 | 190 / 115 | +572.27 | 28.8 -> 26.3 | -0.069 -> -0.056 | 1,028 -> 902 | 3,395 -> 3,159 | 449 |
| **be75** | WTI 15m 50d | 963 | 295 / 116 | +1,717.94 | 29.7 -> 27.6 | -0.005 -> +0.016 | 1,795 -> 1,630 | 5,626 -> 5,220 | 676 |
| **be75** | WTI 1h 208d | 969 | 404 / 178 | +3,185.86 | 32.0 -> 31.2 | +0.017 -> +0.029 | 6,590 -> 6,195 | 17,640 -> 16,695 | 1,698 |
| fib75 | Brent journal | 678 | 109 / 62 | +476.57 | 28.6 -> 32.1 | -0.060 -> -0.040 | 631 -> 565 | 2,323 -> 2,209 | 213 |
| fib75 | Brent replay | 720 | 170 / 56 | +1,741.23 | 29.0 -> 32.6 | +0.094 -> +0.137 | 860 -> 785 | 3,236 -> 3,077 | 310 |
| fib75 | Brent 15m 50d | 720 | 157 / 89 | +584.44 | 31.3 -> 34.4 | +0.137 -> +0.152 | 1,221 -> 1,085 | 3,977 -> 3,798 | 394 |
| fib75 | Brent 1h 208d | 720 | 247 / 165 | +1,144.57 | 31.9 -> 34.2 | +0.032 -> +0.036 | 4,606 -> 3,981 | 13,067 -> 12,591 | 1,428 |
| fib75 | WTI journal | 711 | 150 / 92 | +394.67 | 27.9 -> 32.1 | -0.048 -> -0.035 | 700 -> 534 | 2,646 -> 2,507 | 367 |
| fib75 | WTI 15m 50d | 720 | 215 / 88 | +1,936.60 | 30.6 -> 34.6 | +0.060 -> +0.090 | 1,318 -> 1,124 | 4,251 -> 4,016 | 535 |
| fib75 | WTI 1h 208d | 720 | 282 / 178 | +1,273.08 | 30.9 -> 33.8 | +0.006 -> +0.014 | 4,654 -> 4,138 | 13,447 -> 12,921 | 1,566 |

- **At 75% of the way to target the ratchet helps**, both forms: the summed change is positive on all seven datasets, and more pairs get better than worse on every one (1.5 to 4 to one). be75 gives up 2% to 12% of the target hits, fib75 9% to 24%, and turns a larger number of full stops into exits at or above the entry.
- **At 50% it is a wash**: negative on three of seven datasets (replay, Brent 50d, WTI journal). It gives up 12% to 25% of the target hits. This is the "cuts winners" case.
- **Moving the stop to the 38.2% level** (fib75) lifts the win rate 2 to 4 points because the exit is then above the entry. Moving to break-even lowers it: a break-even exit still pays fees and 3 bps slippage, so it counts as a small loss.
- **The size of it: about +0.01R a trade.** The ratchet does not turn a losing family into a winning one. Medians of the paired change are $0.00 on every row: most pairs never reach the trigger.

## The operator's own trade, costed

Long 92.96, stop 92.695, target 93.709: the stop is 28.5 bps away, planned 2.83R. After the measured fee and 3 bps stop slippage: **2.38R (taker entry), 2.45R (limit entry). It needs 29% to 30% wins to break even.** Across the grid the win rate of tight next-level stops is 27% to 32% (Brent 50d 31.9%, Brent 208d 28.9%, WTI 50d 27.0%, WTI 208d 29.0%): at the break-even line, not above it. A 28.5 bps stop sits inside one median 15m bar (22.7 bps high to low on Brent).

## Fit with the live long book

| family | Brent journal: fib trades alone that overlap a live long |
|---|---|
| a, long only / thesis side / with the swing | 780 of 1,414 (55%) / 699 of 1,262 (55%) / 2,406 of 3,453 (70%) |
| b, long only and thesis side / with the swing | 1,259 of 1,993 (63%) / 3,036 of 4,139 (73%) |
| c, long only / thesis side / with the swing | 319 of 746 (43%) / 250 of 605 (41%) / 1,499 of 2,169 (69%) |

Both rules buy dips, so they want the same slot. Under the one-position rule the fib sleeve is either blocked by the live long or closed when the live long enters, which is usually at the low of the dip the fib trade bought: a fib long that would have won alone is cut at a loss. That is why "beside live" is worse than "alone" for the long families. Combined P&L on the Brent journal at 6% (live net + sleeve): live alone +$174.12; with the textbook zigzag setup +$156.16; with the textbook 24h-range setup, with the swing, +$216.81 (and -$69.82 on the replay).

## Multiple testing

3,690 variants, two risk sizes, 7 datasets. 0 PASS and 0 LEANING. 75 variants are above zero beside live on all three datasets (11 of them in both halves too) and 24 hold alone everywhere. No variant is in both lists. With this many tries those counts are what chance gives.

## Gut call

No live money on a fib rule, and no change to the live rule.

- **The retracement level carries no edge here.** The best level on Brent is the worst on WTI over the same days. The 24h-range and session anchors lose alone.
- **The zigzag result is a trend result.** Buying a pullback in a strong leg worked from August to November because Brent trended. From April to August the same rules lost on 80% of the grid.
- **Beside the desk it subtracts.** The hand trade and the desk compete for one slot; in the test the fib sleeve lowers the median outcome on every dataset.
- **For the hand trades the operator takes anyway:** keep the ratchet at 75% or later, not 50%; prefer the stop moved to the 38.2% level over plain break-even; a 28 bps stop needs about 30% winners at 2.8R and the market gives about that, so the size should stay small. This is advice on execution, not an edge.
- **Worth watching on paper:** zigzag k 3, 61.8%, stop beyond 78.6%. It needs to hold through a non-trending month before it means anything.

## Method

One script, deterministic, no LLM call: `services/strategies/evals/fib_retrace_test.py`. Same cache in, same numbers out (two runs on one cache, 8 and 3 worker processes, gave a byte-identical `variants.csv`; the run takes about 75 seconds).

- **Reused, not rebuilt:** the candle and funding loaders (`backtest/data.py`, `market_scout.load_coin_candles`), the measured fee (`intraday_5m_test.measured_fees`), ATR, the trade summary and the P&L curve (`intraday_5m_test`), `risk_capped_notional` and the slippage constant (`desk_tick_sim`), the 20/15 leverage fit (`next_trade.fit_leverage`), the weekend clock (`weekend.py`), the live long book, its masks, drawdown, collisions, drop-best-day and the bar (`short_ladder_test.prepare`, `max_dd_pct`, `collisions`, `drop_best_day`, `judge`).
- **Swings read closed 1h bars only.** ATR14 is the mean true range of the last 14 closed 1h bars. A zigzag pivot is known at the close of the bar that confirms it. The 24h-range and session swings need a move of at least 1 ATR. The session needs 4 closed hours after the reopen.
- **One entry per swing.** A level that price has traded through since the swing's end is spent. No order in the weekend or on the first bar after it.
- **Fills.** Limit: at the level, when the bar trades through it by one tick; an order that rested on an earlier bar and is gapped through fills at the open; maker fee. Confirmation: the open of the next bar, taker fee, slippage = half the range of a 5m bar, taken from the fill bar's range by the square-root-of-time rule (0.289 x a 15m bar's range, median 6.6 bps on Brent, 6.8 on WTI), never under 3 bps. It is the half-range model of `SHORT_LADDER_TEST_2026-11-16.md` brought to 15m bars.
- **Exits, per bar:** the ratchet is read at the tick from closed bars and applies from the next bar; funding on the hour; a market close at the open when the live long enters or the weekend starts; the stop (resting, taker, 3 bps; a bar that opens beyond it fills at its open; it can fire on the fill bar); the target (resting limit, maker, a trade-through by one tick, never on the fill bar). A stop and a target in one bar is a loss. A trade open at the end is marked at the last close.
- **Sizing:** `risk_capped_notional` at 6% or 1.2% of equity at the stop, capped by the full size at the fitted leverage. A stop too wide for 15x is refused (about 2% of price), as place_bracket would refuse it.
- **Beside live:** sized off the live book's marked equity plus the sleeve's own P&L. The live trades keep their own sizes, so the combined net is the live net plus the sleeve's.
- **Halves:** each half is its own run with a fresh live book.
- **The 1h sets** run the same rules with 1h bars as the tick: the confirmation is a 1h close, the ratchet is read hourly.
- **Not applied to the sleeve:** the event and headline blackouts and NO_BREAK_ENTRY. The live baseline in the sim does not apply the blackouts either. A resting limit below the market never chases.

Where it would break:

- **One regime.** 15m candles cover 50 days, the journal 29. The 208-day sets are 1h bars, which hide the path inside the hour: a stop and a target in one hour is scored a loss.
- **Small counts beside live.** The live long is open most of the time, so no long-only variant reaches 10 trades beside it. NOISE means "cannot be judged", not "works".
- **The zigzag anchor depends on k.** k 2 loses, k 3 wins, k 4 is mixed: no plateau.
- **The operator draws swings by eye.** No rule here is the operator's rule; three mechanical anchors stand in for it.
- **The maker fee is three fills.** At a taker-only target the round trip is 0.6 bps worse.
- **The WTI theses are Brent's**, side only.
- **The replay dataset is read from `/tmp/thesis-replay-30b`.** Without it brent_replay30 is skipped and the counts change.

## Reproduce

    # the full run (about 75 s on 8 cores); exit 1 = no family passes
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.fib_retrace_test \
        --cache-dir <fresh dir> --out-dir <dir>

    # with place_bracket's 2R gate
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.fib_retrace_test \
        --cache-dir <same dir> --out-dir <dir2> --min-r 2

    # cost another hand trade; keep the trades of named variants
    PATHIEL_STATE_DIR=<pathia>/.state .venv/bin/python -m services.strategies.evals.fib_retrace_test \
        --cache-dir <same dir> --out-dir <dir> --op-trade 92.96 92.695 93.709 --detail zz3_long_L618_limit_next_T0_off

    # gate tests (49, no network, under 2 s)
    .venv/bin/python -m pytest -q services/strategies/tests/test_fib_retrace.py

Outputs: `<out>/report.json` (datasets, fee, the nine families at both risk sizes with their grids cut by level, entry, stop, target, ratchet and swing, the level table, the ratchet table, the reference and best variants in full, the operator's trade), `<out>/variants.csv` (one row per variant and risk size, every dataset), `<out>/best_trades.csv`, `<out>/progress.log`.
