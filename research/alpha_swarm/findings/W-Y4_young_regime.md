# W-Y4 — young_mover_short regime overlay: equity-index-7d gate VALIDATED

Lane Y, 2026-07-22. Scripts: `hypotheses/W-Y4_extract_episodes.py` (cohort from
the live block log), `W-Y4_fetch.py` (caches: cohort 1h, xyz:SP500 / xyz:XYZ100 /
BTC 1h+1d, real HL fundingHistory), `W-Y4_regime_backtest.py` (analysis).
Data: `W-Y4_episodes.json`, `W-Y4_cache_1h.json` (merged with the W-Y
cache), `W-Y4_cache_index.json`, `W-Y4_cache_funding.json`. Results:
`W-Y4_results.json`.

## Question (pre-registered)

The 2026-07-20 retrospective slice behind `_macro_regime()` claimed the
young history-floor-blocked short pays +6.03%/ep at 85% win when the equity
index is UP over the prior 7d, and only +0.18%/48% when down (n=55/71).
PRIMARY gate under test, fixed before running: **xyz:SP500 7-calendar-day
return > 0**, computed strictly PIT from COMPLETED daily closes before the
block's UTC day. Everything else (7/14/20d, level-vs-slope, XYZ100, BTC,
the live 1h EMA tag, intraday tape) is a secondary descriptive variant.

## Method

Cohort = every `history_floor_preflight` (coin, UTC day) episode in
`logs/trading_loop.log`, first block ts of the day — the exact population the
LIVE `young_mover_short` book trades. 150 episodes (2026-06-28..07-22),
137 simulated (13 dropped: incomplete 24h forward windows on 07-21/22, all on
gate-fail days — no selection against the gate), 125 xyz across 28 coins +
12 crypto. Entry = open of first 1h bar at/after block ts, SHORT, exit =
close at entry+24h (2h tolerance). Costs 25 bps/side; real funding summed
over the held window (short receives +rate). Live-geometry variant: 6% stop
off the 1h high path. Nulls: 2000-iter matched same-coin random-entry-time
portfolios — null A unconditional, null B restricted to gate-pass days.
Day-cluster permutation on the observed block days. Seed 20260722.

## (a) Reproduction — the split SURVIVES strict PIT

xyz-only, day <= 2026-07-20 (n=122):

| eq7 regime | n | gross | net@25 | win (net) | claimed (07-20 slice) |
|---|---:|---:|---:|---:|---|
| UP | 45 | **+6.02%** | **+5.52%** | 73% (78% gross) | +6.03% / 85% (n=55) |
| DOWN | 77 | **-2.76%** | -3.26% | 35% | +0.18% / 48% (n=71) |

Gross up-side reproduces to a hundredth of a point. Counts differ (45/77 vs
55/71) — the original almost certainly included the FORMING daily bar in the
7d return; strict PIT (completed closes only) moves ~10 episodes across the
boundary. The down side is WORSE than claimed (-2.76% gross, not +0.18%),
which strengthens the gate: the forgone trades are not flat, they lose.

## (b) Regime-signal variants (full xyz sample, n=125, net@25)

| signal | up n / EV / win | down n / EV / win | gap |
|---|---|---|---:|
| **eq7>0 (primary)** | 45 / +5.52% / 73% | 80 / -3.29% / 34% | **+8.81pp** |
| x100_7>0 (XYZ100) | 30 / +6.98% / 87% | 95 / -2.36% / 36% | +9.34pp |
| lvl20 (close>SMA20) | 86 / +1.94% / 59% | 39 / -4.64% / 23% | +6.58pp |
| eq14>0 | 67 / +2.39% / 61% | 58 / -3.01% / 33% | +5.40pp |
| btc7>0 | 69 / +1.21% / 55% | 56 / -1.75% / 39% | +2.96pp |
| eq20>0 | 95 / +0.52% / 58% | 30 / -2.13% / 17% | +2.65pp |
| live1h=up (the `_macro_regime` tag) | 57 / +0.52% / 46% | 68 / -0.65% / 50% | +1.18pp |
| intraday tape>0 | 61 / +0.16% / 51% | 64 / -0.38% / 45% | +0.54pp |

- **Best variant: the 7d daily-close slope.** XYZ100-7d has the widest gap
  (+6.98%/87% up) but 33% less flow (n=30) and is a post-hoc variant
  (Bonferroni x9 applies); eq7 was pre-registered and is nearly as clean.
  Slope beats level (lvl20 up-side only +1.94%); 7d beats 14d beats 20d —
  the signal decays fast.
- **The live `macro_regime` 1h EMA20/50 tag does NOT carry the edge** (up
  subset +0.52%, win 46%). Its 3-state detail: up +0.52% / neutral +0.45% /
  down -2.43% — usable only as a weak down-veto. The gate must compute the
  7d daily return; enforcing on the existing tag would enforce noise.
- Intraday same-day tape ~ nothing — consistent with the
  reverse-refuted-audit's same-day split finding no regime dependence. The
  dependence lives at the 7d horizon, which that audit never tested.

## (c) GATE spec

**Condition (xyz equities, the live arm):** at signal time compute
`eq7 = C[-1]/C[-8] - 1` over COMPLETED xyz:SP500 daily closes (drop the
forming bar). Open the live short only if `eq7 > 0`. On fetch failure treat
as FAIL (fail-closed: pooled ungated EV is ~0, down-regime EV is -3.3%, so an
unknown regime forgoes ~nothing and dodges the tail). Crypto arm (zero
capital) mirrors with BTC 7d, evidence n=12 only.

