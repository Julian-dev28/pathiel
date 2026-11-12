# pathiel public plugin: design (2026-11-12, revision 2)

Path: `docs/overhaul/PUBLIC_PLUGIN_2026-11-12.md`. Base commit `2816df7`, branch `overhaul-2026-10-17`. Version 0.3.0 to 0.4.0.

Revision 2 resolves the plan review: 6 majors and 12 minors, none rejected. The changes are listed under "What the review changed".

## Goal

Make pathiel a free public Claude Code plugin that a stranger can install safely, and that earns a Hyperliquid builder fee on every order it routes for a user who approved it. Add listing-ready metadata for the free MCP directories. Six things ship together: builder fee, safe public defaults, testnet by default, a read-only tier with no key, portability, listing and disclosure.

The operator trades real money on this code today. His path must not change.

No performance claims anywhere in public copy. Audit, measured 2026-11-12: the oil thesis called direction right 32% of the time over 36 graded calls (Brier 0.81); 19 closed trades whose average result cannot be told apart from zero.

## Facts this plan rests on

Code (read at `2816df7`):

- Every order goes through four functions in `pathiel/client/exchange.py`: `place_hl_order` (SDK `order`), `place_hl_trigger_order`, `place_hl_limit_order`, `place_hl_bracket_order` (SDK `bulk_orders`). Callers: `pathiel/agents/bracket.py` and the dashboard order routes. `scripts/treasury.py` builds its own Exchange with a hardcoded mainnet URL.
- `pathiel/agents/bracket.py:302-309`: a market entry fills first, then the stop and target are placed as two trigger orders. A failed stop returns "FILLED WITHOUT A STOP". So a trigger order must never fail because of builder state.
- `pathiel/client/hl_client.py:113-126`: the shared session retries POSTs twice in the adapter and once more in `wrap_session_retry`. A 3 s read through it can take about 18 s. The builder read must not use it.
- The venue URL is one constant, `HL_API` at `hl_client.py:40`. Other hardcoded URLs: `services/intel/sources/venue.py`, `scripts/oil_levels.py`, `scripts/treasury.py`, three research files.
- The operator launches through `.mcp.json` (`.venv/bin/python scripts/pathiel-mcp-server.py`, no env block). The plugin launches through `scripts/pathiel-mcp`, which sources `scripts/_plugin_env.sh`. That difference is the only reliable signal of who is running. A stranger who clones the repo and starts the server file directly looks exactly like the operator and gets the tracked config (mode LIVE, 20x, whole-equity sizing).
- `scripts/_state_env.py` loads the first existing of `$PATHIEL_ENV_FILE`, `<root>/.env.local`, `~/.pathiel/.env.local`. A missing `PATHIEL_ENV_FILE` in a test subprocess falls back to the checkout's live file. The repo `.env.local` sets `PATHIEL_STATE_DIR`.
- The MCP server accepts 19 hidden aliases through `ALIASES`; they are not in `TOOLS`.
- Tool descriptions for `connected_wallets`, `accounts` and `risk_limits` mention the dashboard or a daily pause. `services/risk/derive.py:287` emits "Daily pause after" in `risk_limits` notes. Nothing enforces a daily pause.
- `config.mode` gates nothing.
- `services/thesis/prompt.py:90-93` names the operator and a health detail in the system prompt that `desk_refresh` sends to the local model.
- `scripts/approve_api_wallet.py:42` and `services/accounts/agent_approval.py` hardcode the Mainnet chain and post through `hl_client`.
- `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA` exist only inside Claude Code. `pathiel-py` without the data dir builds a second venv under `~/.pathiel/data`.
- Claim hits measured with the lint patterns: `README.md` 4, `docs/strategy/OIL_STRATEGY.md` 17, `docs/overhaul/BACKTEST_RESULTS_2026-10-25.md` 54, `docs/strategy/SILVER_STOP_FLOOR_TEST_2026-11-02.md` 2. README, GETTING_STARTED and the shipped tick files link them.
- `tests/` has no `__init__.py`; the root `conftest.py` applies to `tests/`, `services/` and `contracts/`, and refuses any non-loopback socket in the test process, daemon threads included.

