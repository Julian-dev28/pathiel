# W-Q — Forensic audit: the "25/25 strategies beat the June gate" leaderboard

2026-07-12. Operator supplied two screenshots (Downloads: `image (8).jpg`,
`image (9).jpg`) of a strategy leaderboard claiming: *"All 25/25 strategies
beat the June gate and remained profitable during the final 30% OOS period"*,
with rows like:

| rank | strategy | return | Sharpe | max DD | OOS return | OOS Sharpe |
|---|---|---|---|---|---|---|
| 1 | Vol-scaled cooldown | 2,311.79% | 7.07 | −5.36% | 167.32% | 7.88 |
| 2 | Dominance timebox | 2,830.97% | 6.37 | −4.27% | 170.89% | 6.83 |
| 3 | EMA oscillator crossing | 528.40% | 4.71 | −5.74% | 85.05% | 5.46 |
| 4 | Dominance transition | 524.79% | 4.32 | −7.30% | 82.79% | 5.24 |
| 5 | EMA soft alignment | 5,221.02% | 4.26 | −7.31% | 252.65% | 4.94 |
| 9 | Burst persistence | 563.30% | 4.00 | −11.54% | 74.95% | 5.26 |
| 10 | Vol-normalized pressure | 760.74% | 3.80 | −10.42% | 138.55% | 4.20 |

## Provenance: UNKNOWN — and that is finding #1

Searched exhaustively on this machine (pathia incl. research/,
scratchpad/, all sibling projects under ~/Documents/code, Downloads):
**no source code, no data, no config produces or mentions these strategies.**
The only artifacts are the two saved images. Nothing here can be reproduced,
therefore nothing here is currently evidence. Everything below is what the
numbers imply on their faces, judged against what this project has measured
on real Hyperliquid data.

## Red flags, ranked

1. **25/25 pass = the gate has zero rejection power.** A validation gate that
   passes every candidate is not a gate; it is a formatting step. Our own
   frontier waves reject 70-95% of pre-registered cells (Lane K: 3/3 signal
   families refuted OOS; W-M: 624/624 cells flat; Lane F: 1 of 4 lanes
   survived). A process that has never said "no" has never been tested.
2. **Sharpe 7-7.9 with max DD under 6%.** Renaissance Medallion, the best
   documented strategy in history, runs Sharpe ~2-3 gross. Retail-accessible
   crypto signals producing sustained Sharpe 7 at 2,300% return would be
   arbitraged to dust in weeks. Numbers in this range are, in every case we
   have personally checked, one of: lookahead, missing costs, survivorship,
   or leverage compounding on an in-sample fit.
3. **"Final 30% OOS" is not out-of-sample if you saw it.** If 25 strategies
   were selected/ranked WITH knowledge of the final-30% results (a
   leaderboard sorted by OOS Sharpe is exactly that), the OOS column is
   in-sample by construction. Real OOS requires frozen thresholds committed
   before the test window is ever scored — we learned this the expensive way
   (KillaXBT loc_fade: train +1.92%/tr p=.008 → untouched test −2.61%
   p=.985).
4. **OOS Sharpe > in-sample Sharpe on 6 of 7 visible rows.** Genuine edges
   degrade out of sample (regression to the mean is mechanical). Improvement
   OOS at this scale suggests the "OOS" period enjoyed a favorable regime —
   or the split leaks.
5. **Strategy-name smell.** "Vol-scaled cooldown", "Dominance timebox",
   "EMA soft alignment" are parameterized indicator combinations — precisely
   the family our own program has refuted on this venue at scale
   ([[project_williams_patterns_neg_ev]]: every indicator-pattern entry −EV;
   [[project_price_entries_no_edge]]: every absolute price-pattern long −EV
   OOS; the 07-09 audit: comments in refuted factor books LIED about +EV).
6. **No costs visible.** At crypto perp fee/slippage reality (25bps round
   trip + funding), high-turnover EMA/burst systems lose their paper edge
   fast — our fee-viability audit found sub-1h holds need >37bps/trade edge
   just to break even. A 2,300% return line without a costs footnote is a
   gross line.

## Verdict

**PRESUMPTIVELY OVERFIT — not evidence, not actionable, nothing gets wired
or sized off these numbers.** The claims are extraordinary; the burden of
proof sits entirely on the source. This is the same epistemic class as the
KillaXBT screenshots (audited: strict record 1 HIT / 1 MISS) and our own
pre-audit backtests (the "+0.46%" nff claim that regraded to −2.0%/ep).

## HANDOFF INSTRUCTIONS — how to actually validate (any agent, ~1 day)

Precondition (operator): supply the SOURCE — repo/notebook/tool that
produced the table, plus its data. Without it, stop here; do not attempt to
reimplement from strategy names (you would be fitting our own version of
someone else's overfit).

With source in hand, run this protocol per strategy (all tooling exists in
this repo):

1. **Reproduce first.** Run their code unchanged on their data. If the
   numbers don't reproduce byte-for-byte, STOP — report the delta.
2. **Lookahead sweep.** Check every feature for future information: bar
   indexing (our candleSnapshot returns the bar CONTAINING t — trap
   documented in HANDOFF-CLAUDE.md), signal-at-close-traded-at-close,
   high/low ordering optimism (use pessimistic adverse-extreme-first fills —
   see `research/killa_xbt/` engine and `W-U1_unlock_backtest.py::short_trade`).
3. **Costs.** Re-run at 25bps round trip + funding for holds ≥8h (helpers in
   `pathia/agents/shadow_ledger.py::funding_return`). Report the
   equity curve before/after. Kill anything whose edge halves.
4. **Frozen re-test.** Freeze every threshold at their published values, run
   on OUR data (`W-H0` hourly cache, 208d × 40 coins + fresh fetch for the
   period after their sample ends — the only truly untouched window).
5. **Matched nulls.** ≥2,000 same-coin random-time entries per strategy
   (pattern in `W-U1_unlock_backtest.py::mc_pvalue`). Demand p < 0.01 given
   25-way selection (Bonferroni: raw p < 4e-4).
6. **Survivorship.** Confirm their universe was point-in-time, not today's
   listings replayed backward (our young_listings/W-Y notes cover the trap).
7. **Report** per strategy: reproduced? / lookahead findings / net-EV25 /
   frozen-retest verdict / null p / survivorship — one row each, then an
   Outcome per the KillaXBT A-D framework (A reject, B shadow recorder,
   C experimental flag, D production candidate). Anything reaching B ships
   as a zero-capital recorder to `.state/shadow_ledger/` with a ≥30-episode
   promotion bar — never directly to capital.

House rules that bind this work: no live config changes from research, no
gate weakening, serialize builders on the live tree, tests in the same
commit, and the operator's refuted-rule (REFUTED → delete, not shadow).

## Priors to carry in (from this repo's measured history)

- Indicator-combination entries on HL perps: refuted at scale, repeatedly.
- Train→test degradation is the NORM: +1.92% → −2.61% (loc_fade) is what a
  textbook overfit looks like at n=100+.
- The only validated edges here are event-anchored (funding spikes, crashes,
  unlocks run-ins) at +1-6%/episode — nobody has produced a sustained
  Sharpe > ~2.5 on this venue under honest accounting in our entire program.
