# KillaXBT methodology research spec (operator-provided, 2026-07-11)
Target branch: able. Research-only; no live behavior changes until OOS validation.
## Part 1: Audit KillaXBT (@KillaXBT) predictions Jul 2024 - Jul 2026
Collect identifiable market calls (posts/quotes/replies/charts/archives/articles).
Structured record per call: {id, asset, post_url, posted_at, retrieved_from,
source_quality: direct|archived|article_quote|screenshot|unverified, original_text,
prediction_type: direction|target|timing|range|cycle|invalidated_setup, direction,
reference_price, target_low/high, invalidation_price, prediction_horizon,
methodology_tags, ambiguities, subsequent_market_result, grade, confidence_in_grade}.
Vague/non-falsifiable calls get NO win/loss grade. Buckets: fully/partially/
directionally testable, unverifiable, vague. Account for deleted-post bias,
survivorship, post-hoc screenshots, edits, conflicting scenarios, no-expiry calls,
target-after-adverse flags, selective reposting. NO headline win rate unless sample
complete + grading rules fixed before outcomes examined.
## Part 2: Reconstruct methodology
Evidence-based only (no named-methodology claims from chart arrows). Component
records: {component, description, evidence, confidence, can_be_quantified,
possible_definition, lookahead_risk, implementation_risk}. Initial hypothesis to
confirm/reject: HTF regime classification + range geometry + deviation-vs-acceptance
+ time-in-structure + scenario pathing + avoiding mid-range entries.
## Part 3: Inspect Pathia (research.py, system_prompt.py, ta_filter.py, risk_gates.py,
indicators/, perception.py, memory.py, client/hl_client.py, models/types.py, scripts/,
tests/, .agent-config.json). Document existing measurements; no duplicate features.
## Part 4: Testable features
A. HTF range state: TREND_UP/DOWN, RANGE, BREAKOUT_UP/DOWN_CANDIDATE,
ACCUMULATION/REDISTRIBUTION_CANDIDATE (provisional until confirmed; no future-price
labeling), UNKNOWN. Inputs: 4h/1d candles, range width/ATR, ADX level+slope, EMA
slope, range-bound closes, boundary touches, volume, OI, funding.
B. Range geometry: range_high/low/mid, width pct+ATR, price_location_0_to_1,
distance_to_high/low/mid_atr, bars_in_range, boundary touch counts.
C. Deviation vs acceptance: upper/lower deviation = trades >{0.10,0.25,0.50} ATR
beyond boundary then closes back inside; acceptance = {1,2,3} closes beyond;
optional volume/OI confirm. Thresholds from TRAIN periods only.
D. Time-in-structure: bars_in_range, bars_since_impulse, prior impulse/consolidation
durations + ratio, range_age_percentile (contextual features, not standalone signals).
E. Entry location buckets: 0-.2/.2-.35/.35-.65/.65-.8/.8-1. Hypothesis: RANGE state
+ mid-location (0.35-0.65) => PASS unless confirmed acceptance. Not hard-coded live.
## Part 5: Framework at research/killa_xbt/{calls.json, methodology.json, README.md},
scripts/research_killa_xbt.py, scripts/backtest_range_structure.py,
pathia/indicators/range_structure.py (research-only), tests/test_range_structure.py.
Prevent: lookahead, leakage, survivorship, test-set tuning, future-confirmed
boundaries, post-close info, ignored fees/slippage/funding, overlapping predictions.
## Part 6: Backtests: baselines (Pathia, EMA-trend, existing breakout) vs
range-location-only, deviation-rejection-only, acceptance-only, combined, Pathia+.
Across BTC/ETH/large-caps, trending/sideways, hi/lo vol, bull/bear. Walk-forward:
train N -> validate N, frozen thresholds, final untouched test period. Realistic
fees/slippage/funding/spread/delay/missing candles/HL history limits. Net-of-cost only.
## Part 7: Full metrics incl. MAE/MFE, per-regime/asset/location/confidence, and:
expectancy-vs-trade-count, drawdown effect, good-trade removal, higher-cost survival,
untouched-asset generalization, long+short, one-cycle dependence.
## Part 8: Fixed grading rules (strict/direction/target/timing/risk-adjusted separately);
adverse-first target hits flagged; dual-path calls not directional wins w/o stated primary.
## Part 9: Outcome A reject / B shadow-only fields / C experimental disabled flag
{"range_structure": {"enabled": false, "shadow_mode": true}} / D production candidate
(OOS, net-of-cost, multi-period, multi-asset, multi-regime, tested, default-off).
No auto-enable ever.
## Part 10 outputs: research/killa_xbt/{README.md, calls.json, methodology.json,
validation_results.json, validation_report.md} + tests. Report: conclusion,
limitations, counts, grades, reconstruction, evidence, features, design, leakage
protections, net results, ablations, failures, recommendation, files, repro commands.
## Part 11: no live config changes, no gate weakening, no test removal, no fabricated
tweets/prices, sources on every record, screenshots unverified w/o original, research
separate from prod, deterministic, type hints, docstrings, boundary tests, fail-safe
on short history.
## Part 12 starting evidence (verify independently): Dec 11 2025 BTC redistribution
call x.com/KillaXBT/status/1998834473975063001 (redistribution range, time-based
capitulation, mid-$60ks path; BTC later ~$62-65k — grade path/timing separately).
Attributed May 2025 long-cycle chart (~$120ks top then $50-60k decline, ~300d path;
BTC reached ~$125k then declined) — UNVERIFIED until provenance found.
If X access is limited: proceed with recoverable evidence, document the limitation,
never manufacture a win rate.