SDK (hyperliquid-python-sdk 0.24.0, read from the installed source):

- `Exchange.order(..., builder=None)` and `Exchange.bulk_orders(order_requests, builder=None, grouping="na")`. `bulk_orders` lowercases `builder["b"]` in place, puts the dict on the action as is, and signs the msgpack hash. Key order and value types are part of the signature.
- An action-level `{"status": "err"}` reply means the whole action was refused: nothing rested or filled.
- The chain flag for signing is `self.base_url == MAINNET_API_URL`.
- `sign_approve_builder_fee(wallet, action, is_mainnet)` needs the master key.

Testnet (read-only info calls, 2026-11-12):

- `perpDexs` returns 279 entries. `xyz` is there with 70 markets.
- `xyz:BRENTOIL` exists on testnet with `isDelisted: true`. `xyz:CL`, `xyz:SILVER`, `xyz:GOLD` are live.
- `maxBuilderFee` returns a bare integer, `0` for a pair with no approval.

## Outcomes

| # | Outcome | Proven by |
|---|---|---|
| 1 | Public, approved, warm cache: 4/4 order paths carry `{b, f: 10}` | `tests/test_eval_builder_coverage.py` |
| 2 | 13 builder states x 4 paths: 52/52 orders sent, 0 reads on the calling thread, at most 2 SDK calls, a known venue refusal sent once | same scorecard |
| 3 | A stop is never lost to builder state, whatever the venue's wording | `tests/test_exchange_builder_paths.py` |
| 4 | Operator: 0 builder fields, 0 jobs, 1 SDK call per order, mainnet, 20x, instructions, tools/list and thesis prompt hashes equal to base | `tests/test_operator_path_unchanged.py`, `tests/test_mcp_operator_unchanged.py`, `services/thesis/tests/test_prompt.py` |
| 5 | Fresh public install: leverage at most 3, risk at most 1% (2% only on a minimum-order bump), 1 position | `tests/test_eval_public_install.py` |
| 6 | Launcher with empty HOME resolves testnet; mainnet needs one explicit line; 0 mainnet requests in testnet mode | `tests/test_public_launcher.py`, `tests/test_eval_public_tiers.py` |
| 7 | No key: N/N market reads return data, K/K account reads (aliases included) say "no account configured", 6/6 writes say read-only, 0 tracebacks | `tests/test_eval_public_tiers.py` |
| 8 | 0 machine-bound strings, 0 operator name in the public thesis prompt, 0 stray venue URLs, treasury refuses off operator mainnet | `tests/test_eval_portability.py` |
| 9 | 0 claim hits in public copy, shipped files and every doc they link; 0 references to the gitignored ROI doc (the tracked tree may not name it, this doc included) | `tests/test_public_copy.py`, `tests/test_public_portability.py` |
| 10 | Versions agree at 0.4.0, README disclosure 7/7, every clone instruction carries the maintainer warning | `tests/test_eval_listing.py` |
| 11 | Approval: dry run sends 0 requests; `--yes` sends 1 on the right chain; 6/6 refusals send nothing; the printed command runs in a plain terminal | `tests/test_eval_builder_approval.py`, `tests/test_public_launcher.py` |

## The model: install kind, network, trading mode

One stdlib module, `contracts/runtime/__init__.py`, answers two questions from the environment.

| Question | Rule |
|---|---|
| Install kind | `PATHIEL_INSTALL=public` means public. Anything else, including unset, means operator. Only `scripts/_plugin_env.sh` and `Dockerfile.mcp` set it. |
| Network | A valid `PATHIEL_NETWORK` (`mainnet` or `testnet`) wins. Otherwise testnet for public, mainnet for operator. |

The plugin launcher resolves the network once in bash (process env, then the `PATHIEL_NETWORK` line in `~/.pathiel/.env.local`, then testnet), exports it, and picks the state dir from it: `~/.pathiel/state` for mainnet, `~/.pathiel/state-testnet` for testnet.

Trading mode on a public install:

