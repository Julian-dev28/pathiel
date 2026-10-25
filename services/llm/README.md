# services/llm

The project's one LLM gateway. Shells out to the local Claude Code CLI
(`/Users/julian_dev/.local/bin/claude -p ...`). Hosted LLM APIs (Anthropic
API, OpenAI, any hosted inference endpoint) are banned by project rule
unless Julien explicitly instructs otherwise - this is meant to be the only
sanctioned path from the rest of the codebase to an LLM.

```python
from services.llm import complete

result = complete(
    system="You are a senior desk trader.",
    user="WTI mid 99.34, ATR 2.8%...",
    max_output_tokens=1800,
)
result.text          # the model's reply (or structured JSON if json_schema was passed)
result.model         # canonical model id actually billed, e.g. "claude-fable-5-1"
result.input_tokens, result.output_tokens, result.cost_usd, result.duration_s
```

Failures raise `services.llm.errors.LlmError` (or a subclass:
`LlmBinaryNotFoundError`, `LlmTimeoutError`, `LlmProcessError`,
`LlmBadOutputError`) instead of returning a sentinel. Every call, success or
failure, is appended to `$PATHIEL_STATE_DIR/llm/usage.jsonl`.

## Live probe log (2026-10-17)

Before wiring a default model in, SWARM_RULES.md section 3 requires verifying
the id with a one-line ping. Ran `claude -p "Reply with exactly: OK"
--output-format json --max-turns 1 --tools "" --safe-mode
--no-session-persistence --model <alias>` for each alias:

| alias | canonical id | cost (trivial ping) | max output tokens |
|---|---|---|---|
| `fable` | `claude-fable-5-1` | $0.0773 | 64,000 |
| `opus` | `claude-opus-5-5` | $0.0161 | 128,000 |
| `sonnet` | `claude-sonnet-5-5` | $0.0081 | 128,000 |
| `haiku` | `claude-haiku-5-5` | $0.0006 | 128,000 |

All four aliases are live and resolve to exactly the candidates named in the
overhaul brief (`claude-fable-5-1`, `claude-opus-5-5`). `MODEL_PROBE_CANDIDATES`
in `config.py` is `("fable", "opus", "sonnet")`, best first - `model_probe.py`
pings down that list and caches the first one that answers, so the default
always resolves to the best model actually up right now, not a hardcoded
guess. Aliases are used (not pinned canonical ids) so the default keeps
tracking each tier's latest point release without a code change.

**`--system-prompt` cuts cost ~4.6x.** `pathiel/agents/ai_brain.py`'s
`ClaudeCliBrain` concatenates system+user into one stdin blob and never
passes `--system-prompt`, which means every call also pays to cache Claude
Code's own (much longer) default system prompt. Same ping, with
`--system-prompt "You are a terse trading desk assistant."` instead of
concatenation:

```
cache_creation_input_tokens: 3846 -> 830
cost:                        $0.0773 -> $0.0168
```

services/llm always uses `--system-prompt`. This is a real, measured,
"best results per token spent" win and the single most actionable finding
for whoever next touches `ai_brain.py` (see "Should ai_brain migrate" below)
- it is a one-line change with no behavior risk, independent of any broader
migration decision.

**`--json-schema` works, including nested `$defs`/`$ref`.** Tested a flat
schema and a schema with a `$defs`/`$ref`-nested array of objects; both
calls returned exactly-shaped JSON with no markdown fences or extra prose.
services/thesis relies on this for its latent-field draft instead of
scraping free text for a trailing `{...}`.

**`--fallback-model`, `--max-budget-usd`, `--system-prompt` and
`--json-schema` all combine cleanly** in one call (verified together with
`--model haiku --fallback-model sonnet --max-budget-usd 0.50`).

**No max-output-tokens flag exists.** Checked `claude -p --help` in full:
there is no token-count cap, only `--max-budget-usd` (dollars) and
`--max-turns` (turns). `max_output_tokens` is therefore enforced indirectly:
`--json-schema` bounds the shape (and so, in practice, the length) of the
response; `--max-budget-usd` is derived from `max_output_tokens` via a
conservative per-model rate table as a dollar backstop; the caller is
expected to say the budget in its own system prompt text; and
`LlmResult.output_tokens` is always the real post-hoc count, to react to,
not proof a ceiling was enforced. This is documented, not silently assumed
away - see `api.py`'s module docstring.

**`-p` sessions persist and `--resume` recalls them without resending
context.** Ran a two-call probe: call 1 (`haiku`, no `--no-session-persistence`)
told it "my favorite number is 7"; call 2 (`--resume <session_id from call 1>`)
asked "what is my favorite number?" with no mention of 7. It answered `7`,
and `usage.input_tokens` on call 2 was `2` - the fact was never re-sent, the
session carried it. `complete()` exposes this as `persist_session=True` (keep
the session instead of the default `--no-session-persistence`) and
`resume_session=<LlmResult.session_id>` (resume it for one more, final turn -
`--no-session-persistence` is added back automatically so the session is
cleaned up once resumed). services/thesis uses this for its repair round: the
first call persists its session, and if the validator rejects it, the repair
call resumes that session with just the error list instead of re-sending the
full analytics/intel render.

