"""Gate tests for services/review/stats.py: known values, seeding, labels at
their boundaries."""
from __future__ import annotations

import math
import random

import pytest

from services.review import stats as S


# ── Wilson ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("k,n,lo,hi", [
    (5, 10, 0.2366, 0.7634),      # textbook values
    (0, 10, 0.0, 0.2775),
    (10, 10, 0.7225, 1.0),
    (3, 3, 0.4385, 1.0),          # "3 for 3" is not "100%"
])
def test_wilson_matches_known_intervals(k, n, lo, hi):
    a, b = S.wilson_ci(k, n)
    assert a == pytest.approx(lo, abs=1e-4) and b == pytest.approx(hi, abs=1e-4)


def test_wilson_empty_is_none():
    assert S.wilson_ci(0, 0) == (None, None)


# ── binomial ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("k,n,p", [(5, 5, 0.0625), (7, 7, 0.015625), (5, 10, 1.0), (0, 1, 1.0),
                                   (9, 10, 0.021484375)])
def test_binomial_two_sided_exact(k, n, p):
    assert S.binom_two_sided(k, n) == pytest.approx(p, rel=1e-9)


# ── bootstrap ──────────────────────────────────────────────────────────────

def test_bootstrap_is_seeded_and_brackets_the_mean():
    rng = random.Random(1)
    xs = [rng.gauss(1.0, 1.0) for _ in range(50)]
    a = S.bootstrap_ci(xs, seed=7)
    assert a == S.bootstrap_ci(xs, seed=7)
    assert a != S.bootstrap_ci(xs, seed=8)
    assert a[0] < S.mean(xs) < a[1]
    # the percentile CI of a mean is about +-1.96 standard errors
    se = math.sqrt(sum((x - S.mean(xs)) ** 2 for x in xs) / (len(xs) - 1) / len(xs))
    assert (a[1] - a[0]) == pytest.approx(2 * 1.96 * se, rel=0.15)


def test_bootstrap_of_one_value_is_a_point_and_of_nothing_is_none():
    assert S.bootstrap_ci([-1.05]) == (-1.05, -1.05)
    assert S.bootstrap_ci([]) == (None, None)


def test_bootstrap_of_a_sum():
    lo, hi = S.bootstrap_ci([1.0, 2.0, 3.0], stat=sum, seed=3)
    assert 3.0 <= lo <= hi <= 9.0


def test_quantile_matches_linear_interpolation():
    assert S.quantile([1, 2, 3, 4], 0.5) == 2.5
    assert S.quantile([1, 2, 3, 4], 0.0) == 1 and S.quantile([1, 2, 3, 4], 1.0) == 4


# ── permutation / sign flip ────────────────────────────────────────────────

def test_permutation_separates_clearly_different_groups():
    # exact p is 2 / C(20,10) ~ 1e-5: with 2000 shuffles the floor is 1/2001
    p = S.permutation_p([1.0] * 10, [0.0] * 10, n_perm=2000, seed=1)
    assert p == pytest.approx(1 / 2001)


def test_permutation_of_identical_groups_is_one():
    assert S.permutation_p([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], seed=1) == 1.0


def test_permutation_is_seeded():
    a, b = [0.5, -1, 2, 0.1], [-1, -1, 0.3, 2, -1]
    assert S.permutation_p(a, b, seed=4) == S.permutation_p(a, b, seed=4)


def test_permutation_with_no_rest_is_the_sign_flip_test():
    xs = [1.0] * 10       # exact two-sided p = 2/1024
    assert S.permutation_p(xs, [], n_perm=4000, seed=2) == S.sign_flip_p(xs, n_perm=4000, seed=2)
    assert S.sign_flip_p(xs, n_perm=4000, seed=2) == pytest.approx(2 / 1024, abs=0.002)
    assert S.permutation_p([], [1.0]) is None


def test_sub_seed_is_stable_across_processes():
    # crc32, not hash(): this exact number must never change
    assert S.sub_seed(20261102, "cause=size") == S.sub_seed(20261102, "cause=size")
    assert S.sub_seed(1, "a") != S.sub_seed(1, "b")
    assert S.sub_seed(1, "a") == (1 * 1_000_003 + 3904355907) % 2 ** 32


def test_percentile_of_counts_ties_half():
    assert S.percentile_of(0.0, [-1, 0, 1, 2]) == pytest.approx(37.5)
    assert S.percentile_of(5.0, [1, 2]) == 100.0
    assert S.percentile_of(1.0, []) is None


# ── labels ─────────────────────────────────────────────────────────────────

def _label(rs, rest):
    return S.label_slice(rs, S.bootstrap_ci(rs, seed=1), S.permutation_p(rs, rest, seed=1))


def test_n9_is_never_an_edge_however_good():
    rs = [2.0, 2.1, 1.9, 2.2, 2.0, 1.8, 2.3, 2.0, 2.1]
    assert len(rs) == 9
    assert _label(rs, [-1.0] * 30) == S.LEAN_POS


def test_n10_with_a_clear_mean_and_p_is_an_edge():
    rs = [2.0, 2.1, 1.9, 2.2, 2.0, 1.8, 2.3, 2.0, 2.1, 1.7]
    assert _label(rs, [-1.0, 0.5] * 15) == S.EDGE


def test_n10_clearly_negative_is_a_leak():
    rs = [-1.0, -1.1, -0.9, -1.0, -1.2, -0.8, -1.0, -1.0, -0.95, -1.05]
    assert _label(rs, [1.0, 0.5, -0.2] * 10) == S.LEAK


def test_a_ci_clear_of_zero_without_p_is_not_an_edge():
    # mean clearly above 0 but no different from the rest: p fails, so no EDGE
    rs = [1.0, 1.2, 0.8, 1.1, 0.9, 1.0, 1.05, 0.95, 1.0, 1.0]
    assert _label(rs, [1.0, 1.2, 0.8, 1.1, 0.9] * 4) not in (S.EDGE, S.LEAK)


def test_one_and_two_trades_are_noise_even_if_huge():
    assert _label([-1.05], [1.0, 2.0, 0.5]) == S.NOISE
    assert _label([-5.0, -6.0], [1.0, 2.0, 0.5]) == S.NOISE


def test_one_big_trade_carrying_a_slice_is_a_fluke():
    rs = [7.5, -1.0, -1.0, -1.0, -1.1]       # mean +0.68 only because of the 7.5
    assert S.one_trade_flips(rs)
    assert _label(rs, [-1.0, 0.2] * 10) == S.FLUKE


def test_leaning_needs_p_under_0_2_or_a_consistent_sign_under_10():
    assert _label([-1.0, -1.1, -0.9], [1.0, 2.0, 0.5, 1.5]) == S.LEAN_NEG     # all one sign, n=3
    mixed = [0.1, -0.2, 0.15, -0.1, 0.05, 0.0, -0.05, 0.1, -0.1, 0.05, 0.02]
    assert _label(mixed, [0.1, -0.2, 0.15, -0.1] * 5) == S.NOISE