| Key | Network | Result |
|---|---|---|
| none | any | read-only: every read works, the six live-money tools return the read-only error |
| set | testnet (default) | trades test money; needs a testnet API wallet from the testnet app |
| set | mainnet (explicit line) | trades real money |

The operator path skips all of this.

A clone is not a public install. Starting `scripts/pathiel-mcp-server.py` directly or accepting the repo `.mcp.json` is the maintainer path: mainnet and the tracked config. The docs say so with one fixed sentence wherever a checkout is described, and point other MCP clients at `scripts/pathiel-mcp` (the same launcher the plugin uses) or the container.

## Builder fee

- Rate: f = 10 tenths of a basis point. 0.01% of notional per fill, 0.02% a round trip. Tool output formula: `fee_usd = notional_usd * f / 100000`.
- Why 0.01%: a tenth of the 0.1% perp cap and about a fifth of the 4.5 bp base taker fee. At the default sizing a round trip costs about 1% of 1R. There is no demonstrated edge, so cost drag is what loses users.
- The approval asks for exactly the charged rate. Hyperliquid rejects any order whose builder fee is above the user's approved max.

Attach rule. Any "no" places the order without a builder:

1. public install
2. builder address set and valid
3. fee between 1 and 100
4. trading account is not the builder address
5. no cooldown running
6. the cache holds an approval at least as large as the fee, read less than 24 h ago

The order path never reads the network. `builder_for_order` looks at the cache and returns. Background threads fill the cache: once at server start, on `state`, on every live-money call, and whenever an entry is due (approved 600 s, not approved 60 s, read error 30 s). An approved entry stays usable for 24 h while its refresh runs. A cold cache means that order carries no fee.

The read is one plain `requests.post` to `/info` with timeout (1 s connect, 2 s read), after a limiter wait of at most 1 s. It does not use the shared session, so there are no adapter retries and no retry wrapper. Scripts that want a synchronous answer (`public_status.py --online`) get it through `builder_status(refresh=True)`, which waits at most 4 s.

Resend rule, only when a builder was attached:

- The reply is an action-level error, or status "ok" with every leg an error: nothing was placed, so send the same order once more without the builder. No builder wording is needed.
- Except: every error is one of Hyperliquid's own refusals that has nothing to do with the fee (`builder.KNOWN_VENUE_ERRORS`: an IOC that did not match, margin, price, size, minimum value, reduce only, post only, rate limit, open interest cap). That reply is returned after one send, with no cooldown and `reason_code` `venue_refused`. A second IOC there would be a market order nobody asked for. Any unknown wording still gets the resend, so a stop is never lost to a builder refusal worded in a new way.
- Start the 10 minute cooldown only if the resend then placed something, or the first error names the builder.
- Any leg resting or filled, an "ok" reply with no statuses, or any raised exception: never resend.
- With no builder attached, which is always the case for the operator, the SDK is called exactly once and the reply is returned unchanged.

The builder dict is built fresh per call as `{"b": address, "f": int}`, keys in that order. Tests pin the order and the int type because both are part of the signed hash.

Surfaces: the initialize instructions, the `install` block in `state`, a `builder_fee` block in every live-money tool output (with the reason when not charged), `scripts/public_status.py`, step 5 of `/pathiel:setup`, `docs/BUILDER_FEE.md`, the README. Trace: one log line per decision, counters in `builder_status()`, the scorecard.

## What the review changed

