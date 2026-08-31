# KillaXBT methodology research

Operator-ordered research program (SPEC.md, 2026-07-11). Research-only: nothing
here touches live behavior. Two lanes:

- **Lane K (this document's audit sections)** — SPEC Parts 1, 2, 8, 12:
  prediction audit, methodology reconstruction, grading framework.
- **Lane K2 (quantitative)** — SPEC Parts 3-7, 9: range-structure features,
  walk-forward validation. See `validation_results.json` /
  `validation_report.md` (owned by the quantitative lane).

## Files

| file | what | owner |
|---|---|---|
| `calls.json` | 14 structured prediction records + machine grades + audit counts | K |
| `methodology.json` | 10 evidence-based methodology components | K |
| `scripts/research_killa_xbt.py` | deterministic grader (schema validation, Part-8 rules, selftest) | K |
| `daily_majors.json` | BTC/ETH/SOL 1d Hyperliquid candles 2020-08..2026-07 (grading + backtests) | shared cache |
| `pathia/indicators/range_structure.py`, `scripts/backtest_range_structure.py`, `tests/test_range_structure.py`, `validation_results.json`, `validation_report.md` | quantitative half | K2 |

Repro:

```
.venv/bin/python scripts/research_killa_xbt.py --selftest   # 9 synthetic grader checks
.venv/bin/python scripts/research_killa_xbt.py              # regrade calls.json in place
```

## Part 1 — Prediction audit (Jul 2024 - Jul 2026)

### How the sample was built, and why it is NOT a complete population

Direct X scraping fails from this environment; nitter and archive.org/archive.ph
are blocked. What works: `api.fxtwitter.com/<user>/status/<id>` returns live
tweet text/date/engagement (source_quality **direct**), and status IDs were
harvested from news articles and web search. Consequences, all recorded in
`calls.json meta`:

- **Survivorship is severe.** Every record was reached through media coverage,
  and the media covers him *because* the May-2025 call hit. Misses that no
  article quoted are invisible to this method.
- **Jul 2024 - Apr 2025 is a hard gap.** No recoverable calls at all.
- **Chart-only levels are article-mediated.** The famous May-2025 chart's
  numeric path comes from article descriptions, not the image.
- **Deleted/edited posts are uncheckable** (fxtwitter returns live state only).

Per SPEC Part 1: **no headline win rate is published**. The sample is not
complete and cannot be made complete without X API access.

### Counts (14 records, graded 2026-07-11)

| bucket | n | ids |
|---|---|---|
| machine-graded | 9 | K01-K03, K05, K07, K08, K10-K12 |
| fully testable (strict-gradable) | 2 | K02 (MISS), K08 (HIT) |
| partially testable | 2 | K01 (path 3/4), K02 |
| directionally testable (researcher-imposed windows, flagged) | 2 | K03 (HIT), K10 (HIT) |
| pending / open (no expiry or window not closed) | 4 | K05, K07, K11, K12 |
| unverifiable / vague / post-hoc — NO grade | 5 | K04, K06, K09, K13, K14 |

Source quality: 8 direct (fxtwitter), 5 article_quote, 1 unverified (headline
only). Zero records rest on screenshots.

### Strict vs relaxed grades (rules fixed in `research_killa_xbt.py`, Part 8)

- **Strict** (numeric target + author-stated horizon + stated primary + no
  invalidation-first, no adverse-first): **1 HIT / 1 MISS / 7 not gradable.**
  - HIT — K08 (2026-04-01): "another 10-15% downside before a macro bottom" —
    band $57.6k-$61.0k traded 2026-06-05, inside the chop-until-September window.
  - MISS — K02 (2025-06-12): 114-116K monthly target, "bullish unless below
    100K"; BTC traded below 100K on 2025-06-22 before ever tagging 114K
    (self-invalidated under his own stated level).
- **Relaxed / component grades:**
  - K01 (2025-05-13 chart, 15.4M views): path 120k-up -> HIT 2025-07-14;
    100k-down -> HIT 2025-11-04; 70k-down -> HIT 2026-02-05; 50k-down ->
    PENDING (post-trigger low $57.8k). **3 of 4 stages, in order.** Levels
    article-mediated, no expiry — impressive but not strict-gradable.
  - K03 (2025-12-10, the SPEC Part-12 lead — verified direct, actual date
    Dec 10): "Redistribution range... timebased capitulation before the next
    leg down" -> direction HIT (-31.4% over a flagged, researcher-imposed 180d
    window). Range/path language matched the tape; no levels to grade strictly.
  - K10 (2026-06-01): "I think we go lower. I'm buying anyway" -> lower leg
    HIT (-16.0% over flagged 30d window); $160k leg open.
  - K05/K07/K11 ($50-55k / $52k capitulation targets): PENDING — BTC's low to
    2026-07-10 is $57.77k; the signature capitulation level has NOT printed.
  - K12 ("$100K not before 2027"): PENDING_ON_TRACK.

### Part 12 leads — resolution

1. **Dec 2025 redistribution call**: VERIFIED direct via fxtwitter
   (status 1998834473975063001, posted 2025-12-10T19:17Z, not Dec 11). Text as
   attributed. Graded path/direction separately (above).
2. **Attributed May-2025 long-cycle chart**: provenance FOUND —
   status 1922185937666109504, 2025-05-13T07:03Z ("Your welcome. I have just
   saved you all. $BTC"). Existence/date/virality are direct; the ~$120ks-top /
   long-decline path detail remains article-mediated (grade confidence medium).

### Honest bottom line on the audit

The recoverable record shows a genuinely early, directionally-correct
bear-market roadmap (K01, K03) plus disciplined stance-taking with explicit
invalidations — and also shows that the one call in the sample with a crisp
author-stated level+horizon (K02) missed by its own invalidation, and the
signature $50k capitulation target has not printed. With n(strict)=2 and
severe survivorship, no skill statistic is defensible. What IS defensible:
his *framework* is coherent and repeatedly evidenced (see below).

## Part 2 — Methodology reconstruction

Full component records with evidence, confidence, and quantifiability:
`methodology.json`. Summary:

| component | evidence | confidence | quantifiable |
|---|---|---|---|
| HTF regime/cycle first | direct (K03, K10) | high | partially (state machine) |
| Range geometry, fade extremes, avoid mid-chase | direct (K03, K06) | high | yes |
| Deviation ("scam wick") vs acceptance | direct (K06, K09) | high | yes |
| Time-in-structure / time-based targets | direct+article (K03, K12, K10) | high | partially (context features) |
| "Rotational market mathematics" swing counts | article-only | **low — not reconstructible; do not implement** | no |
| First-retest rejection prior | direct (K09) | medium | yes (untested) |
| Scenario pathing + invalidation levels | direct (K02, K07) | high | as audit protocol |
| Spot-accumulation vs leverage separation | direct (K10, K08) | high | risk mgmt, not alpha |
| Positioning/sentiment overlays | direct (K13) | medium | needs external data |
| Cycle-math break thesis | direct (K14) | high he holds it | multi-year |

The SPEC's initial hypothesis (HTF regime + range geometry +
deviation-vs-acceptance + time-in-structure + scenario pathing + avoiding
mid-range entries) is **confirmed as a description of his public process**.
Whether its mechanical translation carries edge is the quantitative lane's
question — see `validation_results.json` (walk-forward answer as of
2026-07-11: the translatable components carried **no** net-of-cost edge vs
matched nulls on this repo's data).

<!-- Quantitative lane (K2) owns everything below this line -->
