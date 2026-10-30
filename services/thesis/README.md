# services/thesis

The one latent step in the desk pipeline. One entry point:

```python
from services.thesis import write_thesis
thesis = write_thesis(spec, analytics, intel, prev)  # -> Thesis, never raises
```

Flow: build a dense-text prompt (prompt.py) -> ask services/llm for
schema-constrained JSON (llm_schema.ThesisDraft - the latent fields only:
call, scenarios, bull, bear, what_changes_my_mind, lessons,
leverage_reasons; the call itself is retried once if the CLI exits non-zero
or returns a bad envelope, never on a timeout: 2026-10-25, one such one-off
re-served a stale call for four hours) -> validate it (validator.py, the
anti-hallucination
gate) -> on failure, one repair round, resuming the first call's session so
the repair message stays short -> on a second failure, degrade to `prev`
(annotated with why) or a flat zero-confidence fallback if there is no
`prev` (fallback.py; services/strategies/pipeline.py then keeps the prior
file untouched and logs the refresh as `degraded`, not `written`; both
rejected drafts and their errors are saved to
`$PATHIEL_STATE_DIR/thesis/rejected/<desk>-<utc>.json`, since a warning on
the MCP server's stderr is lost) -> on
success, assemble the full contract Thesis
deterministically (assemble.py: ids, timestamps, catalysts, leverage
verdicts, resolved sources, payload hashes, usage - never from the model).

`render.py` renders the result: `render_markdown(thesis, analytics,
mode="quick"|"full")`.

## The validator (validator.py)

Every rule is deterministic - same inputs, same verdict, every time. Full
inventory and the exact edge cases each one is for are documented in the
module's own docstring. The one worth knowing before touching it:
`build_ground_truth_pool` decides what counts as a "real" number a thesis
is allowed to cite - get the scaling wrong (as happened twice during
development, see below) and it either lets an invented number through or
rejects a correct one.

## Structure, the R gate, and the ladder-stop limit (2026-10-23)

Root cause fixed this date: the live Brent desk's thesis read "Buy dips to
105.00-107.44 at 5x. Target 110.31 then 112.19. Stop 95.87." - a stop 10%
below entry (copied straight off the leverage ladder's liquidation-derived
`stop_px`, 60% of the distance to liquidation) with a best target paying
0.47R. Sizing correctly refused the call; nothing upstream would have
caught it before sizing did.

Three pieces, in the order the data flows:

1. **`contracts.strategies.models.InstrumentStructure`** (contract 1.1.0):
   per-primary-instrument price structure - the last closed 1h candle,
   8h/24h/48h range, ATR14 on 1h bars, and where mid sits in the 24h range.
   Built by `services/strategies/analytics.py::build_instrument_structure`
   from an injectable hourly candle fetcher (same `CandleFetcher` shape as
   the daily fetch; fully offline-testable), stored on
   `DeskAnalytics.structure`.
2. **The prompt (prompt.py)** renders a STRUCTURE block (full and
   incremental modes) and tells the model: the stop must sit just beyond a
   real structural edge, the LEVERAGE LADDER's stop for the chosen leverage
   is that leverage's LIMIT (never a target to widen toward), targets sit
   at the next structural level, and the call must pay at least 1.5R to its
   first target.
3. **The validator** adds `check_r_multiple` (>=1.5R via
   `pathiel.agents.bracket.check_levels` - the SAME function `place_bracket`
   runs, so the desk and the executor can never disagree about what counts
   as a tradable R) and `check_stop_within_ladder` (the call's stop may
   never sit wider, as a fraction of entry, than the matching ladder rung's
   own stop). Both feed the existing one-shot repair loop in api.py.

The quick-mode card (render.py) now shows "Pays X.XR to <first target>"
right after the leverage line, reusing `check_levels` - the same number the
dashboard's 20x line (`services/strategies/call_line.py`) and the validator
compute, never a fourth copy of the math.

## Realistic targets and calibrated scenarios (2026-10-30)

Root cause (operator, 2026-10-30): the live Brent desk's thesis called "Long
Brent 5x at 113.87, stop 111.29, targets 118.87 then 122.20" on a venue
whose all-time high was 117.58 - a price Brent has literally never traded
at on xyz:BRENTOIL. The stop (2.3% under entry) also sat inside one normal
day's range (median daily high-low 2.76% over the trailing 30 days), so on
this year's history the stop was hit before target 1 about 60% of the time.

Three pieces, same shape as the 2026-10-23 structure/R-gate fix above:

1. **New analytics fields** (contract 1.3.0, additive/Optional):
   `InstrumentStructure.all_time_high`/`all_time_low` (candle-derived, with
   the date each one happened), `.range_90d`, `.median_daily_range_30d_pct`
   - all built from the desk's existing DAILY candle fetch (no second
   network call), by `services/strategies/analytics.py::build_instrument_
   structure`'s new `daily=` kwarg. `ForwardStats.long_reach`/`short_reach`
   - the empirical 7-day max FAVOURABLE excursion quantiles, the mirror of
   the existing max ADVERSE excursion (MAE) fields, from the same forward-
   window loop.
2. **The prompt** (prompt.py) renders ALL-TIME HIGH/LOW, the 90d range, and
   the median daily range in STRUCTURE, and REACH quantiles in FORWARD, and
   tells the model: targets are the MOST LIKELY reachable price (first
   target at or inside the empirical p50 reach, never past the ladder's
   all-time high/low), the stop must clear the median daily range or drop a
   leverage rung, and scenario probabilities must be calibrated to the
   empirical return distribution (implied median inside p25-p75).