| Finding | Severity | Resolution |
|---|---|---|
| Retry depends on the word "builder"; a stop can be lost | major | Resend on any nothing-placed reply when a builder was attached; cooldown only on evidence |
| Builder read can take 18 s on an order path | major | Order path is cache only; dedicated bounded read in the background |
| Clone and direct-server launch run in operator mode | major | Maintainer warning in README and GETTING_STARTED, launcher or container for other clients, stderr notice when a checkout has no env file |
| Return tables reachable from public copy and the shipped agent | major | Links removed, clean `OIL_RULES.md`, ROI section removed from `OIL_STRATEGY.md`, one-hop reach lint |
| Thesis prompt names the operator | major | Neutral public persona; operator text pinned by hash |
| Approval command does not resolve in a plain terminal | major | `scripts/pathiel-mcp --commands` prints absolute paste-ready lines |
| Builder dict key order and types unpinned | minor | Two tests |
| Existing approvals sign Mainnet and post to the resolved network | minor | `approve_api_wallet.py` signs for the resolved network and refuses on public; `agent_approval.py` refuses off mainnet; setup names the testnet app |
| `treasury.py` signs mainnet while reads follow the network | minor | Exit 2 unless operator and mainnet |
| Resend rule not conditioned on an attached builder | minor | Stated in the spec and tested |
| Aliases skip the gate | minor | Gate and decorate classify the canonical name; alias rows in the eval |
| Dashboard wording in public tools/list | minor | `server_tools()` with reworded descriptions on public installs |
| risk_limits says two opposite things | minor | The "Daily pause after" note is replaced, not joined |
| Testnet state guard passes with the repo env file | minor | State dir must contain "testnet" |
| Env pop only under `tests/` | minor | Moved to the root `conftest.py` |
| Claims lint fires on disclaimers and misses wrapped text | minor | Whitespace collapsed, negation window, narrower patterns, planted cases |
| Subprocess tests can load the live env file | minor | One `public_env` fixture: existing env file, empty key values, dead proxies |
| Master key read from the env file | minor | Prompt only on public installs; `public_status.py` fails when the variable is set |

## What the final review changed

| Finding | Resolution | Proof |
|---|---|---|
| The tool class check could stop the operator's server at import | Raises only on a public install; the operator's launch logs the problem to stderr and starts | `test_operator_install_never_fails_startup_on_a_class_problem` |
| Any nothing-placed builder order was resent: a second IOC after a no-match, the fee blamed, a 10 minute cooldown | Known venue refusals (`builder.KNOWN_VENUE_ERRORS`) get one send, no cooldown, `reason_code` `venue_refused`; builder or unknown wording keeps the resend | `test_ioc_no_match_is_one_send_and_no_cooldown`, `venue_refuses_known` rows in the builder scorecard |
| `state` did not see a new approval at once | `state` reads the approval again (at most 4 s) when the cache has none yet; order paths never read | `test_state_reads_a_new_approval_at_once`, scenario D |
| The approval post went through the retrying session; a replayed nonce read as a failure | One plain `requests.post`; after an error the approval is read back before a failure is reported | `test_error_reply_with_the_approval_stored_reports_success` |
| The thesis offered 5x to 20x rungs over the 3x public cap | On a public install `desks.get_spec` cuts the ladder to the profile cap (2x and 3x at the default); the prompt, validator, analytics and brief follow | `services/strategies/tests/test_public_ladder.py`, `fresh: desk ladders` rows |
| An empty perpDexs read and a failed one looked the same | `universe.hip3_dex_registry()` returns None on failure; `coin_listing` says `not_listed` on a network with no HIP-3 dex; market read errors on such a coin carry the network note | `test_hip3_dex_registry_tells_empty_from_failed`, scenario D |
| `state` said `network_explicit: true` on the launcher's default | `runtime.network_is_explicit` reads `PATHIEL_NETWORK_SOURCE` | `test_launcher_default_is_not_a_choice` |

## Decisions

