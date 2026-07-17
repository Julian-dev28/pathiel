# DEMOLITION MANIFEST — hermes_trader/agents/ (2026-07-18)

Read-only audit. Nothing deleted. Evidence hierarchy: shadow-ledger verdicts >
config state > ledger inventories > git activity > import graph. PnL attribution
from `scripts/pnl_by_book.py --days 14` (fills 2026-07-09 -> 2026-07-18).

## Headline numbers

| metric | value |
|---|---|
| Modules in agents/ | 35 .py (+ unlock_map.json data file) |
| Total lines now | 12,451 |
| KILL | 4 modules, 1,100 lines |
| SHRINK | 2 modules, est. -625 lines |
| Projected after manifest | ~10,726 lines (-14%); KILL-only floor 11,351 |

14-day realized PnL context (pnl_by_book): TOTAL net **-$91.52**. main-engine
(AI research path) **-$83.02** = 91% of the bleed (caveat: 89.8% of episodes are
default-attributed and the operator trades manually on the same account).
Books: extreme_fade -$4.43 (n=1), news_catalyst -$3.48 (n=3, REFUTED),
crash_continue -$0.59 (n=1). Every other book: zero fills in 14 days.
The strategy books are not where the money went. The main engine is the #1
PnL suspect; that verdict belongs to PNL_FORENSICS.md / MINIMAL_SYSTEM.md,
not this per-module manifest.

Shadow-ledger inventory (2026-07-18 06:12, `shadow_status.py --inventory`):
14 books, notable rows: news_catalyst 4910 sig / 2214 res; whale_flow 334/116;
extreme_fade 230/98; young_listings 163/95; majors_swing 21/0 (silent 158.6h);
mover_pass 19/5; crash_continue 3/0. Grading verdicts on record: news_catalyst
REFUTED 2026-07-16 (df3df37); premium_fade_short and neg_funding_fade REFUTED,
modules already deleted (f967e6b, 6916b85).

## The manifest

