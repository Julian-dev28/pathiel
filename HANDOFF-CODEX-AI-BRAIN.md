# Handoff — Pluggable AI brain → autonomous CLI/MCP operator (for Codex)

**Last updated:** 2026-06-26 by Codex after the EV+ cleanup and live wiring
audit.

**Task:** make the bot's decision-maker pluggable so the TA + signals we currently
POST to OpenRouter can instead be *ingested and verdicted* by an agentic CLI
(Claude Code / Codex) — and ultimately so that agent holds **full verdict authority
and trade autonomy**, either behind the existing call seam or as the MCP-driven
engine. Keep OpenRouter as a first-class, selectable provider. Keep the code clean
(no forked pipelines). This is a live, real-money Hyperliquid perps bot — reversible,
gated, no churn.

---

## TL;DR
- The system has **two clean seams**: a **brain seam** (`_call_ai`) that turns a
  prompt into a verdict, and an **action seam** (`route_verdict`) that turns a
  verdict into a trade. Everything below plugs into one of these. Do not invent a
  third path.
- **Recommended route to the north star:** make the CLI agent a *provider* behind
  the brain seam, running in **agentic mode** (Section 1). The agent becomes the
  brain (full verdict authority); the existing action seam + risk gates + DSL exit
  engine give it safe trade autonomy and the ability to CLOSE — with almost no new
  surface area and **no second executor**.