1. `PATHIEL_INSTALL`, set only by the launcher, decides public or operator.
2. One network per process, resolved once by the launcher, state split by network.
3. Fee 0.01%, approval for exactly that.
4. Order path is cache only for builder state. Zero reads on the calling thread.
5. Resend once without the builder on a nothing-placed reply, unless every error is a known venue refusal unrelated to the fee. Never on exceptions.
6. No builder attached means exactly one SDK call.
7. Public gates sit in `_call_tool`, on canonical names. `exchange.py` keeps its error strings.
8. No new MCP tool. 60 stays. Public installs get reworded descriptions through `server_tools()`.
9. The public profile is explicit `RiskProfile` fields mirrored by a tracked template.
10. `config.mode` stays a label. The daily loss figure is shown, not enforced, and public output says so once.
11. On testnet, HIP-3 sweeps cover only the allowlist; delisted or unlisted coins are refused for entries, never for closes.
12. Public copy links no research doc. `OIL_RULES.md` is the public rules page. `OIL_STRATEGY.md` loses its ROI section and gains an audit banner.
13. The checkout is the maintainer path and is labelled as such. No code heuristic tries to tell a stranger's clone from the operator's checkout.
14. The thesis persona is neutral on public installs; the rules block is shared.
15. The master key is typed at a prompt on public installs and never read from the env file.
16. Operator signing tools refuse off their network: treasury, API wallet approval, dashboard wallet approval.
17. A maintainer testnet run needs a state dir with "testnet" in its path.
18. Shared test fixtures in `tests/conftest.py`; runtime env pop and builder isolation in the root `conftest.py`; evals are single `tests/test_eval_<name>.py` files.
19. Keyless installs default to testnet. Registries get a container image. Approval is a script. Research tools stay on a mainnet allowlist.

## Units

| Unit | Wave | Owns (main files) | Outcomes |
|---|---|---|---|
| venue-core | 0 | `contracts/runtime/`, `pathiel/client/{builder,exchange,hl_client,universe}.py`, root `conftest.py` | 1, 2, 3, 4, 6 |
| test-kit | 0 | `tests/conftest.py` | supports 7 to 11 |
| mcp-surface | 1 | `scripts/pathiel-mcp-server.py` | 4, 7, parts of 5, 6, 8 |
| public-install | 1 | `scripts/_plugin_env.sh`, `scripts/pathiel-mcp`, `commands/setup.md`, `config/public/`, `services/risk/presets.py`, `pathiel/agents/config_store.py`, `scripts/config_preset.py`, `scripts/public_status.py`, `Dockerfile.mcp` | 5, 6, 11 |
| builder-approval | 1 | `scripts/approve_builder_fee.py`, `docs/BUILDER_FEE.md`, `scripts/approve_api_wallet.py`, `services/accounts/agent_approval.py` | 11 |
| portability | 1 | `services/intel/sources/venue.py`, `scripts/oil_levels.py`, `scripts/build_plugin_skill.py`, generated tick files, `skills/pathiel-agent/`, `scripts/treasury.py`, `services/thesis/prompt.py` | 8, 9 |
| listing-docs | 1 | `README.md`, `docs/GETTING_STARTED.md`, `docs/strategy/OIL_RULES.md`, `docs/strategy/OIL_STRATEGY.md`, `server.json`, `smithery.yaml`, `glama.json`, `.claude-plugin/*.json`, `pathiel/__init__.py` | 9, 10 |

The two wave-0 units build in parallel and are merged first. The five wave-1 units build in parallel on top with disjoint files. Each passes the full gate alone. References that cross units are checked after merge: `commands/setup.md` names `scripts/approve_builder_fee.py`; the launcher's printed commands name the same script; README links `docs/BUILDER_FEE.md`; `smithery.yaml` and `server.json` name `Dockerfile.mcp` and its image; the shipped reach set includes `commands/setup.md`.

### venue-core (wave 0)

- `contracts/runtime/__init__.py`: `install_kind`, `is_public`, `network`, `network_is_explicit`, `api_url`, `info_url`, `is_mainnet`, `builder_address`, `builder_fee_tenths_bp`, `fee_rate_str`, `describe`, and `DEFAULT_BUILDER_ADDRESS = ""` (the single config point), `DEFAULT_BUILDER_FEE_TENTHS_BP = 10`, `MAX_PERP_BUILDER_FEE_TENTHS_BP = 100`. Stdlib only.
- `hl_client.py`: `HL_API = runtime.api_url()`, `base_url=HL_API` on the Info constructor, and the maintainer testnet guard. `_http_post_strict` is not changed.
- `universe.py`: cache dir scoped by network; `registered_hip3_dexes()`; `list_hip3_dexes()` filtered to the allowlist on testnet and unchanged on mainnet.
- `builder.py`: `fetch_max_builder_fee` (one plain bounded post), `_fetch`, `_spawn`, `warm`, `builder_for_order` (cache only), `last_decision`, `thread_decision`, `clear_thread_decision`, `builder_status`, `is_builder_rejection`, `note_rejection`, `count`, `reset`. Reason codes: operator_install, no_builder_address, fee_zero, self_trade, cache_cold, not_approved, approval_read_failed, rejected_cooldown, approved.
- `exchange.py`: `_order_builder`, `_nothing_placed`, `_send_order`, `trading_block_reason`, `READ_ONLY_NO_KEY`, `coin_listing`; the four SDK calls go through `_send_order`.
- Root `conftest.py`: clears the four runtime env vars before anything loads, resets builder state around every test, and replaces `builder._spawn` with a recorder (`builder_jobs` fixture) so no test starts a network thread.

