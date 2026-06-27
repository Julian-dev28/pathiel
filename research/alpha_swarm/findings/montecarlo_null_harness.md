# C16 montecarlo_null_harness — reusable null p-value harness (NOT an alpha)

## What it is
`scratchpad/mc_null.py` — a shared significance harness every later Lane-C test imports.
It answers: "could a random selector of the same size, trading the same eligible bars,
have produced this mean by luck?" — the multiple-comparison / data-snooping attack.

## Two nulls
- `shuffle_label_p(observed, pool, n_iter, seed, replace)` — permutation / shuffled-label.
  Null = random entry: draw len(observed) returns from the eligible POOL of same-side /
  same-horizon forward returns, recompute mean, repeat. One-sided p = P(null_mean >=
  obs_mean), with +1 smoothing so p is never exactly 0. Pool carries the tape drift, so
  this is automatically the "excess over random-entry baseline" test the rules demand.
- `block_bootstrap_p(pool_series, k, observed_mean, block_len, n_iter, seed)` — resamples
  contiguous time blocks (wrap-around) to preserve autocorrelation, so clustered /
  overlapping trades don't fake significance the way an i.i.d. shuffle would.

Both return obs_mean, null_mean, excess, z, p_one_sided.

## Self-test (green)
- case1 random signal from pool → p = 0.397 (correctly NOT significant)
- case2 +edge signal (mean shifted +0.03) → p = 0.00025 (significant)
- case3 block-bootstrap: edge mean p = 0.00025, null mean p = 0.48 (correct both ways)
- p never 0 (smoothing).

## VERDICT
**TOOL-READY** — deciding numbers: clean separation (random p≈0.40 vs edge p≈0.0003) on
synthetic ground-truth. Later tests should `import mc_null` and report `p_one_sided` from
BOTH nulls alongside `alpha_lib.summarize`. A backtest that looks +EV but has shuffle-label
p > 0.05 is a mirage.
