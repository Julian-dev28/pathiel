# Lane V report — news vs TA (SKHX case), 2026-07-13

Full findings: research/alpha_swarm/findings/W-V_news_vs_ta.md

## Headline answers

1. **The SKHX shorts were news-BLIND, not news-overruled.** Both SHORT
   verdicts (07-12 23:04 conf 0.72, 07-13 03:06 conf 0.73) researched with
   news_context = "no news". Google News is queried as `"SKHX" stock` and the
   title-relevance guard requires the ticker in the headline — SK Hynix
   coverage never says "SKHX". No xyz ticker→company alias map exists
   (news_catalyst.py `_SYM_ALIASES` is crypto majors only). At 03:06 web
   search DID fire (move −12.1% >= 8%) and surfaced SK Hynix context; the
   model still shorted the profit-taking dump and still emitted news_risk
   "none". Neither short executed (runner gate: shorts disabled).
2. **History cannot answer "news vs TA": UNDERSAMPLED.** 5,845 research
   events since 06-19; 3,417 directional. The session log never stored the
   headline string; polar news_risk fired 9x ever. Gradeable: ALIGNED n=3,
   CONFLICT n=0, NEUTRAL n=1 (bar: 30/cell). No verdict faked.
3. **The xyz conflict set (part 2) is EMPTY for a structural reason**: the
   news pipeline can't see xyz catalysts (2 of 126 xyz reads in the memory
   window had any real news), so a news/TA conflict can never be recorded on
   the class where the operator's question lives — until the alias fix.
4. **Forward recorder wired** (the real product): shadow book
   `news_ta_quadrant` tags every directional verdict researched with real
   news as aligned/conflict/neutral; grades itself via shadow_status.
   Pre-registered rule at n>=30/quadrant (findings doc §4).

## Quadrant table

| cell | n graded | mean fwd net25 | win | null | p | flag |
|---|---|---|---|---|---|---|
| ALIGNED | 0 (3 pending, <25h old) | — | — | — | — | UNDERSAMPLED |
| CONFLICT | 0 (zero in all history) | — | — | — | — | UNDERSAMPLED |
| NEUTRAL | 0 (1 pending) | — | — | — | — | UNDERSAMPLED |
| NO_NEWS_DATA 24h | 3,410 | −1.54% | 0.38 | −0.14% | 1.000 | baseline |
| NO_NEWS_DATA 72h | 3,389 | −2.54% | 0.39 | +0.01% | 1.000 | baseline |

Side-finding (context, not a kill order): the whole directional-verdict
population underperforms matched same-coin random-time entries at 24h and
72h — includes gate-blocked verdicts; consistent with Lane G (gates save
money, AI longs anti-calibrated).

SKHX so far: the news-blind 07-12 23:04 short is +9.80% gross running
(1415.00 → 1276.30 by 07-13 04:00). The listing was met with distribution —
the TA short is currently RIGHT. Anecdote only; the ledger decides.

## What changed on the live tree (uncommitted, per lane scope)

- `pathia/agents/mover_recorders.py`: `classify_news_polarity()` +
  `record_news_ta_quadrant()` (1d horizon, 15% stop, dedup coin/UTC-day,
  hot-kill mover_recorders.enabled).
- `scripts/trading_loop.py`: import + call right after the research
  log_event, before route_verdict mutates the verdict (last_price backfilled
  from universe mid).
- `tests/test_mover_recorders.py`: +3 tests (polarity determinism incl. the
  SKHX-class headline, all-three-quadrant tagging, skip/dedup/hot-kill).

Tests: full suite `.venv/bin/python -m pytest tests/ -q
--ignore=tests/test_e2e_live.py` → **621 passed, 12 deselected, 0 failed**
(+3 new tests in test_mover_recorders.py; that file is 9/9 green). Nothing trades; no config/gate
changes; loop untouched otherwise. trading_loop.py verified via ast.parse
(never imported — no main guard). Everything left UNCOMMITTED per lane
orders. Note: research/alpha_swarm/hypotheses/W-N_cache_gdelt.json shows
modified in git status — that is the known detached Lane-N GDELT fetcher
still draining, not this lane.
