# Market Folklore Stress Test — Results

> Deliberately speculative research, not investment advice or a live-trading input.

## Dataset

- Daily S&P Composite / S&P 500 chart data: **1928-01-03 to 2026-07-17** (24,751 sessions).
- Price source: Yahoo Finance public chart endpoint, fetched on 2026-07-19. The date rules use open-to-close returns, so each signal is known before the open.
- Data caveat: Yahoo's pre-1962 bars carry a synthetic open equal to the close (only closes were recorded). For those sessions the rule return falls back to prior-close-to-close; calendar signals are known before the session either way, so neither measure looks ahead.
- The S&P 500 launched in 1957; earlier observations are the historical predecessor/back-tested composite. That makes 1929 useful for a shape comparison but not identical to the modern index.

## Predeclared Rules

These are the only date/solar rules treated as strategies rather than post-hoc discovery. `p` is a one-sided permutation p-value against random session selections of identical size. A persuasive signal should have a low p-value **and** retain its sign in both chronological halves.

| Rule | Sessions | Exposure | Avg bp/trade | Annualized | Max DD | p | 1st half bp | 2nd half bp | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Date digit-root 1 / 4 / 7 | 8230 | 33.25% | +4.06 | 2.92% | -68.31% | 0.116 | +2.48 | +5.66 | INCONCLUSIVE |
| Solar cardinal 0–2.5° | 707 | 2.86% | -1.80 | -0.18% | -30.49% | 0.858 | -2.61 | -0.98 | REFUTED |
| Prime day-of-month | 8617 | 34.81% | +3.43 | 2.45% | -67.48% | 0.268 | +4.09 | +2.76 | INCONCLUSIVE |
| Friday the 13th event | 168 | 0.68% | +4.43 | 0.06% | -16.04% | 0.423 | -5.49 | +14.83 | INCONCLUSIVE |

Interpretation: no row qualifies as a reliable trading signal merely because it has a positive annualized return. The all-session intraday average is **+2.81 bp**. Verdicts are mechanical: SUPPORTED needs `p < 0.05`, a lift over the unconditional average, and a positive mean in both chronological halves; REFUTED means no lift at all or `p >= 0.5` (no better than the median random draw); everything else is INCONCLUSIVE.

## Exploratory Fishing Net

I scanned solar-sign and date-root buckets to make the inevitable selection bias visible. `Family p` is the raw one-sided normal-approximation p-value multiplied by 12 or 9 tests. These rows are diagnostics, **not** signals selected for deployment.

| Family | Bucket | Sessions | Avg bp | Vs rest bp | Raw p | Family p |
| --- | --- | --- | --- | --- | --- | --- |
| Solar sign | Capricorn | 1868 | +9.70 | +7.46 | 0.0014 | 0.0169 |
| Solar sign | Cancer | 2121 | +5.78 | +3.25 | 0.0843 | 1.0000 |
| Date root | 4 | 2747 | +5.39 | +2.91 | 0.1071 | 0.9638 |
| Date root | 2 | 2763 | +4.89 | +2.34 | 0.1639 | 1.0000 |
| Date root | 1 | 2738 | +4.17 | +1.53 | 0.2463 | 1.0000 |
| Solar sign | Aries | 2059 | +4.02 | +1.32 | 0.3089 | 1.0000 |
| Solar sign | Aquarius | 2027 | +3.99 | +1.29 | 0.2883 | 1.0000 |
| Solar sign | Gemini | 2113 | +3.38 | +0.63 | 0.4036 | 1.0000 |
| Date root | 6 | 2754 | +3.31 | +0.57 | 0.4030 | 1.0000 |
| Solar sign | Leo | 2184 | +3.26 | +0.50 | 0.4215 | 1.0000 |
| Solar sign | Sagittarius | 1969 | +3.17 | +0.40 | 0.4372 | 1.0000 |
| Date root | 7 | 2745 | +2.63 | -0.19 | 0.5337 | 1.0000 |
| Solar sign | Pisces | 2049 | +2.58 | -0.25 | 0.5379 | 1.0000 |
| Date root | 8 | 2740 | +2.54 | -0.30 | 0.5515 | 1.0000 |
| Date root | 5 | 2748 | +2.17 | -0.72 | 0.6181 | 1.0000 |
| Solar sign | Scorpio | 2021 | +1.78 | -1.12 | 0.6407 | 1.0000 |
| Date root | 3 | 2762 | +0.65 | -2.43 | 0.8548 | 1.0000 |
| Solar sign | Taurus | 2183 | -0.43 | -3.55 | 0.9272 | 1.0000 |
| Date root | 9 | 2754 | -0.48 | -3.69 | 0.9351 | 1.0000 |
| Solar sign | Libra | 2100 | -1.35 | -4.54 | 0.9133 | 1.0000 |
| Solar sign | Virgo | 2057 | -1.40 | -4.59 | 0.9494 | 1.0000 |

## 63-Session Chart Analogs