### test-kit (wave 0)

Fixtures in `tests/conftest.py`:

- `public_env`: a complete subprocess env with a temp HOME, an env file that exists, the four `HYPERLIQUID_*` variables present and empty, `PATHIEL_INSTALL=public`, and proxies pointed at a dead loopback port.
- `copy_lint`: `PERFORMANCE_PATTERNS`, `claim_hits` (whitespace collapsed, 30-character negation window), `style_hits`, `linked_docs`, `reach` (one hop).
- `scorecard`: prints a table, writes `$PATHIEL_EVAL_OUT/<name>.json`, returns `{name, passed, total, rows}`.

### mcp-surface

- `server_instructions()`: the unchanged constant for the operator, a public text (at most 2500 chars) otherwise.
- `server_tools()`: `TOOLS` for the operator; a copy with dashboard and daily-pause wording replaced for public installs.
- Every tool classified once; aliases resolved to canonical names before classification.
- `_public_gate`: read-only refusal without a key (dry-run brackets pass), builder cache warm-up on live-money calls, `set_leverage` capped at the profile, delisted or unlisted coins refused for entries, account reads without an address answered with one standard message.
- `_public_decorate`: `network` and `builder_fee` on live-money outputs, `install` on `state`, the daily-pause note replaced on `risk_limits`, a `network_note` on desk briefs, no dashboard wording.
- `desk_refresh` on public installs fails fast when the claude CLI is missing.
- One stderr notice in operator mode when the checkout has no `.env.local` and `PATHIEL_ENV_FILE` is unset. It never prints for the operator.

### public-install

- Launcher split into `pathiel_paths`, `pathiel_resolve_network`, `pathiel_state_paths`, `pathiel_build_venv`, `pathiel_seed_config`, `pathiel_print_commands`. `scripts/pathiel-mcp --commands` prints three paste-ready lines (status, approve-dry, approve-sign) with absolute paths and the data dir set inline.
- `PUBLIC_DEFAULT_PROFILE`: custom, risk 1.0%, daily 3.0%, max leverage 3, 1 position, bump allowed up to 2.0%. Mirrored by `config/public/agent-config.json`.
- `config_store.py`: a public install defaults to `~/.pathiel/.agent-config.json`, never the repo file, and falls back to the public profile when the profile is missing or invalid.
- `config_preset.py seed-public`: writes the template once, never overwrites, refuses the repo config.
- `public_status.py`: PASS, WARN, FAIL lines for network, keys by name, master key absent, trading mode, profile, builder fee, claude CLI.
- `commands/setup.md`: disclosure, bootstrap, mode choice, keys (testnet needs its own API wallet from app.hyperliquid-testnet.xyz), status, builder fee approval through the printed command, connect, proof.
- `Dockerfile.mcp`: the same launcher as the entrypoint, state in a `/data` volume.

### builder-approval

- `scripts/approve_builder_fee.py`: dry run by default; `--yes` signs with the master key; on a public install the key comes from the hidden prompt only; refuses when no builder is set, when the builder is the account, when the rate is above 0.1%, on network drift, when the key belongs to another account, when there is no key source. Confirms with one `maxBuilderFee` read.
- `scripts/approve_api_wallet.py`: signs for the resolved network; refuses on a public install and on network drift; operator mainnet output unchanged.
- `services/accounts/agent_approval.py`: refuses off mainnet before any post.
- `docs/BUILDER_FEE.md`: rate, scope, approval steps, enforcement, how to stop.

