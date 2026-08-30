# W-SOC2 — social_trending: VALIDATED forward, and why it can never be backtested

**Date:** 2026-08-30
**Book:** `social_trending` (LIVE)

## Verdict

VALIDATED on its own point-in-time forward ledger, graded 2026-08-29 by
`scripts/autonomous_cycle.py`:

| | |
|---|---|
| n | 185 |
| EV @6bps | +1.08% / signal |
| EV @25bps | +0.89% / signal |
| OOS halves | +0.54 / +1.50 |
| mc_p | 0.0005 (floor of a 2000-draw matched null) |

Both halves positive with the second stronger, and it survives a 4x-conservative
fee tier. That clears every clause of the evidence doctrine except one, and the
exception is structural rather than a shortfall in the result.

## Why there is no backtest, and cannot be one

The signal is "a coin entered CoinGecko's search-trending list". **That series
has no retrievable history**, and the module's own header documents the search:

- fxtwitter has no search or timeline endpoint
- CoinGecko `/coins/{id}/history` returns 0.0 for its reddit/social fields
- LunarCrush v4 is 401/paid

So a social-attention signal cannot be reconstructed from cached data at any
price we are willing to pay. The only way to obtain evidence was to record it
forward, which is what produced the 185 signals above.

The grader's own docstring argues this is the STRONGER evidence type: a forward
point-in-time read carries no survivorship bias and no lookahead, which is more
than a backtest on a cached universe can claim.

## The doctrine consequence

This book keeps its capital: the evidence exists, it is forward, and it is
large. But it was accrued under the old rules. Under the current doctrine
(`research/EVIDENCE_DOCTRINE.md`) a NEW signal in this class — no retrievable
history, no existing forward record — cannot be validated and therefore must not
be built. `social_trending` is the last of its kind here, not a template.

## The live arm was written after the grade

Worth recording explicitly: the ledger that graded VALIDATED was produced by a
recorder with no capital path. The live arm was written on 2026-08-30 and built
to the geometry it was GRADED on — long, 1-day horizon, hard stop, no trail —
because a live policy that differs from the graded policy is an ungraded book
wearing a validated book's verdict. `tests/test_live_book_order_path.py` pins
that correspondence.

It has therefore never placed a real order. The forward record from here is the
first evidence of the LIVE policy, and `autonomous_cycle` demotes it the moment
that record turns negative at the verdict fee tier.