3. **The validator** adds `check_targets_reachable` (errors on a target
   beyond the venue's all-time high/low, or beyond the empirical p90 reach
   - a looser p75 bound for the first target specifically, since the
   prompt's own ask is p50) and `check_scenarios_calibrated` (errors when
   the scenario set's own implied median - where cumulative probability
   crosses 50% - falls outside the empirical p10-p90, looser than the
   prompt's p25-p75 ask). Both skip silently when the new fields are absent
   (old packets, most hand-built test fixtures), same convention as every
   other analytics-gated rule here.

## Real-eval findings (2026-10-17)

Two real runs against the live CLI (oil fixture + evals/btc_fixture.py),
~$2.86 total. First run: mean score 0.429 (FAIL), caught three real bugs:

1. **False "invented number"** on a WTI mid of 99.339: the grounding
   pool's fraction-to-percent (`x100`) scaling applied to EVERY numeric
   leaf, not just fraction-shaped ones, so `99.339 * 100 = 9933.9` entered
   the pool with a loose relative tolerance wide enough to "ground" a
   literally invented `9999`. Fixed: the `x100` variant is now gated to
   `|v| <= 1.5`.
2. **False "invented number"** on `positioning.change = -10403`: repair
   prose wrote "fell by 10403" with no minus sign (the verb already carries
   direction). Fixed: the ground-truth pool now also includes each
   negative leaf's absolute value.
3. **False "outside its range"** on a BTC scenario that wrote bare `120`
   meaning `$120k`: fixed with a narrow, scenario-specific safety net (the
   abbreviation is only accepted if `value * 1_000` or `value * 1_000_000`
   lands inside THAT scenario's own derived range - not a blanket
   "small numbers are fine" rule).

Second run after the fixes: **mean score 0.857 (PASS, threshold 0.85)**.
Both desks passed validator_pass, numbers_grounded, one_liner_length_ok,
scenario_count_ok, invalidation_present, and catalysts_in_horizon.

**Known remaining gap: `tokens_within_budget` still fails on both desks.**
Single calls ran 2800-4000 output tokens against an 1800-token budget even
after adding `maxLength`/reduced `maxItems` caps to the JSON schema
(llm_schema._PROSE_CAPS) and an explicit terseness instruction in the
system prompt - a real, partially-mitigated improvement (btc dropped
~3100 to ~2840), not a full fix. Next step for whoever picks this up:
check whether the probed model is spending "thinking" tokens inside
`output_tokens` (fable/opus may default to extended thinking that this
service never explicitly disabled), and/or push the prose caps tighter
still.

A separate, independently-fixed-and-unit-tested bug found while reviewing
this run's raw numbers: `services/llm`'s `LlmResult.input_tokens` was
reading only `usage.input_tokens`, which Claude Code leaves near-zero for
any call with a sizeable system prompt (the real cost lands in
`cache_creation_input_tokens` / `cache_read_input_tokens`). Fixed to sum
all three - see services/llm/README.md and services/llm/api.py's
`_to_result`. Not re-verified with a third real call (unit-tested against
the exact envelope shape observed live instead), per the brief's "a few
calls is fine."

## Tests

```sh
PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest services/thesis/tests -q
```

All offline (the LLM is faked throughout - see test_api_e2e.py's
`_FakeLlm`; run_eval.py's PURE scoring logic gets its own free,
deterministic coverage in test_run_eval.py, with write_thesis itself never
called there). Covers: prompt building and budget trimming (including the
incremental-mode diff, measured ~52% smaller than a full rewrite on the oil
fixture, and the STRUCTURE block in both modes); every validator rule with
its adversarial case (invented number, wrong-side stop, probabilities
summing to 0.9, overlapping buckets, uncited source, avoid-rung leverage,
under-1.5R call, stop wider than the ladder limit, plus the extras above);
both renderers (quick mode's "Pays X.XR" line included); deterministic
assembly and the two fallback paths; and the full write_thesis
orchestration against a scripted fake LLM (happy path, resumed repair,
resume-failure fallback, reject-with-prev, flat fallback, malformed JSON,
and a "never raises no matter how long the LLM misbehaves" check).

Eval (paid, real CLI calls - not part of the free suite above):

```sh
PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m services.thesis.evals.run_eval
```

Scores each case on a 9-point rubric (7 from the 2026-10-17 run above, plus
`r_gate_pass` and `stop_within_2_atr`, added 2026-10-23): `r_gate_pass` is
`check_r_multiple` + `check_stop_within_ladder` both clean;
`stop_within_2_atr` is a looser, convention-matching second opinion (the
call's stop distance divided by the primary instrument's daily
`InstrumentSnapshot.atr14_pct`, capped at `MAX_STOP_ATR_MULTIPLE = 2.0`).
A case's score is (checks passed) / 9; **the eval passes at mean score >=
`THRESHOLD = 0.85` across every case**, same threshold as before - the two
new checks raise the bar a real run has to clear, not a separate gate.
The report line per case now also prints the generated call's R multiple
and its stop distance in ATR (`[oil] call: 2.21R to first target, stop
0.84 ATR from entry`-shaped), so a FAIL is legible without reading the
raw Thesis.