**Code hook** (`hermes_trader/agents/mover_recorders.py`,
`record_young_mover_short`): the book already tags `macro_regime` via
`_macro_regime()` — that tag stays for continuity but is NOT the gate
(validated useless above). Add:

```python
def _equity_index_7d() -> Optional[float]:
    try:
        bars = fetch_hl_candles("xyz:SP500", "1d", 12)
        closes = [b.c for b in bars[:-1]]          # completed bars only
        return closes[-1] / closes[-8] - 1 if len(closes) >= 8 else None
    except Exception:
        return None
```

- record ALWAYS, gate only the live leg:
  `eq7 = _equity_index_7d()`; add to ledger meta
  `"eq_idx_7d": eq7, "regime_gate": "pass" if (eq7 or 0) > 0 else "fail"`;
  then `live = live and bool(cfg_gate) and (eq7 is not None and eq7 > 0)`
  where `cfg_gate = live_cfg.get("regime_gate_eq7", True)` (new config key
  under `mover_recorders.young_short_live`, hot-killable).
- Grading stays two-sided forever:
  `shadow_status.py --book young_mover_short --meta regime_gate=pass` (the
  live policy) vs `--meta regime_gate=fail` (the counterfactual the gate
  forgoes). Zero-capital rows keep accruing on fail days.

## (d) Verdict: PROMOTE (enforce the gate on the live book)

Gate = eq7>0, xyz-only, full sample (n=45 taken / 80 forgone, 24 days):

| metric | value |
|---|---|
| EV net@25+funding | **+5.56%/ep** (funding worth +4bp; 4 delisted-endpoint coins funding=0) |
| EV at the live 6%-stop geometry | +4.68%/ep |
| win | 73% (median +5.42%) |
| OOS halves (time split) | +4.34% (n=22) / +6.65% (n=23) — both +, both n<30 |
| matched null A (any-time) | mc_p **0.0005**, excess **+5.05pp** |
| matched null B (gate-pass-time only) | mc_p **0.0005**, excess **+3.45pp** — block-day timing adds real alpha beyond regime |
| day-cluster permutation (6 of 15 block days) | p **0.0115** — survives at day granularity; 6 up-days is the honest effective n (<30 flag) |
| non-overlapping episodes | +4.96%/ep (n=40) |
| ex-top-coin (ZHIPU, +49.4pp/5 eps) | +4.98%/ep, 72% win, 19 coins |
| day-equal-weight EV | +6.53%/day; ALL 6 up-days positive (+1.6..+14.0%); 7 of 9 down-days negative |
| flow | takes 13.1/wk, forgoes 23.3/wk (forgone EV -3.29%/ep — the gate's veto EARNS ~+0.8%/ep-forgone in avoided loss) |
| ungated book (status quo) | -0.07%/ep net+funding (n=125, win 48%) |

**Forward out-of-sample confirmation, zero fitting:** every one of the 14
gradeable ledger rows since the book went LIVE 2026-07-20 fired on an
eq7-FAIL day. The gate would have blocked ALL of them. They graded
**-10.07%/row** (24h-close grading; ZHIPU -37.1%, KIOXIA -25.0%, SKHY -15.0%
— live losses smaller under the 6% backup stop, same sign). The gate's veto
side is already forward-validated on the book's own live bleed. Gate state
today (07-22): FAIL — the book should currently be dormant.

Time-clustering check: up-days are NOT one wave — three clusters (07-01;
07-09/10; 07-13/15/16) interleaved with down days, and the PnL flips sign
WITH the flag day-by-day (07-13 +1.6 / 07-14 -2.8 / 07-15 +4.6 / 07-16
+14.0 / 07-17 -1.1).

## (e) Caveats

- **Effective independent sample is 6 up-days / 15 block days in ONE 24-day
  window** (one xyz listing wave, semis/AI-hardware tape). The day-perm
  p=0.0115 is the honest significance; the episode-level mc_p 0.0005
  overstates independence. n<30 flags: up-days (6), OOS halves (22/23),
  forward rows (14), crypto arm (12).
- The retrospective's +0.18% down-side was optimistic; strict PIT grades the
  down side -3.3%/ep. Nothing in the promoted direction relies on the
  original slice's arithmetic.
- Grading geometry vs live: 24h-close, next-1h-bar entry vs the DSL's
  timeout/backup-stop path; the 6%-stop variant (+4.68%) bounds the gap.
- Survivorship: cohort = coins the loop scanned AND that still resolve on the
  candle endpoint; log starts 2026-06-28. xyz:DRAM/KIOXIA/MRVL/NOK returned
  zero funding rows (renamed/delisted endpoints) — funding treated as 0
  there.
- 9 regime variants were examined; only eq7 was pre-registered, its p stands
  un-haircut. XYZ100-7d needs its own forward evidence before replacing eq7.
- Crypto (bare) episodes: +9.32%/ep descriptive on n=12 — keep the crypto
  arm zero-capital; do not arm it off this.
- Config path today: `mover_recorders.young_short_live` has no regime key —
  enforcement requires the one-line gate + config default above (live-code
  change, out of scope for this READ-ONLY lane; ledger meta addition is the
  same diff).

## Decision rule going forward

Wire `regime_gate_eq7=true` on the live leg. Review at +30 forward gate-pass
episodes OR 2026-08-22: demote to shadow if forward gate-pass EV@25 < 0;
re-examine XYZ100-7d as the gate signal if its forward `--meta` split beats
eq7 by >2pp on n>=20.
