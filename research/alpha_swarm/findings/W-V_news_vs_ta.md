# W-V — News vs price action (the SKHX case)

Lane V, 2026-07-13. Operator question: the AI shorted xyz:SKHX on bearish TA
at conf 0.73 while the tape was SK Hynix's record $26.5B US listing — is NEWS
stronger than PRICE ACTION?

## The SKHX case, reconstructed from the session log

Two SHORT verdicts, neither executed (runner gate: "shorts disabled" —
zero capital moved):

| ts (UTC) | verdict | conf | 24h move at read | news_context | news_risk | web search |
|---|---|---|---|---|---|---|
| 07-12 23:04 | SHORT | 0.72 | −1.4% | **"no news"** | none | not triggered (move < 8%) |
| 07-13 03:06 | SHORT | 0.73 | −12.1% | **"no news"** | none | fired; citations incl. Bloomberg on the leveraged ETF around "a key AI memory stock" + an SK Hynix post |

The operator's framing ("AI weighed TA over news") is not what happened at
23:04 — **the AI never saw the news**. Root cause is deterministic and sits in
`pathia/agents/news_catalyst.py`:

1. `_coin_query("xyz:SKHX")` queries Google News for `"SKHX" stock`. The
   listing coverage says "SK Hynix", never "SKHX".
2. `_title_relevant()` (the 18d596c relevance fix) then REQUIRES the ticker in
   the title (cashtag / ALL-CAPS / symbol-word + equity context). Correct for
   crypto homonyms, but it makes every xyz equity whose ticker differs from
   its common name **structurally news-dark**. `_SYM_ALIASES` maps crypto
   majors only (BTC→bitcoin...); there is no xyz ticker→company map.
3. At 03:06 the web-search arm DID surface SK Hynix context (it triggers at
   |move|≥8%, so only after the dump was already −12%) and the model still
   said SHORT 0.73, reading the tape as "profit-taking dump ongoing, no
   catastrophe" — and still emitted news_risk "none", so nothing downstream
   could even see a news/TA conflict.

xyz news-darkness is class-wide, not SKHX-specific: of the 126 xyz research
events inside the memory window, **2** had a real news_context (xyz:MU,
xyz:DRAM — tickers that ARE the words headlines use).

## 1. Historical quadrant backtest (W-V0/W-V1) — honest limits first

What history can and cannot answer:

- The session log holds every research event since 06-19 (5,845; 3,417
  directional LONG/SHORT) with verdict, confidence, ts, news_risk — but **it
  never carried the news_context headline string** (0 occurrences in 102MB).
- news_context survives only in the rolling last-200 window of
  `.agent-memory.json` (23 events with real news at extraction time).
- Polar news_risk (positive/negative) has fired **9 times ever**, all
  07-11 onward (before the 07-12 relevance fix most of it tainted anyway),
  and 8 of the 9 were PASS/hold verdicts.
- GDELT backfill for small caps is structurally blind (findings/
  W-N_news_replay.md) — per-coin historical news cannot be reconstructed.

Tags: ALIGNED = polar news matches verdict side; CONFLICT = opposes; NEUTRAL
= real news with no polarity; NO_NEWS_DATA = no usable news info (the
reference pool, NOT evidence of "no news existed"). Classifier =
`mover_recorders.classify_news_polarity` (news_risk wins when polar, else
deterministic keyword polarity) — the same code the forward recorder runs.

### Quadrant table (directional verdicts, signed fwd return net 25bps, matched same-coin same-side random-time nulls, bootstrap 2000)

| cell | n graded | mean fwd net25 | win | null mean | p(null>=obs) | flag |
|---|---|---|---|---|---|---|
| ALIGNED 24h/72h | 0 (3 events, all <25h old, grades pending) | — | — | — | — | **UNDERSAMPLED** |
| CONFLICT 24h/72h | 0 (zero events in all recorded history) | — | — | — | — | **UNDERSAMPLED** |
| NEUTRAL 24h/72h | 0 (1 event, pending) | — | — | — | — | **UNDERSAMPLED** |
| NO_NEWS_DATA 24h | 3,410 | **−1.54%** | 0.38 | −0.14% | 1.000 | baseline |
| NO_NEWS_DATA 72h | 3,389 | **−2.54%** | 0.39 | +0.01% | 1.000 | baseline |