- **MCP-as-engine** (Section 4) is the alternative when you want the agent to also
  own *orchestration* (which coins, when). It already routes execute through the
  gates, but as built it gives orchestration authority, **not** verdict authority
  (it executes the bot's stored verdict) — and its close path is duplicated logic
  that must be consolidated. Bigger lift; do it after the brain seam works.
- Current live state: `mode=LIVE`, `enable_crypto=true`, `enable_hip3=true`;
  EV+ live books are `xs_momentum`, `extreme_fade`, and `rally_exhaustion`.
  `hail_mary_short` is wired in shadow-only as an AI/semis HIP-3 short-basket
  research book, not promoted live alpha. `gex_signal` is a HIP-3 guardrail/veto,
  not standalone alpha. Removed EV-, refuted, and whale/signal-suite paths should
  stay gone.

---

## North star (the actual goal — keep in view)
Full **verdict authority AND trade autonomy living inside the CLI agent or MCP
server**: the agent ingests TA+signals → forms the verdict → it gets sized →
executed → managed/closed, end to end, on its own. OpenRouter and the
current HTTP completion path are **fallbacks/providers**, not the destination.
The only non-negotiable is that the **risk gates and kill-switch remain
non-bypassable** — autonomy is about *who decides and acts*, never about removing
the guardrails.

---

## 0. The two seams (load-bearing architecture — read this first)

> **Line numbers are hints, not contracts** — they drift as code is edited (this
> file was re-anchored 2026-06-26 after the EV+ cleanup and strategy-book wiring
> fixes). Always `grep` the function name to confirm before relying on a number.

### Seam A — the brain: prompt → verdict text
- `research.py:293` — `_call_ai(system_prompt, user_message) -> str`. Posts to
  OpenRouter (via `_async_do_call`, `:338`), returns raw model text. **This is the
  only place a brain lives.**
- `research.py:156` — `_build_user_message(...)` already assembles **all** live
  context as plain text: multi-TF EMA/RSI/ATR/ADX, 1h structure, recent candles,
  funding, GEX guardrail context, news, equity/positions, and dex equity. Removed
  whale/free-signal blocks are gone. `system_prompt.py:6` —
  `build_system_prompt(mode, win_rate, recent_trades)`. **Reuse both verbatim;
  never rebuild the prompt.**
- `research.py:386` — `parse_verdict(ai_text, coin, perception)` consumes the text.
  It expects the verdict as **JSON on the last line** (the system prompt emits the
  contract), with a regex fallback:
  `{"verdict":"PASS|LONG|SHORT|CLOSE","confidence":0-1,"side":...,"entryPx":...,"stopPx":...,"tpPx":...,"reasoning":...}`

**Any function returning a string that ends in that JSON is a drop-in brain.** That
is the entire integration surface for Options 1–2. Do not widen it.

### Seam B — the action: verdict → trade (already provider-agnostic)
- `executor.py:1132` — `route_verdict(analysis, *, execute_fn=None, close_fn=None)`.
  Pure routing, side-effects injected (so it's unit-testable):
  - `LONG`/`SHORT` → `maybe_execute(analysis)` (`execute_fn`, default inside `route_verdict`)
  - `CLOSE` → `close_position_market(coin)` (`close_fn` default; body at
    `executor.py:1370`)
  - `PASS` → no-op unless it carries the **narrow TA-sidestep hint**; then the
    executor owns the actual upgrade decision. Broad force/whale overrides are
    removed. `ai_down` blocks even this narrow sidestep.
- `maybe_execute` (`executor.py:218`) is the full gate stack: builds the gate context
  → `eval_all_gates` (`risk_gates.py:364`) → blocks → `place_hl_order` only after
  every gate clears.
  Confidence, runner-gate (incl. the 2026-06-21 `late_chase_relax` admit + the
  live `capital_rotation` evict-for-room hook), trend filter, loss-cooldown,
  degraded-read guard, concurrency, notional caps, margin floor (+ the pre-research
  margin preflight `_rotation_preflight_eval` in `trading_loop.py:256`),
  kill-switch-entry-block all fire here. **A new brain's verdict flows through ALL of
  these unchanged — autonomy never widens past the gate stack.**
- `executor.py:1110` — `monitor_exits(mids)` is the DSL trailing-stop / SL-TP exit
  engine, run by the loop every cycle (and still in `mode: OFF`, `trading_loop.py:679`
  under the `trading_loop.py:641` OFF-mode guard).

The loop just wires A→B sequentially, per coin: `analysis = research(coin, perception)`
then `route_verdict(analysis)` (`trading_loop.py:878`; imports at `:59–61`).

### Invariants every brain MUST preserve
1. **`ai_down` on failure.** `parse_verdict` sets `ai_down = not ai_text.strip()`
   (`research.py:483`). Empty text = "the brain failed" → forces PASS **and** tags it
   so the override won't upgrade a failure-PASS into a blind LONG (this caused an
   8-trade shotgun on 2026-06-11). **Every provider maps failure — non-zero exit,
   timeout, empty stdout, unparseable — to an empty string.** Never fabricate a PASS
   JSON on failure; return `""` and let the existing logic mark `ai_down`.
2. **Preserve the 402 degraded-retry** when refactoring OpenRouter. The current code
  (`research.py`, inside `_call_ai`) catches a 402 with an affordability hint and retries once
   at a smaller `max_tokens` (kept the bot alive during a credit drought). This logic
   moves *with* the OpenRouter provider — don't drop it on the floor.
3. **Verdict contract is shared.** All providers feed `parse_verdict` unchanged. They
   change *transport* (subprocess vs HTTP), never the prompt format or the parse.

---

## 1. Recommended route to the north star: CLI agent as a brain provider (agentic)

This is the cleanest way to get **full verdict authority + trade autonomy** with the
least new surface:

- The CLI agent sits behind Seam A as a provider. Its output (the verdict JSON) IS
  the analysis → it flows through Seam B → gates → trade. **That is full verdict
  authority, for free** — because the agent literally is the brain that produces the
  stored verdict.
- **Trade autonomy** comes from the existing action seam: LONG/SHORT auto-execute
  through `maybe_execute` (gated); CLOSE auto-closes through `close_position_market`.
- **CLOSE ability** is already wired: the loop re-researches held coins on an interval
  for a possible AI CLOSE, so a CLI brain emitting `CLOSE` on a held coin closes it
  through the proven path (this satisfies the standing "AI closes are required"
  requirement — DSL alone isn't enough).
- **Agentic mode** is the real upgrade over OpenRouter: give the agent read-only
  tools (its own MCP client against the read-only tools in Section 4, or filesystem
  access to candle caches) so it can *pull more data before verdicting* — the thing a
  flat completion cannot do. Keep tools read-only; the verdict is its only output.

The loop stays as orchestrator (which coins to research, throttles, exit engine,
risk heartbeat). The agent owns the decision. One brain, one executor, no spaghetti.

Current live loop also calls the EV+ strategy books before discretionary AI
research: `xs_momentum`, `extreme_fade`, and `rally_exhaustion` (see
`trading_loop.py`). It also calls `hail_mary_short` in `shadow_only=true` mode
to log crash-basket regime/fresh-breakdown evidence without allocating capital.
Those books route through `maybe_execute` when live and should remain
independent of the pluggable research brain.

---

## 2. The provider abstraction (the refactor — do this first, behavior-identical)

Turn the brain into a small strategy chosen by config/env. Suggested shape (adapt to
the codebase's conventions):

- New module `hermes_trader/agents/ai_brain.py` with a trivial contract:
  `complete(system_prompt: str, user_message: str) -> str` (returns `""` on any
  failure). Implementations: `OpenRouterBrain` (move the current
  `_async_do_call`/402 logic here verbatim), `ClaudeCliBrain`, `CodexCliBrain`.
- `_call_ai` (`research.py:322`) becomes a 3-line dispatcher: read provider →
  `get_brain(provider).complete(system, user)`. Nothing else in `research.py`,
  `executor.py`, `route_verdict`, or the loop changes.
- Selector: `AI_BRAIN_PROVIDER` env **or** `config["ai_brain"]["provider"]` ∈
  `{openrouter, claude_cli, codex_cli}`, default `openrouter`. Make it **hot-read**
  (read per call, like the rest of config) so the operator can switch or revert
  instantly **without a restart**.
- Log the chosen provider on each verdict / in the heartbeat so the operator can see
  which brain produced a decision.

**First commit = the refactor alone, `openrouter` default, behavior byte-identical
(402 path included). Verify no behavior change before adding any CLI provider.**

---

## 3. The CLI brains — exact invocations

> Flags evolve between CLI versions. Treat the below as canonical-but-verify: run
> `claude --help` / `codex exec --help` in the loop's actual environment and confirm
> before relying on them.

### `claude_cli` — Claude Code print/headless mode
```bash
# prompt on stdin (preferred for large prompts), JSON envelope out
printf '%s' "$SYSTEM_PROMPT

$USER_MESSAGE" | claude -p \
  --output-format json \
  --max-turns 1            # cheap mode: single pass, glorified completion
# (agentic mode: raise --max-turns and grant read-only tools so it can fetch data)
```
- `--output-format json` returns an envelope: `{"result": "...", "is_error": bool,
  "session_id": ..., "total_cost_usd": ...}`. **JSON-parse the envelope first**, check
  `is_error`, then pass `.result` to `parse_verdict`. (`--output-format text` returns
  raw stdout if you'd rather skip the envelope.)
- System prompt: simplest is to concatenate into the prompt (as above), matching how
  OpenRouter sends system+user. Or use `--append-system-prompt`.
- Restrict tools in cheap mode (`--max-turns 1` and/or `--disallowedTools`) so it
  cannot mutate the repo or place orders — **the executor trades, the brain only
  verdicts.** In agentic mode, allow only read-only tools.

### `codex_cli` — Codex non-interactive exec
```bash
printf '%s' "$SYSTEM_PROMPT

$USER_MESSAGE" | codex exec -   # final message → stdout
# add --json if your build emits structured events; then extract the final message
```
- `codex exec` runs headless and prints the final message to stdout → feed stdout to
  `parse_verdict`. With `--json`, parse the event stream and take the final message.

### Mandatory wrapper for both (this is where correctness lives)
- **Hard timeout + kill** (≤120s, matching `research.py:374`). On timeout → kill the
  process group → return `""` (→ `ai_down`).
- **Failure mapping:** non-zero exit, empty stdout, `is_error`, or JSON-less output →
  `""`.
- **No tools by default** in cheap mode; read-only tools only in agentic mode.
- **Determinism caveat:** OpenRouter runs at `temperature: 0.1` (`research.py:373`);
  the CLIs don't expose temperature the same way, so verdicts will be less
  deterministic. Acceptable, but know it — don't chase a "flaky verdict" bug that's
  just sampling.

---

## 4. Option 3 — MCP server as the autonomous engine (bigger; after 1–3)

The MCP server (`scripts/hermes-mcp-server.py`, `tool_handlers` at `:1118`, 100 tools
= 52 real + 48 honest stubs) already exposes the whole pipeline: `handle_scan`
(`:882`), `handle_research` (`:963`), `handle_execute` (`:1007`),
`handle_close_position` (`:1257`), `state`, `market_*`, `whale_index`, etc. An external
Claude Code / Codex session can be the outer loop: `scan` → `research` (TA+signals
return as tool results) → decide → `execute`/`close_position`.

**Verified safe on entries:** `handle_execute` (`:1007`) → `maybe_execute`
→ the full gate stack (Seam B), after pre-filtering to LONG/SHORT. **No order
reaches the exchange without the gates.**

### Two things to fix/understand before trusting MCP-as-engine

1. **Verdict authority gap.** `handle_execute` executes the analysis **by stored id**
   — i.e. the verdict produced by `research()`/`_call_ai`, not an
   agent-supplied one. So as built, MCP gives the agent **orchestration** authority
   (which coins, whether to execute the bot's verdict), **not its own verdict
   authority.** To close this for the north star, either:
   - (a) run the Section-1 CLI brain so `research()` already reflects the agent's
     judgment (cleanest — the two options compose), **or**
   - (b) add a thin tool that lets the agent submit its own verdict, which becomes the
     stored analysis, then `execute` it through the same gated path. Keep it a thin
     adapter; do not duplicate sizing/gates.

2. **`close_position` is duplicated logic — consolidate it.** `handle_close_position`
   (`:1257`) **re-implements close inline** via `place_hl_order`, bypassing
   `close_position_market` (`executor.py:1340`). That means MCP closes skip reduce-only
   handling, loss-cooldown arming, and DSL trigger cleanup — a correctness divergence
   **and** exactly the spaghetti to avoid. **Fix: make `handle_close_position` delegate
   to `close_position_market`**, mirroring how `handle_execute` delegates to
   `maybe_execute`. (This is a worthwhile cleanup even if you never ship Option 3.)

### Division of labor (the elegant north-star topology)
Run the agent as the entry/decision brain via MCP, and keep the **loop in `mode: OFF`
as a pure risk + exit heartbeat** (the `trading_loop.py:725` OFF-mode guard still runs
`monitor_exits` (`:679`) and the kill-switch with scan/research/execute off). Result:
- Agent owns: which coins, the verdict, entry execution, and discretionary CLOSE.
- Loop owns: the proven DSL trailing-exit engine + flatten-on-breach kill-switch.

The agent doesn't reimplement exits (don't — the DSL engine is sophisticated); it can
still CLOSE discretionarily via the (consolidated) `close_position` tool. The one
coordination point: the DSL engine and the agent can both close the same position —
with `close_position` consolidated onto `close_position_market` (reduce-only +
idempotent-ish), a double-close is safe, but make CLOSE reduce-only and tolerate
"already closed." This is the "AI closes required" intent realized without a
double-executor race on *entries*.

**Flatten-on-breach is the operator's call, not a hard requirement.** It lives in the
loop heartbeat, not in `maybe_execute` — `eval_all_gates` only *blocks new entries* on
breach. If you keep the `mode: OFF` loop, you keep flatten-on-breach for free; if you
fully stop the loop, entries are still gate-blocked on breach but open positions
won't be auto-flattened. Flagging it so it's a conscious choice — your call whether it
matters.

---

## 5. Keep it clean (anti-spaghetti — this is what "no collisions" means here)

The risk isn't runtime races so much as **forking the pipeline into tangled
half-wired paths.** Hold the line:
- **One seam per concern, one provider interface.** Every brain implements
  `complete(system, user) -> str` behind `_call_ai`. No brain gets a special-case
  branch in the loop, `executor.py`, or `route_verdict`. If you're editing the
  executor to add a *provider*, you're in the wrong place.
- **Don't duplicate the prompt or the parse.** `_build_user_message` and
  `parse_verdict` are shared by all providers, verbatim.
- **MCP handlers are thin adapters over existing functions** — like `handle_execute`
  → `maybe_execute`. Never grow a second copy of gate/sizing/close logic in the MCP
  server. (The current `handle_close_position` violates this — fix it, per §4.)
- **Provider selection is data, not scattered branches.** One lookup → one strategy.
  A 4th brain later = one new function + one registry entry, nothing else touched.
- **Delete dead paths; don't accrete.** Two live ways to do one thing is the
  spaghetti to avoid.

### The one genuine runtime rule (separate from tidiness)
Only **one process** places orders and writes the live state files
(`.agent-memory.json`, `.dsl-state.json`, `.positions-snapshot.json`) at a time. Two
things calling execute on one account double-trade/pyramid; two writers corrupt memory
(it has wiped live memory before). Options 1–2 are single-executor by construction
(the loop). For Option 3 the executor **moves** into the agent (loop in `mode: OFF`) —
you're relocating the one executor, not adding a second. Still one brain, one
executor, one writer.

---

## 6. Cost / latency / concurrency reality (don't skip)

The loop researches candidates **sequentially**, per coin. A CLI agent call is far
slower and pricier than one OpenRouter HTTP call (cold start + inference, possibly
10–30s each vs ~2–5s). At N candidates/cycle that's N× added latency on the critical
path — and because `monitor_exits` runs in the same cycle, a slow research phase
**delays exit checks** (worse stop slippage). Mitigations, in order of cleanliness:
- **Cap candidates/cycle** for the CLI brain.
- **Hybrid triage** (clean if structured as one provider): OpenRouter does first-pass
  triage, the CLI agent adjudicates only the top-k survivors. Implement as a single
  `CompositeBrain` provider behind the seam — not as scattered branches.
- **Restrict the CLI brain to high-composite candidates**, OpenRouter handles the bulk.
- If you parallelize subprocesses, you must make `memory.record_analysis` writes
  concurrency-safe (it persists per analysis) — prefer staying sequential or add a
  lock rather than risk the memory-corruption class of bug.

---

## 7. Build order (each step shippable + reversible)
1. **Refactor `_call_ai` → provider dispatch**, `openrouter` default, behavior-identical
   (402 path preserved). Land alone; verify no change.
2. **Add `claude_cli` + `codex_cli` providers** (cheap mode) with timeout/kill,
   failure→`""`, provider logging. Unit-test the `ai_down` path. Switch via hot-read
   config. → Verdict authority for the CLI agent, gates unchanged.
3. **Agentic mode**: grant the CLI brain read-only tools so it can pull data before
   verdicting. → North star via Seam A.
4. **(Optional) Option 3 / MCP engine**: consolidate `handle_close_position` onto
   `close_position_market`; add verdict-injection if you want MCP-native verdict
   authority; run loop in `mode: OFF`. Shadow first (agent advises, no `execute`),
   then hand over `execute`.

## 8. Testing & acceptance
- `AI_BRAIN_PROVIDER=openrouter` reproduces today's behavior, **including the 402
  degraded retry** (add a test if one doesn't exist).
- CLI providers: a forced failure (bad binary / induced timeout) → `""` → `ai_down=True`
  → PASS, and the override does **not** upgrade it (extend the existing `parse_verdict`
  tests).
- Golden-prompt test: feed a known prompt to each provider, assert the returned text
  is parseable by `parse_verdict` into the expected verdict shape.
- If you touch `handle_close_position`: test that an MCP close goes through
  `close_position_market` (arms loss-cooldown, cleans DSL triggers, reduce-only).
- **Do not run the full pytest suite against the live tree while the loop trades**
  (it has wiped live `.agent-memory.json`; `tests/conftest.py` redirects state to temp,
  but be careful).

## 9. Environment, run & gotchas
- Repo `/Users/julian_dev/Documents/code/hermes-trader`, branch `able`; release by
  fast-forwarding `able` → `main`.
- Python venv `.venv/bin/python`. Loop `scripts/trading_loop.py`; dashboard
  `hermes_trader.server` :8000; restart via `scripts/restart.sh`.
- `.agent-config.json` (repo root) is **hot-read per-trade** (config changes are
  instant); **code changes need a restart**. Make the provider selector config/env so
  switching/reverting is hot.
- **CLI auth in the daemon's environment.** The loop runs as a daemon; the `claude`
  and `codex` CLIs use their *own* auth (logged-in session / their own API key), which
  may not be present in the daemon's env/user. **Verify `claude -p` / `codex exec` run
  non-interactively in the loop's actual environment before depending on them.**
- Secrets in gitignored `.env.local` (`OPENROUTER_API_KEY`, etc.). Never commit.
- `OPENROUTER_MODEL` (default `x-ai/grok-4.3`, `research.py:325`) still selects the
  OpenRouter model. Note OpenRouter can already route to Claude *models*
  (`anthropic/claude-...`) for a flat completion with zero code — the only reason to
  build the CLI brains is tool-use + iteration (agentic mode).

## 10. Guardrails (do not violate)
- Real money. Risk gates (`eval_all_gates`, caps, kill-switch entry-block) stay
  non-bypassable on every path. Flatten-on-breach is the operator's call (§4) — keep
  it if you keep the heartbeat; don't silently drop it.
- `ai_down` on every provider failure (§0). No fabricated PASS JSON — return `""`.
- One executor, one state-file writer (it may **move** into the agent, but stays one).
- Keep it clean: one seam per concern, one provider interface, thin MCP adapters, no
  forked pipelines (§5).
- Reversible only; provider selectable via hot-read config/env. Don't widen the verdict
  contract or touch the gate/executor internals unless the task truly requires it.