### portability

- `venue.py` and `oil_levels.py` read the info URL from the resolver.
- `scripts/treasury.py` exits 2 unless install is operator and network is mainnet.
- `services/thesis/prompt.py`: neutral persona on public installs; operator prompt sha256 `fc6794201c10e43d5214bd268fa4d4edd950f0c25e095c897e6a5584b72eca8c` for the fixture desk spec.
- Generator rules strip the operator's 20x, 15x and desk share numbers and every `docs/strategy/` and `BACKTEST_RESULTS` pointer from the plugin tick files. The operator sources under `.claude/` are not edited.
- `skills/pathiel-agent/` loses its absolute path and local-server assumptions.
- Gate tests: hardcoded venue URLs only in five named files, the one signer among them guarded; the shipped reach set has no claim hits.

### listing-docs

- Version 0.4.0 in `pathiel/__init__.py`, `plugin.json`, `server.json`.
- `server.json` (one OCI package over stdio, `PATHIEL_NETWORK` default testnet, no secrets), `smithery.yaml`, `glama.json`.
- README: "Public install: read this before you trade" with the seven disclosures; the maintainer warning in the MCP server, Running it and Risk sections; four claim sentences rewritten; research links removed.
- GETTING_STARTED: plugin install first; the checkout block retitled and placed under the maintainer warning; links moved to `OIL_RULES.md`.
- `docs/strategy/OIL_RULES.md`: rules only, numbers read from code, an honest "what is not known" paragraph.
- `docs/strategy/OIL_STRATEGY.md`: audit banner under the title, ROI section replaced by a short summary with no figures.
- Tests: the reach-set lint, the maintainer-warning check, the ROI check, the metadata checks.

## Invariants and their proof

| Invariant | How it holds | Test |
|---|---|---|
| I1 operator unchanged | every new behavior is behind `is_public()` or a testnet check; SDK calls get no new kwarg and are made once; outputs get no new key | instructions sha256 `8d82a135a7ac6113ae0a44c444d19dd1873f0a830cf0763f971b7eccffda5c1c`, tools/list hash pinned at base, `.mcp.json` sha256 `8edb54198025f4c1a99bbbf6907e5ef0d1e19d225e7fccb6eabd1f3b282c8ffc`, thesis prompt sha256 `fc6794201c10e43d5214bd268fa4d4edd950f0c25e095c897e6a5584b72eca8c`, fake SDKs without a builder parameter |
| I1 no builder to self | attach rule step 4 | `test_account_equal_to_builder_never_attaches` |
| I2 never block | cache-only decision; one resend on a nothing-placed reply unless it is a known venue refusal; exceptions untouched | 52/52 scorecard, `inline_reads 0`, `test_stop_after_market_entry_survives_a_builder_rejection` |
| I3 tracked config untouched | no unit owns `.agent-config.json`; seed-public refuses the repo path | `git diff --stat 2816df7 HEAD -- .agent-config.json` is empty |
| I4 agent safety | tests run under pytest with temp HOME and state, keys pinned empty, sockets refused in process, dead proxies in subprocesses; signing only with an injected fake post; the signing line from `--commands` is compared as text, never run | conftest guard, `tests/test_eval_public_kit.py`, eval harness URL log |
| I5 tests and evals | each unit names both | seven scorecards |
| I6 writing style | lint over public copy | `tests/test_public_copy.py` |

Safety envelope for any ad hoc Python that imports pathiel outside pytest (prefer pytest):

```
T="$(mktemp -d)"; mkdir -p "$T/home/.pathiel"; : > "$T/home/.pathiel/.env.local"
env -i PATH="$PATH" HOME="$T/home" PATHIEL_HOME="$T/home/.pathiel" \
  PATHIEL_ENV_FILE="$T/home/.pathiel/.env.local" PATHIEL_STATE_DIR="$T/state-testnet" \
  PATHIEL_AGENT_CONFIG_FILE="$T/agent-config.json" PATHIEL_UNIVERSE_CACHE_DIR="$T/universe" \
  HYPERLIQUID_PRIVATE_KEY= HYPERLIQUID_MASTER_PRIVATE_KEY= \
  HYPERLIQUID_WALLET_ADDRESS= HYPERLIQUID_MASTER_ADDRESS= \
  .venv/bin/python your_script.py
```