The latest normalized path runs from **2026-04-17** through **2026-07-17**. Paths are rebased to 0%, so the test is shape-only. Across 24,605 eligible historical endpoints, **0** non-overlapping windows stayed within 2 percentage points at every point and **25** stayed within 3 points (windows counted so no two share a session; ranked rows below are non-overlapping too).

| Overall rank | End date | RMSE | Max deviation | Correlation | Following 21 sessions |
| --- | --- | --- | --- | --- | --- |
| 1 | 1995-11-16 | 1.00% | 2.60% | 0.873 | +1.59% |
| 2 | 1963-11-04 | 1.04% | 2.31% | 0.892 | +0.75% |
| 3 | 1963-03-20 | 1.08% | 3.22% | 0.852 | +4.97% |
| 4 | 1964-11-24 | 1.13% | 2.46% | 0.846 | -1.84% |
| 5 | 1958-03-19 | 1.14% | 2.72% | 0.862 | +1.47% |
| 6 | 1993-02-09 | 1.16% | 3.52% | 0.824 | +1.88% |
| 7 | 1964-03-12 | 1.20% | 2.66% | 0.824 | +0.87% |
| 8 | 1976-08-24 | 1.20% | 3.21% | 0.821 | +5.58% |
| 9 | 1992-09-16 | 1.21% | 2.88% | 0.830 | -2.46% |
| 10 | 1989-03-22 | 1.21% | 3.22% | 0.883 | +6.58% |

### Named crash and boom eras

| Era | Best endpoint | RMSE | Max deviation | Correlation | Following 21 sessions |
| --- | --- | --- | --- | --- | --- |
| Roaring Twenties boom | 1929-06-21 | 1.91% | 6.13% | 0.632 | +8.30% |
| 1929 crash | 1930-09-18 | 2.11% | 5.48% | 0.761 | -16.57% |
| Dot-com boom | 1997-09-22 | 1.50% | 3.69% | 0.787 | +1.76% |
| Dot-com unwind | 2002-01-22 | 1.47% | 3.43% | 0.794 | -3.43% |
| Global financial crisis | 2007-07-05 | 1.30% | 4.01% | 0.776 | -6.05% |
| Post-GFC bull | 2017-02-09 | 1.23% | 2.99% | 0.805 | +2.84% |

Open `analogs.html` for the overlaid paths and `analogs.csv` for the raw ranking. The final column is what happened afterward in history, not a forecast. Pattern matching is particularly vulnerable to data mining and regime changes.

## Company Name / Founding-Year Rules

The trine rule buys the listed companies only in calendar years whose Chinese-zodiac trine matches the company's founding-year trine. The name rule buys a company if its Pythagorean name root matches the calendar-year root. Both rebalance annually, use split-adjusted returns, and compare with static label shuffles across the same firms (the `--trials` count). A year with no matching company sits in cash. The benchmark is an equal-weight portfolio of whichever universe names have data that year, so early years hold only a couple of firms.

| Rule | Years | Annualized | Equal-weight | Max DD | Shuffle p | 1st half | 2nd half |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Founder-year zodiac trine | 1980–2025 | 30.34% | 31.56% | -45.12% | 0.105 | 41.10% | 20.41% |
| Name/year root resonance | 1980–2025 | 8.92% | 31.56% | -49.84% | 0.937 | 10.88% | 6.99% |

| Ticker | Founding name | Year | Zodiac | Name root |
| --- | --- | --- | --- | --- |
| AAPL | Apple | 1976 | Dragon | 5 |
| MSFT | Microsoft | 1975 | Rabbit | 1 |
| AMZN | Amazon | 1994 | Dog | 7 |
| NVDA | NVIDIA | 1993 | Rooster | 5 |
| GOOGL | Google | 1998 | Tiger | 7 |
| META | Facebook | 2004 | Monkey | 4 |
| TSLA | Tesla | 2003 | Goat | 3 |
| NFLX | Netflix | 1997 | Ox | 9 |
| ORCL | Oracle | 1977 | Snake | 9 |
| ADBE | Adobe | 1982 | Dog | 9 |
| CSCO | Cisco | 1984 | Rat | 4 |
| INTC | Intel | 1968 | Monkey | 6 |

This company test is strongly survivor-biased and uses a tiny manually declared universe of current large firms. It cannot support capital allocation even if a result looks good; the shuffle p-value only says whether the labels beat relabelings within this already-biased sample.

## Bottom Line

- **Date digit-root 1 / 4 / 7** — INCONCLUSIVE (p = 0.116, +4.06 bp/session vs +2.81 bp unconditional).
- **Solar cardinal 0–2.5°** — REFUTED (p = 0.858, -1.80 bp/session vs +2.81 bp unconditional).
- **Prime day-of-month** — INCONCLUSIVE (p = 0.268, +3.43 bp/session vs +2.81 bp unconditional).
- **Friday the 13th event** — INCONCLUSIVE (p = 0.423, +4.43 bp/session vs +2.81 bp unconditional).

This is a stress test for charming market stories, not evidence that astrology or numerology dictates prices. The implementation preserves the results so claims can be audited, but none should enter a live strategy without a separately precommitted, out-of-sample protocol and conventional risk controls.
