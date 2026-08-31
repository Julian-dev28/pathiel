# HANDOFF — 2026-07-11 (Claude → Codex)

You are taking over a LIVE real-money Hyperliquid perps bot. Account:
**$39.30, flat**. Branch `able`, everything committed through `21e7503`,
tree clean except research scratch. The operator (Julien) pledged no manual
trading through **2026-07-24** (broken 3x; his last manual position closed
2026-07-11 ~13:00). Read `CLAUDE.md` first — evidence discipline is the game.

## The one-paragraph state

Main AI engine (claude_cli brain, now with selective live web search) plus
fixed-size strategy books. Everything new ships as a zero-capital shadow
recorder and only earns capital after forward validation
(`python scripts/shadow_status.py` grades `.state/shadow_ledger/*.jsonl`).
Today three things went live: web search in AI research, the
unlock_short_runin book ($20/1x), and two new recorders (unlock 24h arm,
news coverage-surge). Bot-only weekly EV at this equity: ~+$2.30-3.70 if
priors hold. Loop pid check: `scripts/restart.sh status`.

## LIVE books (capital on) — all sizes in .agent-config.json (hot-read)

| book | size | thesis | kill |
|---|---|---|---|
| main engine | 0.5eq×15x, caps below | AI verdicts, runner-gated | max_daily_loss_usd −12 |
| xs_momentum | eq-frac, k4/hold5 | 7d xs momentum, residual | enabled=false |
| extreme_fade | 0.4eq (deep 0.6) | ≤−12% crash fade LONG, skew-armed | shadow_only=true |
| rally_exhaustion | $20/1x, 25% stop | +12%/2d BTC-tape short | shadow_only=true |
| crash_continue | $20/1x | BTC-up + coin −8%/2d short | shadow_only=true |
| engulf_short | $20/1x, trail 3.0/0.10 | xs bearish engulf next-day short | shadow_only=true |
| funding_spike_short | $20/1x | 24h funding z≥2 crowded-long fade | fwd EV25<0 @15 eps |
| **unlock_short_runin** | $20/1x | short 48-72h pre-unlock, exit AT event | **review @10 eps** |
| thin_short_relax | $20 executor carve | conf≥0.72 shorts under vol floor | enabled=false |
| **news_surge_short** | $20/10x, 15% stop | breaking news-coverage-surge short, xyz-equities-only | **review @8 eps** |
| **mover_pass_short** | $20/10x, 15% stop | short the mover the AI just PASSed (inverse of mover_pass) | **review @8 eps** |

Main-engine caps: ai_long_notional_usd 25 (AI longs are ANTI-calibrated:
−2.13% @0.70-0.80 conf — do not raise without the second calibration window).

NOT live, evidence said no: unlock_short T-1d arm (p=0.10 + sign flip),
news breaking-longs (n=1), majors_swing (shadow, first grades ~07-17),
young_listings (recorder, actions off), neg_funding_fade (shadow, leaning
refuted −2.0%/ep, dies at n≥8 negative).

## Shipped 2026-07-11 (context for what you're watching)

1. **Web search in AI research** (6a3f22d): claude_cli gets `--tools
   WebSearch` ONLY for |24h move|≥8% or held coins; hot-kill
   `ai_brain.web_search.enabled`. Verified end-to-end (HMSTR:
   `web_search=on` logged, `web_search_used:true` tagged). ⚠️ The model
   FAKES headlines unless ordered to search — `_WEB_SEARCH_BLOCK` in
   research.py does that; audit envelopes via `web_search_requests`.
2. **Unlock lane** (6a3f22d, ed05a12): DefiLlama OPEN DATASETS bucket
   (`defillama-datasets.llama.fi/emissionsIndex` — api.llama.fi went paid).
   W-U1, 915 events: **−2.1% drift T-3d→T (n=408), done by the event** →
   live book (48-72h window, exits AT unlock) + recorder arms. First live
   candidate: SEI entered the window 2026-07-11 afternoon.
3. **news_catalyst_live** (W-N3): 30-min Google News coverage-surge reads on
   scan candidates; non-breaking rows are the built-in null. Go-live gate:
   ≥60d AND EV25(breaking)>0 AND > non-breaking AND n≥15.
4. **KillaXBT program CLOSED** (43d5f34, research/killa_xbt/): Outcome A —
   every mechanical range/deviation translation refuted OOS. A range-location
   gate would KILL extreme_fade's best bucket (range low = +4.06%/ep, n=75).
   Do not rebuild. Reusable: `api.fxtwitter.com` beats the X access wall.
5. **Lane N CLOSED**: GDELT is structurally blind on small caps (FARTCOIN: 0
   articles/9d) — historical research only, NEVER a live path. A detached
   fetcher may still be draining `W-N_cache_gdelt.json`; free full rerun:
   `W-N1_precedence.py` then `W-N2_replay.py`.
