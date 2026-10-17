# services/thesis

The one latent step in the desk pipeline. One entry point:

```python
from services.thesis import write_thesis
thesis = write_thesis(spec, analytics, intel, prev)  # -> Thesis, never raises
```

Flow: build a dense-text prompt (prompt.py) -> ask services/llm for
schema-constrained JSON (llm_schema.ThesisDraft - the latent fields only:
call, scenarios, bull, bear, what_changes_my_mind, lessons,
leverage_reasons) -> validate it (validator.py, the anti-hallucination
gate) -> on failure, one repair round, resuming the first call's session so
the repair message stays short -> on a second failure, degrade to `prev`
(annotated with why) or a flat zero-confidence fallback if there is no
`prev` (fallback.py) -> on success, assemble the full contract Thesis
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

158 unit/e2e tests, all offline (the LLM is faked throughout - see
test_api_e2e.py's `_FakeLlm`). Covers: prompt building and budget trimming
(including the incremental-mode diff, measured ~52% smaller than a full
rewrite on the oil fixture); every validator rule with its adversarial
case (invented number, wrong-side stop, probabilities summing to 0.9,
overlapping buckets, uncited source, avoid-rung leverage, plus the extras
above); both renderers; deterministic assembly and the two fallback paths;
and the full write_thesis orchestration against a scripted fake LLM
(happy path, resumed repair, resume-failure fallback, reject-with-prev,
flat fallback, malformed JSON, and a "never raises no matter how long the
LLM misbehaves" check).

Eval (paid, real CLI calls - not part of the free suite above):

```sh
PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m services.thesis.evals.run_eval
```
