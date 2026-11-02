"""Fluke-or-edge statistics. Pure, stdlib only, seeded: the same inputs and
seed always give the same numbers.

The question each function answers, in trading terms:

  wilson_ci         the win rate's 95% range, honest at small n (a 3-for-3
                    record is 44% to 100%, not "100%")
  bootstrap_ci      the range a slice's mean R (or total $) would land in
                    if we could replay the same kind of trades again
  permutation_p     could this slice's mean R differ this much from the
                    other trades by shuffling labels alone? (two-sided)
  sign_flip_p       the same, for one group against zero (no "rest")
  binom_two_sided   is a hit rate different from a coin flip?
  label_slice       EDGE / LEAK / LEANING+ / LEANING- / FLUKE / NOISE

The labels (README "Labels"):

  EDGE      n >= 10, the bootstrap CI of mean R is above 0, and p < 0.05
  LEAK      n >= 10, the CI is below 0, and p < 0.05
  LEANING   n >= 3 and p < 0.2, or 3 <= n < 10 with every trade the same
            sign; "+" or "-" by the sign of mean R
  FLUKE     would have been EDGE/LEAK/LEANING, but dropping the single
            biggest trade flips the sign of mean R: one trade carries it
  NOISE     everything else, and always under n = 3

Nothing under n = 10 is ever EDGE or LEAK (MIN_N_EDGE).
"""
from __future__ import annotations

import math
import random
import zlib
from typing import Callable, List, Optional, Sequence, Tuple

MIN_N_EDGE = 10        # never EDGE/LEAK below this many trades
MIN_N_LEAN = 3         # never LEANING below this many trades
P_EDGE = 0.05
P_LEAN = 0.20
N_BOOT = 2000
N_PERM = 2000
SEED = 20261102

EDGE, LEAK, LEAN_POS, LEAN_NEG, FLUKE, NOISE = "EDGE", "LEAK", "LEANING+", "LEANING-", "FLUKE", "NOISE"
LABELS = (EDGE, LEAK, LEAN_POS, LEAN_NEG, FLUKE, NOISE)


def sub_seed(seed: int, key: str) -> int:
    """A stable per-key seed (crc32, not hash(): Python salts str hashes per
    process), so a slice's numbers do not depend on the order slices run."""
    return (int(seed) * 1_000_003 + zlib.crc32(key.encode())) % (2 ** 32)


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def wilson_ci(k: int, n: int, z: float = 1.959964) -> Tuple[Optional[float], Optional[float]]:
    """Wilson score interval for k wins in n trades. (None, None) at n = 0."""
    if n <= 0:
        return None, None
    p = k / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def quantile(sorted_xs: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile of an already sorted list (numpy's
    default "linear" method)."""
    if not sorted_xs:
        raise ValueError("empty")
    pos = q * (len(sorted_xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def bootstrap_ci(values: Sequence[float], stat: Callable[[Sequence[float]], float] = mean,
                 n_boot: int = N_BOOT, seed: int = SEED, alpha: float = 0.05
                 ) -> Tuple[Optional[float], Optional[float]]:
    """Percentile bootstrap CI of `stat` over `values`: `n_boot` resamples
    with replacement, seeded. One value gives a zero-width interval (there
    is nothing to resample, which is the point: n = 1 says nothing)."""
    xs = list(values)
    if not xs:
        return None, None
    rng = random.Random(seed)
    n = len(xs)
    boots = sorted(stat(rng.choices(xs, k=n)) for _ in range(n_boot))
    return quantile(boots, alpha / 2), quantile(boots, 1 - alpha / 2)


def permutation_p(a: Sequence[float], b: Sequence[float], n_perm: int = N_PERM, seed: int = SEED) -> Optional[float]:
    """Two-sided permutation test of mean(a) - mean(b): the share of label
    shuffles whose |difference| is at least the observed one, with the +1
    correction so p is never 0. `b` empty: the one-sample sign-flip test
    of `a` against zero instead. None when `a` is empty."""
    a, b = list(a), list(b)
    if not a:
        return None
    if not b:
        return sign_flip_p(a, n_perm, seed)
    obs = abs(mean(a) - mean(b))
    pool = a + b
    na, total = len(a), sum(pool)
    n = len(pool)
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_perm):
        sa = sum(rng.sample(pool, na))
        diff = sa / na - (total - sa) / (n - na)
        if abs(diff) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def sign_flip_p(a: Sequence[float], n_perm: int = N_PERM, seed: int = SEED) -> Optional[float]:
    """Two-sided one-sample test of mean(a) against 0: flip each value's sign
    at random (the null: a trade was as likely to go either way)."""
    a = list(a)
    if not a:
        return None
    obs = abs(mean(a))
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_perm):
        s = sum(x if rng.random() < 0.5 else -x for x in a)
        if abs(s / len(a)) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def binom_two_sided(k: int, n: int, p: float = 0.5) -> Optional[float]:
    """Exact two-sided binomial test (the "minlike" method scipy uses): the
    total probability of every outcome no more likely than k."""
    if n <= 0:
        return None
    probs = [math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    pk = probs[k]
    return min(1.0, sum(q for q in probs if q <= pk * (1 + 1e-7)))


def one_trade_flips(rs: Sequence[float]) -> bool:
    """True when dropping the single biggest |R| trade flips (or zeroes) the
    sign of mean R: one trade carries the slice."""
    rs = list(rs)
    if len(rs) < 2:
        return True
    m = mean(rs)
    if m == 0:
        return True
    i = max(range(len(rs)), key=lambda j: abs(rs[j]))
    rest = rs[:i] + rs[i + 1:]
    m2 = mean(rest)
    return m2 == 0 or (m2 > 0) != (m > 0)


def label_slice(rs: Sequence[float], ci: Tuple[Optional[float], Optional[float]], p: Optional[float]) -> str:
    """The label for one slice from its trades' R, the bootstrap CI of mean
    R and the permutation p (module doc)."""
    rs = list(rs)
    n = len(rs)
    if n < MIN_N_LEAN or p is None or ci[0] is None:
        return NOISE
    m = mean(rs)
    base = NOISE
    if n >= MIN_N_EDGE and ci[0] > 0 and p < P_EDGE:
        base = EDGE
    elif n >= MIN_N_EDGE and ci[1] < 0 and p < P_EDGE:
        base = LEAK
    elif m != 0:
        consistent = n < MIN_N_EDGE and (all(r > 0 for r in rs) or all(r < 0 for r in rs))
        if p < P_LEAN or consistent:
            base = LEAN_POS if m > 0 else LEAN_NEG
    if base != NOISE and one_trade_flips(rs):
        return FLUKE
    return base


def is_negative(label: str) -> bool:
    return label in (LEAK, LEAN_NEG)


def is_positive(label: str) -> bool:
    return label in (EDGE, LEAN_POS)


def percentile_of(actual: float, sample: Sequence[float]) -> Optional[float]:
    """Where `actual` sits in `sample`, 0-100 (ties count half)."""
    xs: List[float] = list(sample)
    if not xs:
        return None
    below = sum(1 for x in xs if x < actual - 1e-12)
    ties = sum(1 for x in xs if abs(x - actual) <= 1e-12)
    return 100.0 * (below + 0.5 * ties) / len(xs)