6. **Config leftovers** (21e7503): kill −40→−12 (−40 was drift from acf2786,
   unreachable on $39); late_chase_relax ripped (W-M 624-cell flat refute).

## YOUR immediate queue, in order

1. **Watch unlock_short_runin's first opens** (SEI first). Verify: claim
   registered, DSL hard_timeout ≈ hours-to-unlock (exits AT the event, never
   through it), backup SL 15%, unlock_recorder still writing the shadow arm.
   Hard review at 10 episodes: fwd EV25<0 → shadow_only=true, no debate.
2. **Grade ledgers as they mature** (`python scripts/shadow_status.py`):
   funding_spike @15 eps, thin_short_relax @30 entries, majors_swing ~07-17,
   mover_pass / b15_up / unlock arms / news_catalyst as they hit n. Promotion
   = EV25>0 both halves. Demotion is symmetric.
3. **Web-search calibration**: at ~15 graded `web_search_used:true` verdicts,
   compare vs the 4,644-episode pre-news baseline (Lane G method). If web
   search doesn't fix the anti-calibrated long band, longs stay capped.
4. **Dated re-runs**: W-F4 OI quadrant 2026-07-30; weekend gap-fade ~2026-10-20.
5. **Operator nudges**: VPS (Mac slept 194.8h/15d — the single biggest free
   EV gain), re-fund decision (he chose to stay at $39 for now; at $100:
   kill −25, book clips $30).

## Traps that already bit us — do not relearn

- **`import scripts.trading_loop` STARTS THE LIVE LOOP** (no main guard). It
  spawned a second live instance today (killed in 30s, 0 fills). Use
  `ast.parse` for syntax checks. Never import it, never run two loops.
- `candleSnapshot` returns the bar CONTAINING t → lookahead in naive
  backtests. Fixed grader + W-U1 handle it; new backtests must.
- Manual trades pollute main-engine attribution and fill the notional cap.
  `pnl_by_book.py` is exact-first (book_open events); to split bot-vs-manual,
  match fills against `Trade result` log lines ±3min.
- One dex failing to fetch fakes a huge equity loss — check heartbeat
  dex_equity before believing any crash. "Position vanished" after a clean
  dex query is usually a manual close, not a bug.
- Mid-day restarts re-baseline startOfDayEquity (kill laundering) — SOD flush
  helps, still verify after every restart.
- New live books MUST join `_ACTIVE_CLAIM_BOOKS` (rebalancer_owned.py — else
  claims silently denied) AND `BOOK_PRIORITY` (pnl_by_book.py — else
  attribution lost).
- pytest is state-isolated via conftest, but avoid the full suite while the
  loop is mid-trade; use a worktree for paranoid runs.
- GDELT: 1 req/5s, plain-text 429s. Google News RSS is the live news source.
- Sweep stop-width {8-40%} on mean-reversion edges — tight stops invert real
  edges (rally_exhaustion lesson).

## Standing operator rules

- **AUTO-FLIP ORDER (2026-07-12, "make everything live if it's ev+ expected
  go")**: when shadow_status grades a book VALIDATED (EV25>0 both halves at
  its bar), flip it live the SAME DAY at $20/1x with a kill criterion —
  commit, restart, inform; do not re-ask. Symmetric: REFUTED → shadow the
  same day. PENDING books never blanket-flip.

- Evidence promotes; the operator can order live flips — implement BOUNDED
  ($20/1x + kill criterion) and state plainly which parts the evidence does
  not support (precedent: today's unlock flip shipped the validated arm only).
- Tests in the same commit, commit+push immediately, then say exactly what to
  restart (`scripts/restart.sh loop` for code; config is hot-read).
- Julien: numbers first, minimal text, no AI vocabulary, no em dashes, end
  with the next action.

## Where everything lives

- Ledgers/grading: `.state/shadow_ledger/`, `scripts/shadow_status.py`
- Attribution: `scripts/pnl_by_book.py --days N`
- Research: `research/alpha_swarm/` (W-<letter><n> naming; pre-register
  cells, matched same-coin random-time nulls ≥2000, OOS halves, 25bps+funding,
  survivorship caveat). KillaXBT archive: `research/killa_xbt/`.
- Journal: `.monitor-journal.md` (gitignored). Screen: `pathia-stack-live`.
- Memory (Claude-side): `~/.claude/projects/.../memory/MEMORY.md` mirrors most
  of this if you need deeper history.

Torch is yours. The system is healthier than the PnL looks: measurement is
trustworthy now, every live edge has a kill switch, and the ledgers are
accruing the evidence that decides what scales. Protect the $39, grade
ruthlessly at the bars — in both directions.
