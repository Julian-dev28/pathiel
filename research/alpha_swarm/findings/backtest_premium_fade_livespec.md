# backtest_premium_fade_livespec — the LIVE spec through the live grader

Purpose: give the PENDING `premium_fade_short` shadow book a verdict NOW by replaying the
EXACT live recording logic (z>=2 daily-bucket premium, entry_ref_px = completed close,
5d hold, 20% stop) over the 90d funding history and grading it with the identical
shadow_ledger.grade_records + classify the forward survey uses.

Result (n=108):
  net mean: +1.74% @12bps, +1.60% @25bps, +1.36% @50bps, win .59
  TIME-OOS @12bps: first half = -2.52%, second half = +5.99%
  VERDICT: MARGINAL — edge concentrated in the 2nd (BTC-crash) half; first calm half NEGATIVE.

Reconciliation: WEAKER than the D5 research script's ROBUST +3.67% / OOS both-positive (n=150).
The gap is methodology — D5 used next-open entry + rolling-24h premium-z (more events, cleaner
look); the LIVE book uses completed-close + daily-bucket z. The live-spec read is the honest one.

Implication: the premium-fade is a regime-tilted "short crowded longs while the market falls"
trade. Real average EV, but the calm-regime first half lost money. Correctly shadow-only; the
forward shadow must confirm an up/calm-regime cell before any flip. Survivor universe = upper bound,
so a MARGINAL here is a YELLOW FLAG. See [[project_premium_fade_short]].
