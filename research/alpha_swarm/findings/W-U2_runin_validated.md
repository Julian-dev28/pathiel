# W-U2 — unlock run-in: VALIDATED on a proper backtest

**Date:** 2026-08-30
**Book:** `unlock_short_runin` (LIVE)

## Why this re-run was owed

The book was live on W-U1's `EXPL_runin_Tm3_T` cell. W-U1's own docstring calls
that cell "EXPLORATORY (reported, not promotable without a re-run)", and the
code earns the label: it is a **raw close-to-close drift** with no trade
construction — no stop, no fees, no matched null, no OOS split. Its headline
-2.105% is a price move, not a strategy return.

Meanwhile W-U1's PRIMARY *pre-registered* cell (short T-1d -> T+2d) returned
**p_mc = 0.10 and fails the 0.05 bar**.

So the cell that was tested properly failed, and the cell the live book trades
had never been tested at all. The forward ledger carried only n=14.

## The re-run

Run-in window as an actual trade: short close(T-3d) -> close(T), 15% stop
checked on the daily HIGH (a spike through the stop counts even if the close
recovers), 25bps round trip. Matched same-coin random-day null, OOS time halves.

| | |
|---|---|
| n | **408** |
| mean | **+1.466%** net of fees |
| win | 0.578 |
| OOS first half | +0.951% |
| OOS second half | +1.980% |
| mc_p | **0.0040** |

Both halves positive and the second is STRONGER — no decay, which is the
failure mode that killed the session-sweep candidate (W-SESS1) and the
main_engine trigger (W-ME1).

p=0.0040 clears 0.05 outright and survives a Bonferroni correction over the
dozen-odd cells W-U1 and W-U2 examined between them.

## Verdict

VALIDATED. The book stays live, now on n=408 with a real null instead of n=14
forward and an untested drift number.

The lesson is about the shape of the original error, not the outcome: a raw
drift statistic looked like a strategy result, and nobody had multiplied it by a
stop and a fee. It happened to survive that multiplication. It did not have to.

Reproduce: `research/alpha_swarm/hypotheses/W-U2_runin_rerun.py`.