The four polar/neutral events (each will grade within days):
07-12 04:19 ARB LONG 0.75 ALIGNED (news_risk) · 07-12 22:21 XPL SHORT 0.74
NEUTRAL · 07-13 00:13 ETH LONG 0.76 ALIGNED (keywords, weak headline) ·
07-13 03:14 xyz:MU SHORT 0.74 ALIGNED (keywords, "SK Hynix Falls / Memory
Stocks Decline").

Baseline side-finding (real, n=3.4k): the AI's directional verdicts as a
POPULATION underperform matched same-coin same-side random-time entries at
both horizons (p=1.0 means every one of 2,000 null bootstrap means beat the
observed mean). Caveats before anyone panics: this population counts every
verdict INCLUDING the ones the gates then blocked (the gates are separately
forward-validated to save money — blocked entries −1.68%/ep, Lane G), it is
survivorship-bounded to today's universe, and it echoes what we already
measured (anti-calibrated AI longs). It is context for the quadrants, not a
new kill order.

**Verdict on the operator's question from history: UNDERSAMPLED — n(ALIGNED)=3,
n(CONFLICT)=0, n(NEUTRAL)=1 against a bar of 30 per cell.** History cannot
answer whether news beats TA; there is no faked verdict here. The only
population history grades at scale is NO_NEWS_DATA — the AI's ordinary
TA-driven verdicts — which serves as the baseline the forward quadrants will
be compared against.

## 2. The SKHX class (xyz + mega-catalyst conflict set)

Every xyz research event with news_risk positive + SHORT (or negative +
LONG), full recorded history: **zero events.** Broadened to keyword polarity
over the memory window: still zero conflicts — the only polar-news xyz event
is xyz:MU SHORT with NEGATIVE news ("SK Hynix Falls In Seoul... US Memory
Stocks Decline"), i.e. ALIGNED.

The conflict set is empty for a structural reason, not a behavioral one: the
news pipeline cannot see xyz catalysts (above), so news_risk is 'none' on
effectively every xyz read and no conflict can ever be recorded. SKHX itself
enters history as NO_NEWS_DATA. The forward ledger seeds from zero.

SKHX shorts graded so far (1h HL candles, PIT entry = next-bar open):
- 07-12 23:04 short (never executed): entry-ref next-bar open 1415.00 →
  last cached close 1276.30 = **+9.80% gross running for the short** as of
  07-13 04:00 UTC. 24h grade completes 07-13 23:04.
- 07-13 03:06 short: entry bar not closed yet at fetch time; pending.

So the operator's instinctive framing cuts the OTHER way so far: the
news-blind TA short is winning — the record listing was met with
distribution, and "short the post-listing profit-taking dump" is exactly the
unlock_short/W-U1 shape ("done by the event"). One anecdote, zero evidence
weight; the quadrant ledger is what settles it.

## 3. What is wired forward (the real product)

`record_news_ta_quadrant(analysis, config)` in
`pathia/agents/mover_recorders.py`, called from
`scripts/trading_loop.py` right after the research log event (before
route_verdict can mutate the verdict). For every LONG/SHORT verdict researched
with a REAL news_context it records to shadow ledger book
**`news_ta_quadrant`**: hypothetical trade in the verdict direction, 1d
horizon, 15% stop, entry_ref = last_price/mid, meta {quadrant:
aligned|conflict|neutral, news_polarity, polarity_source, news_risk,
confidence, web_search_used}. Dedup one row per coin per UTC day. Hot-kill:
`mover_recorders.enabled=false`. Zero capital; `scripts/shadow_status.py`
auto-discovers the book. Tests in `tests/test_mover_recorders.py`
(same-commit), full suite green.

Known blind spot carried forward, on purpose: verdicts whose news_context is
"no news" (like SKHX itself) record nothing — the recorder measures the
news-vs-TA question only where news is actually visible. Fixing xyz
visibility is a separate, live-path change (below), not smuggled in here.

## 4. Pre-registered forward decision rule (fixed before any grading)

At n>=30 graded episodes in BOTH the aligned and conflict quadrants
(shadow_status EV25, both OOS halves populated):

- If **EV25(CONFLICT) < EV25(ALIGNED) − 1.0%** per episode → news information
  dominates: wire a news-conflict veto/downweight as a recorder-backed gate
  (block or halve-size directional verdicts that oppose polar news), itself
  shipped shadow-first.
- If **EV25(CONFLICT) >= EV25(ALIGNED)** → TA dominates / news is priced in:
  do NOT build a news veto; close the question.
- In between, or if either OOS half flips sign → extend to n>=60, no action.
- NEUTRAL quadrant is the within-book control; if ALIGNED does not beat
  NEUTRAL by >0 at its own n>=30, "news polarity" carries no signal here and
  the veto idea dies regardless of the conflict cell.

## Follow-ups (proposed, NOT built — live-path changes outside this lane)

1. **xyz ticker→company alias map** in `news_catalyst.py` (`_SYM_ALIASES`
   pattern: SKHX→"SK Hynix", KIOXIA→"Kioxia", ARM→"Arm Holdings", ...) +
   equity-context relevance. Without it the quadrant recorder accrues ~zero
   xyz rows and the SKHX class stays unmeasurable. Half-day, needs its own
   tests + the W-N3 taint lesson (restart epoch, don't grade pre-fix rows).
2. Web-search trigger is move-gated (|24h|>=8%), i.e. catalyst-CHASING by
   construction — it can never warn before the move. An unlock/news-calendar
   pre-trigger is the only pre-move path (W-N4 covers the live wire).

## Files

- `research/alpha_swarm/hypotheses/W-V0_extract.py` / `W-V0_events.json`
- `research/alpha_swarm/hypotheses/W-V1_quadrant_backtest.py` /
  `W-V1_results.json` / `W-V_cache_1h.json`
- Live (uncommitted, this lane): `pathia/agents/mover_recorders.py`,
  `scripts/trading_loop.py`, `tests/test_mover_recorders.py`
