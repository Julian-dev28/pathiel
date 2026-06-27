# A15 carry_plus_trend 💰

**Hypothesis:** Combine funding-carry (short rich-funding / long cheap-funding) with
price-momentum — the two strongest cross-sectional factors in perp markets.

**Status: BLOCKED-DATA.** The dataset's `universe[coin].funding` is a single scalar
*snapshot* of the current funding rate (verified: 40 coins, each a float, e.g. BTC
-2.45e-6), not a time series. Carry is a time-varying factor; with one point per coin it
cannot be ranked-then-held-then-rebalanced through history, so there is nothing to backtest.
Needs data_logger funding history (~1-2 wk of accrual) before it can be validated.

**What was done instead:** wired and unit-tested the combination logic on synthetic funding
(`carry_plus_trend.py`): cross-sectional z(trend) - z(carry) score, long top / short bottom,
market-neutral. Synthetic test passes — a cheap-funding + strong-trend coin is selected long,
an expensive-funding + weak-trend coin is selected short. The function is ready to drop onto
real funding history the moment it exists.

**Verdict: BLOCKED-DATA** — logic staged + unit-tested, no historical funding to validate on.
