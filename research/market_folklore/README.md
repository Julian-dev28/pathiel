# Market Folklore Stress Test

This is a deliberately speculative, offline research exercise. It tests calendar,
solar-position, company-name, and Chinese-zodiac stories as falsifiable trading
rules. It does not feed Hermes' live loop and is not investment advice.

## What it tests

1. **Date root 1-4-7** — buy the S&P at the open and sell at the close when the
   digit root of `YYYYMMDD` is 1, 4, or 7.
2. **Solar cardinal window** — the same intraday trade during the first 2.5
   degrees after an equinox or solstice.
3. **Prime calendar day** — the same intraday trade when the day-of-month is
   prime.
4. **Friday the 13th** — an event study, not an executable claim because the
   sample is small.
5. **Solar-sign scan** — all twelve 30-degree solar longitude bins. This is
   explicitly exploratory; it uses a family-wise correction to show why the
   best-looking sign is not automatically a strategy.
6. **Chart analogs** — compare the latest 63 sessions with every earlier
   63-session return path, then separately rank best analogs in 1929, dot-com,
   2008, and selected boom eras. “Within 2–3%” means maximum path deviation of
   at most 2 or 3 percentage points after normalizing each path to zero.
7. **Zodiac-founder trine** — hold a small declared universe only in calendar
   years whose Chinese-zodiac trine matches the company's founding-year trine.
8. **Name/year resonance** — hold a company when the Pythagorean numerology
   root of its founding name matches the year root.

The company experiments are controlled by shuffling founding years or name roots
across the same companies. They are still survivor-biased: the universe contains
only successful firms selected today, so no result can establish an investable
edge.

## Run / reproduce

```bash
# First run (or to refetch): downloads via curl, fills .cache/
.venv/bin/python research/market_folklore/run.py --refresh

# Re-run offline from the cache (deterministic given the same cache + seeds)
.venv/bin/python research/market_folklore/run.py

# Tests (no network; the downloader is exercised against a mocked subprocess)
.venv/bin/python -m pytest tests/test_market_folklore.py -q
```

The run writes `RESULTS.md`, `analogs.csv`, and `analogs.html` in this
directory and stores raw responses in the ignored `.cache/` directory.
Permutation trials default to 5,000 (`--trials`); seeds are fixed in `run.py`,
so a cached re-run reproduces the committed numbers exactly.

Downloads go through a bounded `curl` subprocess: browser User-Agent,
`--connect-timeout 10 --max-time 60`, HTTPS-only (`--proto =https`), three
retries with exponential backoff, and payload validation before the cache file
is committed atomically. Certificate validation is never disabled. (The
previous `urllib` path hung on SSL; that is why the fetch is a subprocess with
hard bounds.)

## Results summary (data through 2026-07-17)

Full tables in `RESULTS.md`. Verdicts are mechanical; criteria are printed in
the report.

| Fixed rule | Avg bp/session | p | Verdict |
| --- | --- | --- | --- |
| Date digit-root 1/4/7 | +4.06 (vs +2.81 all-session) | 0.116 | INCONCLUSIVE |
| Solar cardinal 0–2.5° | −1.80 | 0.858 | REFUTED |
| Prime day-of-month | +3.43 | 0.268 | INCONCLUSIVE |
| Friday the 13th (event study) | +4.43, halves −5.49/+14.83 | 0.423 | INCONCLUSIVE |

- **Analogs:** the latest 63 sessions (2026-04-17 → 2026-07-17) look most like
  calm grind-up tapes: best match ends 1995-11-16 (RMSE 1.00%, max deviation
  2.60%), which was followed by +1.59% over the next 21 sessions. Zero
  non-overlapping historical windows matched within 2 points; 25 matched
  within 3. Closest 1929-crash-era shape (1930-09-18) was followed by −16.57%,
  but its fit is much worse (max deviation 5.48%). Descriptive only.
- **Exploratory net:** the only bucket surviving the family-wise correction is
  solar Capricorn (+9.70 bp, family p = 0.017) — which is Dec 22 to Jan 19,
  i.e. the long-documented turn-of-year seasonal wearing an astrology costume.
  Exploratory, not a strategy.
- **Company astrology (survivor-biased universe):** zodiac-trine returned
  30.3% annualized vs 31.6% for the equal-weight baseline (shuffle p = 0.105);
  name/year resonance returned 8.9% vs 31.6% (p = 0.937). Neither beats simply
  holding the declared winners.

One-line conclusion: none of the folklore rules produced a supported edge; the
one "significant" exploratory bucket is a known calendar seasonal in disguise.

## Method choices

- Date and solar rules are known before each session and are simulated
  open-to-close, avoiding close-to-close look-ahead.
- The solar longitude is an approximate geocentric calculation at noon UTC. It
  is sufficient for 30-degree signs and 2.5-degree ingress windows, not a
  natal-chart calculation.
- The historical S&P series begins in 1928. Treat pre-1957 observations as the
  predecessor/back-tested composite rather than the modern 500-constituent
  index.
- Yahoo's pre-1962 bars carry a synthetic open equal to the close (only closes
  were recorded), which would fake a motionless market for three decades under
  a naive open-to-close rule. For those sessions the harness falls back to the
  prior-close-to-close return and says so in the report. Calendar signals are
  known before the session either way, so neither measure looks ahead.
- Reported permutation p-values ask how often equally many randomly selected
  sessions or randomly relabeled companies do as well. They do not prove a
  causal effect.