| module | lines | role | verdict-evidence | call | why |
|---|---|---|---|---|---|
| ai_brain.py | 549 | AI verdict providers (claude_cli since 3cabe28 07-13; openrouter legacy) | research.py dep; AI-close is a standing operator requirement | KEEP | The provider contract for the research verdict. Shrink-later: retire the openrouter path once claude_cli is proven. |
| config.py | 65 | Trigger weights/thresholds for perception | Loop + backtests import; weights re-fit to measured lift 06-02 | KEEP | Live scan config, tiny. |
| config_store.py | 235 | Read/normalize .agent-config.json, fail-safe mode OFF | 18 importers across loop/server/dashboard/MCP | KEEP | The config contract for everything. |
| crash_continue_div_short_live.py | 401 | Live short: BTC-up tape, coin -8%/2d, continuation | Backtest +9.1%@12bps, +7.0% excess vs matched null, OOS both + (extreme_surface 06-27). Forward: 3 sig / 0 graded; 14d realized -$0.59. BTC-up-gated so dormancy is by design | KEEP | Validated structure, live, cheap. Review bar: if still 0 graded episodes by 2026-08-15, re-table as KILL (cannot validate at this fire rate). |
| crypto_whale.py | 193 | Binance aggTrades whale-print reader (free, keyless) | Sole consumer: whale_flow_live recorder | KEEP | Lives or dies with whale_flow. If whale_flow grades REFUTED, kill both. |
| data_logger.py | 87 | Funding + OI snapshot logger, zero added API load | Feeds the blocked-data lanes (D7); W-F4 OI re-run due 2026-07-30 | KEEP | Measurement accruing toward a dated bar. |
| dsl_exit.py | 739 | DSL stop/trail engine, backup-SL, TP plumbing | Safety rail. P0 entry-basis fix f941562 yesterday; 21 commits | KEEP | Protects money. Do not touch during demolition. |
| engulf_short_live.py | 381 | Live xs bearish-engulf short, $20/12x, 1d hold | W-C1/C2: +1.25%/trade vs matched random-short null p=0.00012, OOS both +, short-only (long leg refuted). Forward 5 sig / 4 res; 0 fills 14d | KEEP | Validated against the strictest null in the book stable; low fire rate is the design. |
| executor.py | 1647 | Order path: sizing, gate glue, TP scale-out, backup SL, book tags, thin_short_relax admit | 86 commits, actively maintained; no dead-book references left; 13% of agents/ in one file | SHRINK | Working but overgrown. Split gates/TP/book-plumbing into peers AFTER the kill wave settles, behavior-preserving, est. -300 lines. Never mid-demolition. |
| extreme_fade.py | 95 | Pure crash-fade signal engine | Sole consumer extreme_fade_live | KEEP | Pure, tested, tiny. |
| extreme_fade_live.py | 358 | Flagship long book: fade completed -12% daily crash | SETTLE-2 +4.71%/trade OOS both +; W-B2 skew-arm ROBUST (enforce=true live); ledger 230 sig / 98 res, active 0.5h ago. 14d realized -$4.43 on n=1 (noise) | KEEP | The strongest validated edge in the account. |
| funding_spike_short_live.py | 321 | Live short: fade funding z>=2 crowded longs, 5d hold | W-F2A VALIDATED +6.2%/ep p=0.0027, dedup made it stronger; live per standing auto-flip order. 0 signals since live 07-09 (z>=2 is rare); no ledger file yet because zero signals ever fired | KEEP | Validated. Dormant by rarity, not by defect. Verify the recording path the first time a signal fires. |
| hyperfeed.py | 475 | HL leaderboard / trader-discovery / market lookups | Live path uses exactly ONE function: market_get_funding_regime (risk_gates.py:332). The leaderboard_* / discovery_* surface (~270 lines) serves only scripts/hermes-mcp-server.py | SHRINK | Keep funding-regime + universe helpers (~150 lines) in agents/; move the MCP-only discovery surface out (operator tooling, not loop code). Est. -325 lines from agents/. |
| majors_swing_live.py | 386 | Majors trend + pullback-resume longs, 0.25 eq x 12x | UNVALIDATED by its own docstring; breakout cousin on the SAME asset set REFUTED by matched null (e7f3935). Flipped live 07-13 (db9927e) while PENDING, against validate-first. Ledger 21 sig / 0 graded, silent 158.6h. 0 fills 14d. Geometry: 300% notional, 2.2% liq-capped stop; one stop-out ~ -6.6% equity on a $19 account | **KILL** | Live capital on a never-validated entry whose nearest relative is refuted, producing zero graded evidence, at the most aggressive geometry in the stable. |
| market_regime.py | 230 | Per-asset-class regime classifier (crypto/equity/commodity) | Dep of executor, risk_gates, hyperfeed; feeds the regime gate | KEEP | Risk-gate input. |
| memory.py | 421 | Disk-backed agent memory singleton | Loop, server, dashboard, research all import; 23 commits | KEEP | Measurement + state. |
| mover_recorders.py | 338 | 4 zero-capital recorders: mover_pass (W-M4), b15_up (W-M1), news_ta_quadrant, trend_block_news_long | Pre-registered promotion bars (>=30 ep, EV25>0 both halves). mover_pass 19 sig / 5 res, b15_up 15/5, news_ta 6/5, all accruing | KEEP | Measurement toward dated bars. Config flag: pass_live went LIVE 07-13 at n far below its own bar; flip mover_recorders.pass_live.shadow_only back to true (config change, not module change). |
| news_catalyst.py | 469 | Google News coverage-surge detector (keyless RSS) | Feeds research.py AI context + scripts/news.py /news UI. Operator standing order (07-15): keep news cards, summaries, citation links | KEEP | The detector is UI + research context, not the refuted trade. Does not read the news_catalyst config key. |
| news_catalyst_live.py | 257 | Breaking-news LONG book + 30-min shadow recorder | **REFUTED 2026-07-16** at its own pre-committed bar: -8.65%/sig @12bps, n=34, both OOS halves negative (-7.35 / -9.94), df3df37. Live arm lost -$3.48 realized. Ledger 4,910 rows and still recording every 30 min on a decided question | **KILL** | Ledger verdict REFUTED = kill per demolition rule. History stays in .state/shadow_ledger/news_catalyst.jsonl + a _DEAD_BOOKS entry. |
| oi_logger.py | 51 | Self-collected OI time-series (HL keeps no history) | W-F4 / D7 blocked-data re-run due 2026-07-30 depends on it; zero extra API calls | KEEP | Measurement toward a dated bar, 51 lines. |
| perception.py | 535 | Scan engine: candles, triggers, composite score | Core loop + server + MCP; 21 commits | KEEP | The engine's eyes. Known defect for the rebuild: live reads the forming bar where backtests read completed (07-10 audit). |
| rally_exhaustion_live.py | 350 | Live short: BTC-down tape, coin +12%/2d rally | Codex-corrected +EV with WIDE stop; current config stop_pct=25 (the audit's inverted ~6% drift is gone from config); B10 garch lane independently re-derived the effect. btc_down=False now, so dormant; 0 fills 14d | KEEP | Corroborated edge, correct config, regime-dormant by design. |
| rebalancer_owned.py | 405 | Claims registry: stops books closing foreign positions | 19 importers; every book routes ownership through it | KEEP | Safety rail. On kills, prune the dead names from _ACTIVE_CLAIM_BOOKS (lines 77-79). |
| research.py | 734 | Perception -> indicators -> AI verdict -> persist (the main engine) | main-engine realized -$83.02 net /14d, 43% win, avgL -4.82 vs avgW +2.75; activity audit 06-28: -$206/8wk fee/churn-dominated | KEEP (flagged) | The loop cannot run without it, and attribution is too polluted (default-main 89.8% + manual trades) to convict from here. It is the #1 PnL suspect; MINIMAL_SYSTEM.md decides if the AI engine survives the rebuild. |
| risk_gates.py | 521 | Pure gate functions: history floor, reentry cap, book blocks, regime | Swarm wave 07-09: the gates SAVE money; 26 commits | KEEP | Protects money. |
| shadow_ledger.py | 351 | Unified shadow ledger: record + grade + classify | The measurement backbone; shadow_status.py and every book depend on it | KEEP | This is how anything ever gets a verdict. |
| sizing.py | 117 | Pure ATR/turtle risk-first sizing lib | Live consumer (atr_risk_sizing) deleted in the f967e6b rip-out; unreachable from loop AND server; only scripts/strategy_grid_search.py + scripts/backtest_logged.py import it | **KILL** (relocate) | Dead weight in agents/ by the import-graph rule. Move the file + tests/test_sizing.py under scripts/ or a research lib so the two backtest harnesses keep working, then delete from agents/. |
| system_prompt.py | 129 | Conviction-biased AI prompt (PASS = worst verdict) | Research dep. The conviction bias is implicated in main-engine churn (activity audit 06-28; W-M4 measured the opposite failure, PASS-veto forfeits) | KEEP (flagged) | Needed while the AI engine runs. Rebuild candidate, not a deletion. |
| ta_filter.py | 197 | Deterministic pre-AI statistical gate | Loop imports; saves LLM calls on junk triggers | KEEP | Deterministic-space gate, exactly where it belongs. |
| unlock_recorder.py | 189 | Unlock calendar keeper + 2 zero-capital arms (T-1d cell, run-in shadow rows) | T-1d cell NOT validated (p=0.10, sign flip) so it stays recorder-only; run-in drift -2.1% n=408 robust exploratory; also the ONLY calendar source unlock_short_live reads | KEEP | Measurement + shared calendar infrastructure. |
| unlock_short_live.py | 220 | Live run-in short (48-72h pre-unlock), $20/12x | Live by operator order 07-11 WITHOUT validation, with a pre-committed kill: shadow if EV25<0 after 10 episodes. Now 6 episodes / 2-3 resolved: bar not reached. 0 fills 14d | KEEP | Its own kill bar is dated and not yet reached. Let the bar decide; do not pre-empt it in either direction. |
| whale_flow_live.py | 103 | Zero-capital whale order-flow recorder | 334 sig / 116 res, active 0.2h ago, zero HL rate-budget impact (Binance API). Promotion bar >=30/side is likely REACHED on resolved n | KEEP (grade now) | Still accruing, but the next action is a grading run; a REFUTED verdict kills this + crypto_whale together. |
| xs_momentum.py | 150 | Pure xs momentum engine | Sole consumer xs_momentum_live | KEEP | Pure, tested. |
| xs_momentum_live.py | 412 | Cross-sectional momentum rebalancer (the honest live stack, ~+1.4%) | 06-23 honest-stack verdict; W6 vol-managed wired. Zero realized episodes attributed in 14d = attribution gap, flag for PNL_FORENSICS | KEEP | One of two edges the honest accounting ever blessed. |
| young_listings_live.py | 340 | Lane for sub-60-bar xyz listings | W-Y1 pre-registered backtest: continuation LONG REFUTED (best cell -2.03%, MC p=0.996 = worse than random), continuation SHORT REFUTED (all 12 cells negative), drift REFUTED (OOS sign flip). The one +EV cell (crash fade-long) is STRONGER in the mature window, i.e. it is extreme_fade, not a young-listing edge. up_action/down_action already "off": earns nothing by config; ledger (163 sig) keeps recording the refuted continuation frame | **KILL** | Its own registered hypotheses are refuted and its only live function is recording a dead question. The min_history_bars floor that protects the main engine lives in risk_gates and is untouched. |
| (unlock_map.json) | n/a | Data file for unlock_recorder | referenced at unlock_recorder.py:44 | KEEP | Not code. |

## KILL list, applied

| module | lines | one-line reason |
|---|---|---|
| news_catalyst_live.py | 257 | Ledger verdict REFUTED 2026-07-16 at its own pre-committed bar (-8.65%/sig, both halves negative); still burning a 30-min recorder on a decided question. |
| majors_swing_live.py | 386 | Never validated, nearest cousin refuted on the same assets, 21 signals / 0 graded / silent 6.6 days, live at 300% notional on a $19 account. |
| young_listings_live.py | 340 | W-Y1 refuted every registered hypothesis (best long cell worse than random, p=0.996); actions already off; the surviving +EV cell belongs to extreme_fade. |
| sizing.py | 117 | Live consumer deleted in f967e6b; unreachable from loop and server; relocate to research tooling for the two backtest scripts, remove from agents/. |

Removed from agents/: **1,100 lines** (12,451 -> 11,351).
With the two SHRINKs (hyperfeed -325, executor -300): projected **~10,726**.

## Orphaned .agent-config.json keys after the kill wave

| key | orphaned by | note |
|---|---|---|
| `majors_swing` (whole block) | majors_swing_live.py | only readers: the module + dashboard _BOOKS badge row |
| `young_listings` (whole block) | young_listings_live.py | same pattern |
| `news_catalyst` (whole block) | news_catalyst_live.py | news_catalyst.py (detector) does NOT read this key; verified no readers in research.py / scripts/news.py |

No other keys orphan. `thin_short_relax` stays (executor reads it),
`unlock_short`/`unlock_recorder` stay, `strategy_book_*` stay,
`atr_risk_sizing` was already removed with the f967e6b rip-out.

## Collateral edits required when the kills are executed (NOT done now)

1. `scripts/trading_loop.py`: remove imports (lines 62, 64, 74) and call sites
   (~832 young_listings, ~859 majors_swing, ~909 news_catalyst).
2. `hermes_trader/dashboard.py`: move the three _BOOKS rows (570, 572, 576)
   into `_DEAD_BOOKS` with closing verdicts (the 5dfb885 convention).
3. `hermes_trader/agents/rebalancer_owned.py:77-79`: drop "majors_swing",
   "young_listings", "news_catalyst" from _ACTIVE_CLAIM_BOOKS. Check the claims
   registry for open claims by these books before removal.
4. Tests: delete tests/test_majors_swing_live.py, test_news_catalyst_live.py,
   test_young_listings_live.py; move tests/test_sizing.py with sizing.py; sweep
   references in test_dashboard_pages.py, test_outcome_store.py,
   test_claims_registry_and_sizing.py, test_live_executor_result_semantics.py.
5. `scripts/pnl_by_book.py` BOOK_PRIORITY: names STAY (historical attribution
   convention, same as vol_breakout/premium_fade).
6. Ledger files in .state/shadow_ledger/ STAY (history + _DEAD_BOOKS evidence).
7. Before touching anything: confirm no open positions/claims tagged to the
   three books (14d fills show none, but check live state at execution time).

## Standing follow-ups this manifest surfaces

- Grade whale_flow now (resolved n likely at the >=30/side bar). REFUTED means
  whale_flow_live.py + crypto_whale.py both go on the next kill list (-296 lines).
- unlock_short_runin: kill bar at 10 episodes, currently 6. Dated decision.
- crash_continue_div_short: re-table as KILL if still 0 graded by 2026-08-15.
- mover_recorders pass_live: flip shadow_only back to true (live at n=19 vs a
  pre-registered bar of 30).
- The main engine (research.py + system_prompt.py + executor sizing of AI longs)
  is 91% of the 14-day bleed as attributed. That is the demolition question that
  matters, and it is MINIMAL_SYSTEM.md's to answer.