The env file must exist. A missing one makes the loader fall back to the checkout's `.env.local`.

## Where it breaks

- The first order after a cold start carries no fee if the background read has not landed.
- A refusal in wording that is not in `KNOWN_VENUE_ERRORS` is resent: one extra request, usually the same refusal. An IOC refused that way can fill on the resend.
- A transient venue error in unknown wording followed by a clean resend starts a 10 minute cooldown that was not needed. Fees are lost for that window; orders are not.
- The network is fixed at process start. Changing the env file needs an MCP reconnect. A drift guard refuses to sign for the wrong chain.
- The oil desk cannot be practised on testnet because Brent is delisted there. Tools say so. `xyz:CL` and the main-dex perps work.
- A stranger who ignores the maintainer warning and runs the server from a clone with his own key trades mainnet on the tracked 20x config. No code check can tell his clone from the operator's checkout without risking the operator's live stack.
- Research docs with backtest figures remain in the public repo. They are unlinked from public copy and shipped files, and `OIL_STRATEGY.md` opens with the audit banner.
- The claims lint is a pattern list with a one-hop reach. A new phrasing, or a clean doc that links a research doc, can pass.
- `OIL_RULES.md` restates rules that live in code; only the minimum R is tied to code by a test.
- The public thesis persona is tested for structure, not for thesis quality.
- The daily loss figure is not enforced. Public copy says so.
- `Dockerfile.mcp` is checked statically in the gate.
- The `0%` revoke value has not been exercised live. `PATHIEL_BUILDER_FEE_TENTHS_BP=0` is the certain way to stop the fee.
- Users on plugin 0.3.0 move to testnet on update until they add the mainnet line.

## Rollout

1. Build venue-core and test-kit in parallel, run the gate and their scorecards, merge both.
2. Build the five wave-1 units in parallel worktrees on top, each green alone, then merge. Suggested order: public-install, mcp-surface, builder-approval, portability, listing-docs.
3. Run the integration checks: ruff, the full gate, the seven scorecards, the operator proofs, the stop-safety tests, both reach-set lints, the cross-unit reference check, the file-untouched diffs.
4. Commit this doc. Push the branch. Nothing is submitted to any registry by an agent.

## What Julien must do

1. Pick the builder wallet. Put its address in `DEFAULT_BUILDER_ADDRESS` in `contracts/runtime/__init__.py`. It becomes public. Keep at least 100 USDC of perps account value in it on mainnet.
2. For a testnet rehearsal, give the same wallet 100 test USDC on testnet. From a second testnet account, with a testnet API wallet, run `/pathiel:setup`, run the printed approve-dry line, then the approve-sign line in your own terminal, place a small testnet bracket and confirm the fill shows a builder fee.
3. Decide the open questions: fee level, the ROI section removal, keyless default, notice to 0.3.0 users, image name.
4. Optional before publishing: run the existing thesis eval once with `PATHIEL_INSTALL=public` to check the neutral persona.
5. Tag `v0.4.0`. Build and push the image from `Dockerfile.mcp` to `ghcr.io/julian-dev28/pathiel-mcp:0.4.0`.
6. Submit: official MCP registry with `mcp-publisher login github` then `mcp-publisher publish`, Smithery (connect the repo, check `smithery.yaml` against their current format), Glama (claim with `glama.json`).
7. Builder fees accrue to the builder address and are claimed through Hyperliquid's referral rewards flow.

## Restart

Your live stack needs nothing. Behavior on your path is identical. If you want the running processes on the new code anyway: reconnect the MCP server from Claude Code with `/mcp`, and restart the dashboard with `scripts/restart.sh` (yours to run; agents never run it). No sudo is involved.
