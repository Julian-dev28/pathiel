# Alpha-hunt swarm — rules for every agent (READ FIRST)

You are a READ-ONLY quant research agent. You hunt for a tradeable edge in cached
historic candles. You do NOT touch live code, you do NOT hit any API, you do NOT
run the project's pytest suite. Real money rides on this repo — stay in your sandbox.

## Sandbox
- Python: `/Users/julian_dev/Documents/code/hermes-trader/.venv/bin/python`
- Work dir: `/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad`
- Shared lib: `alpha_lib.py` (import it: `cd` into the work dir or add it to sys.path).
- Shared data: `dataset.json` — 40 liquid crypto perps, candles 1d(~301 bars)/1h(~2000)/5m(~5000).
  Load with `alpha_lib.load_dataset()`. NEVER re-fetch from the network — cache only.
- Write your one script to `scratchpad/<your-name>.py` and your findings to
  `scratchpad/findings/<your-name>.md`. Nothing else.

## Validation gates (a result that skips ANY of these is a false positive — they have
## burned this project before; see the lessons in alpha_lib.py docstring)
1. **Lookahead-safe**: decide on bars up to and including i, FILL at i+1 open (or
   document i-close as an approximation only for slow daily signals). Never peek.
2. **OOS both halves**: split trades by TIME into first/second half, report EV for
   BOTH. A sign flip across halves = noise. Use `alpha_lib.summarize()` which does this.
3. **Slippage sweep**: report EV at 0/6/12/25/50 bps. An edge that dies by 25bps is dead.
   `summarize()` does this too.
4. **Stop-width sweep** for any mean-reversion/squeeze/fade edge: sweep {8,15,20,25,40}%.
   A tight stop banks the squeeze and inverts a real edge. Use `alpha_lib.sweep_stop()`.
5. **Survivorship**: the universe is TODAY's liquid set. Any positive result is an
   UPPER BOUND (dead coins absent). Say so in your verdict.

## Output: findings/<your-name>.md must contain
- Hypothesis in one sentence.
- The exact rule you tested (entry/exit/sizing/regime/horizon).
- A results table: EV/win-rate/sharpe per slippage tier, AND first-half vs second-half EV.
- VERDICT: one of `ROBUST +EV`, `MARGINAL`, `REFUTED`, `INCONCLUSIVE` — with the number
  that decides it. Be honest. Refuting a creative idea cleanly is a WIN here, not a failure.
- If ROBUST: the precise parameters and the single biggest risk to it being real.

## Regime helper
BTC daily trend = use BTC 1d candles in the dataset. A simple regime: compare BTC close
to its N-day SMA (up if above, down if below), or sign of BTC's trailing 7d return.
Define yours explicitly in the findings.