Total real-CLI spend across every probe above (model aliases, flat and
nested `--json-schema`, the combined-flags check, the resume check): **10
calls, about $0.34**, all logged in this file so nobody has to re-derive it
from `usage.jsonl`.

**`usage.input_tokens` alone undercounts the real prompt size.** Found
2026-10-17 running services/thesis's real eval: a ~700-token prompt
reported `usage.input_tokens == 2`, because Claude Code bills the
system prompt and most of the user message under
`cache_creation_input_tokens` (first use) or `cache_read_input_tokens`
(repeat/resume), leaving `input_tokens` as just the small uncached
remainder. `LlmResult.input_tokens` is now the sum of all three fields -
the honest total prompt size, and the right number to compare against a
token budget. `total_cost_usd` was never affected by this; only the token
*count* was mislabeled.

## Config

| var | default | notes |
|---|---|---|
| `CLAUDE_CLI_COMMAND` | `/Users/julian_dev/.local/bin/claude` | shared with `ai_brain.py`'s own override env var |
| `PATHIEL_LLM_MODEL` | unset | pins the model and skips probing entirely |
| `PATHIEL_LLM_CONCURRENCY` | `4` | max concurrent CLI subprocesses (a `threading.BoundedSemaphore`) |
| `PATHIEL_STATE_DIR` | `.` | usage log and model-probe cache live under `<dir>/llm/` |

## Should `ai_brain.py` migrate onto this gateway?

**Not as a blind swap. Yes for the subprocess internals; no for the
verdict-specific behavior, at least not without a decision from Julien.**

`ai_brain.py` was deeply embedded in the live trading loop
(`trading_loop.py -> research._call_ai() -> ai_brain -> parse_verdict()
-> executor.route_verdict()`; the module and that doc are deleted as of
2026-10-25, this gateway is the only LLM path left), had three
providers (`openrouter`, `claude_cli`, `codex_cli`) where this gateway only
covers one, and its `claude_cli` path has its own tuned behavior
(`_contains_parseable_verdict_json`'s trailing-`{...}` scrape, the
`web_search` aux-model-pin workaround, hot-reloadable per-provider config via
`read_agent_config()`). `ai_brain.py` is owned by the "cleanup" agent in
SWARM_RULES.md's ownership table, not this one - the following is analysis to
hand off, not a change made here.

Concrete, low-risk first step for whoever owns that file next: point
`ai_brain._run_cli` and the argv-building inside `ClaudeCliBrain.complete` /
`CodexCliBrain.complete` at `services.llm.cli.run_cli` /
`services.llm.cli.build_args` directly, keeping `ai_brain`'s own
envelope-to-verdict contract (`AiBrainResult`, `_validated_cli_result`,
`_contains_parseable_verdict_json`) exactly as it is today. That removes the
duplicated, bug-prone part (process-group kill, the 240s timeout tuning, the
env pinning) without touching verdict semantics or the live trading path's
behavior. Do **not** migrate the `openrouter` provider onto this gateway -
it is a hosted API, out of scope by design, and the fact that it remains
`DEFAULT_AI_BRAIN_PROVIDER` today predates the project's "no hosted LLM API"
rule. That is a policy question for Julien, not something to silently change
here.

A full migration (ai_brain's `claude_cli` backend literally calling
`services.llm.complete()`) is a bigger step and should wait for: (1)
confirming that switching the live verdict path onto `--json-schema` doesn't
change its failure characteristics (today it free-texts a trailing verdict
JSON blob; schema-constrained output would likely be *more* reliable, but
that is a behavior change to a live-money path, which is exactly the kind of
decision SWARM_RULES.md's Confusion Protocol reserves for a human, not an
agent); and (2) the `--system-prompt` cost win above, which is safe to take
immediately and independently of everything else in this section.

## Tests

```sh
PATHIEL_STATE_DIR="$(mktemp -d)" .venv/bin/python -m pytest services/llm/tests -q
```

48 unit tests, all offline (subprocess is faked throughout - see
`cli.py`'s module docstring for why run_cli raises typed exceptions instead
of returning `""`). Covers: the CLI wrapper against a fake subprocess
(success, every failure mode, usage parsing, argv shape for every optional
knob); the model prober (env override, first-success-wins, fallthrough,
on-disk caching with a TTL, corrupt-cache resilience); the usage log
(append shape, ordering, write-failure resilience); and `complete()` itself
(model resolution, web-search tool/turn/env wiring, json-schema pass-through,
timeout clamping, failure logging, the concurrency bound under real
threads, and the pure helpers `_budget_for` / `_fallback_chain` /
`_to_result`).
