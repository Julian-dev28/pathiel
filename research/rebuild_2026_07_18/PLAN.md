# REBUILD PLAN — consolidated from 4-agent swarm (2026-07-18)
Full evidence: PNL_FORENSICS.md, FEE_VIABILITY.md, DEMOLITION_MANIFEST.md, MINIMAL_SYSTEM.md

## The convergent diagnosis (4 independent reads)
- Bot did ~95% of the $260->$18 damage; manual trading was a rounding error (-$12).
- Fees 57% of net bleed: $471k notional churned, 2,721 fills, all taker, 3.07bps avg.
- THE split: holds <2h net -$385.59 (602 eps) vs holds >=2h net +$134.96.
- Main engine (AI research path) = the churner: -$83.02 of the last -$91.52 (91%);
  80 closes/wk at median hold 54min, 5-7x over the ~36 RT/wk churn budget.
- Ops disasters ~$170 (BIRD liq, zombie-wake, killswitch storm) — infra class, fixes shipped/underway.
- Surviving edges clear fee breakeven 15-50x at >=6h holds. Fees per episode were never the problem.

## Decision menu (operator sign-off, in order of impact)
1. BOOKS-ONLY: main-engine entries OFF (AI keeps close-checks only). Hot-read, reversible.
2. KILL 4 modules + config blocks: news_catalyst_live (REFUTED), majors_swing_live
   (never validated, 300% notional exposure), young_listings_live (W-Y1 refuted),
   sizing.py (orphaned). -1,100 lines.
3. mover_pass back to SHADOW (live at n=19 vs pre-registered bar 30 — should never have flipped).
4. Kill switch rescale to -15% of SOD equity (fixes unreachable -$100 + SOD-laundering).
5. RE-FUND decision: $80 min for sane per-stop risk; ~$150 expresses full validated set.
   Measured EV ceiling: ~$3.35/wk at $19; ~$11.80/wk at $100. Software cannot beat this at current size.
6. v2 migration per MINIMAL_SYSTEM.md (7 modules, staged, never leaves money unmanaged).

## Standing-order amendment proposed
Nothing trades live pre-VALIDATED. VALIDATED -> live $20/1x + kill (unchanged).
PENDING never flips live regardless of enthusiasm (this is what put news_catalyst
and mover_pass live pre-verdict).

## Already fixed this session (infra class)
claude_cli brain (402 outage), partial-dex equity fake-loss (3 layers), DSL entry-basis
on adds (P0), boot LaunchAgent installed (BLOCKED on operator FDA grant for /bin/bash).

## Dated bars unchanged
whale_flow final 07-26 (interim REFUTED n=82) · W-F4 frozen re-run 07-30 ·
unlock_short kill bar @10 eps (now 6) · crash_continue re-table 08-15.
