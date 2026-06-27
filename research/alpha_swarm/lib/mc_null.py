"""C16 montecarlo_null_harness — reusable null-distribution p-values for the swarm.

NOT an alpha. Every Lane-C test (and ideally every lane) bolts this on to answer the
real question behind a positive backtest: "could a random selector of the same size,
trading the same eligible bars, have produced this mean by luck?" Attacks the
multiple-comparison / data-snooping weakness directly.

Two complementary nulls:
  1. shuffle_label_p  — permutation / shuffled-label. The null is "random entry": draw
     len(observed) returns from the eligible POOL (every candidate bar's same-side,
     same-horizon forward return) and recompute the mean, many times. p = P(null_mean
     >= observed_mean). Strips the directional tape drift because the pool carries it too.
  2. block_bootstrap_p — preserves serial autocorrelation: resample contiguous BLOCKS
     from the time-ordered pool series so clustered/overlapping trades don't inflate
     significance the way an i.i.d. shuffle would.

Usage from another test:
    import mc_null
    res = mc_null.shuffle_label_p(signal_rets, pool_rets, n_iter=5000, seed=0)
    # res['p_one_sided'] small  => signal mean unlikely under random entry
    res2 = mc_null.block_bootstrap_p(pool_series, k=len(signal_rets),
                                     observed_mean=mean(signal_rets), block_len=5)
"""
from __future__ import annotations
import random
import statistics
from typing import Sequence


def _mean(xs: Sequence[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def shuffle_label_p(observed: Sequence[float], pool: Sequence[float],
                    n_iter: int = 5000, seed: int = 0,
                    replace: bool = True) -> dict:
    """Permutation p-value. observed = signal trade returns. pool = all eligible
    random-entry returns (MUST include the observed entries' own returns ideally, or a
    superset representing 'any bar you could have entered'). One-sided P(null >= obs)."""
    obs = list(observed)
    pool = list(pool)
    if not obs or len(pool) < 2:
        return {"n_obs": len(obs), "n_pool": len(pool), "p_one_sided": None,
                "verdict": "thin"}
    k = len(obs)
    obs_mean = _mean(obs)
    rng = random.Random(seed)
    ge = 0
    null_means = []
    for _ in range(n_iter):
        if replace:
            sample = [pool[rng.randrange(len(pool))] for _ in range(k)]
        else:
            sample = rng.sample(pool, min(k, len(pool)))
        m = _mean(sample)
        null_means.append(m)
        if m >= obs_mean:
            ge += 1
    null_mu = _mean(null_means)
    null_sd = statistics.pstdev(null_means) + 1e-12
    return {
        "n_obs": k, "n_pool": len(pool),
        "obs_mean": round(obs_mean, 6),
        "null_mean": round(null_mu, 6),
        "excess": round(obs_mean - null_mu, 6),
        "z": round((obs_mean - null_mu) / null_sd, 3),
        "p_one_sided": round((ge + 1) / (n_iter + 1), 5),  # +1 smoothing, never 0
        "verdict": None,
    }


def block_bootstrap_p(pool_series: Sequence[float], k: int, observed_mean: float,
                      block_len: int = 5, n_iter: int = 5000, seed: int = 0) -> dict:
    """Block-bootstrap null. pool_series is a TIME-ORDERED sequence of per-entry-bar
    returns (random-entry, same side/horizon). Builds a size-k sample by drawing
    contiguous blocks (wrap-around) so autocorrelation is preserved, then compares its
    mean to observed_mean. One-sided P(null_block_mean >= observed_mean)."""
    s = list(pool_series)
    n = len(s)
    if n < block_len + 1 or k < 1:
        return {"p_one_sided": None, "verdict": "thin"}
    rng = random.Random(seed)
    ge = 0
    null_means = []
    n_blocks = (k + block_len - 1) // block_len
    for _ in range(n_iter):
        vals = []
        for _b in range(n_blocks):
            start = rng.randrange(n)
            for j in range(block_len):
                vals.append(s[(start + j) % n])
        vals = vals[:k]
        m = _mean(vals)
        null_means.append(m)
        if m >= observed_mean:
            ge += 1
    null_mu = _mean(null_means)
    null_sd = statistics.pstdev(null_means) + 1e-12
    return {
        "k": k, "n_series": n, "block_len": block_len,
        "obs_mean": round(observed_mean, 6),
        "null_mean": round(null_mu, 6),
        "excess": round(observed_mean - null_mu, 6),
        "z": round((observed_mean - null_mu) / null_sd, 3),
        "p_one_sided": round((ge + 1) / (n_iter + 1), 5),
    }


def _selftest():
    rng = random.Random(42)
    # Pool: zero-centered noise (the "tape").
    pool = [rng.gauss(0.0, 0.05) for _ in range(4000)]

    # Case 1: observed is just a random draw from the same pool -> NOT significant.
    null_signal = [pool[rng.randrange(len(pool))] for _ in range(40)]
    r1 = shuffle_label_p(null_signal, pool, n_iter=4000, seed=1)
    assert r1["p_one_sided"] > 0.05, r1
    print("  case1 random-signal p =", r1["p_one_sided"], "(expect >0.05)  PASS")

    # Case 2: observed has a real +edge (shifted mean) -> significant.
    edge_signal = [rng.gauss(0.03, 0.05) for _ in range(40)]
    r2 = shuffle_label_p(edge_signal, pool, n_iter=4000, seed=2)
    assert r2["p_one_sided"] < 0.01, r2
    print("  case2 edge-signal   p =", r2["p_one_sided"], "(expect <0.01)  PASS")

    # Case 3: block-bootstrap on the same edge mean -> significant; on null mean -> not.
    rb_edge = block_bootstrap_p(pool, k=40, observed_mean=0.03, block_len=5, n_iter=4000)
    rb_null = block_bootstrap_p(pool, k=40, observed_mean=0.0, block_len=5, n_iter=4000)
    assert rb_edge["p_one_sided"] < 0.01, rb_edge
    assert rb_null["p_one_sided"] > 0.10, rb_null
    print("  case3 block edge p =", rb_edge["p_one_sided"], "null p =",
          rb_null["p_one_sided"], " PASS")

    # Case 4: smoothed p never 0.
    assert r2["p_one_sided"] > 0.0
    print("mc_null self-test PASSED.")


if __name__ == "__main__":
    _selftest()
