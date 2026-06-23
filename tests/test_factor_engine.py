"""Tests for the generalized factor engine (vol_dispersion.py with pluggable score_fn).

Covers:
- sortino_score: correct computation, None on short history, positive skew → high score
- amihud_score: correct direction (high |ret|/vol → high score), None on missing volume
- rank_universe with score_fn="sortino" and score_fn="amihud"
- Original idio_vol API still works (backward-compatibility)
- Unknown score_fn gracefully falls back to idio_vol
"""
import math
from hermes_trader.agents.vol_dispersion import (
    idio_vol_score, sortino_score, amihud_score, kurtosis_score,
    rank_universe, rebalance_plan, is_empty_plan, TargetBook,
    _SCORE_FNS,
)


def _bars(closes, volumes=None):
    """Minimal bar list. OHLCV fields. volumes defaults to 1e6 (liquid)."""
    if volumes is None:
        volumes = [1_000_000.0] * len(closes)
    return [{"t": i, "o": c, "h": c, "l": c, "c": c, "v": v}
            for i, (c, v) in enumerate(zip(closes, volumes))]


def _flat_bench(n):
    return _bars([100.0] * n)


def _noisy_bars(n, seed_returns, volumes=None):
    closes = [100.0]
    for i in range(n):
        r = seed_returns[i] if i < len(seed_returns) else 0.0
        closes.append(closes[-1] * (1 + r))
    if volumes is None:
        volumes = [1_000_000.0] * len(closes)
    return _bars(closes, volumes)


# ── sortino_score ──────────────────────────────────────────────────────────────

def test_sortino_score_positive_drift():
    """A coin with persistent positive returns should have a positive Sortino score."""
    n = 35
    bars = _noisy_bars(n, [0.005] * n)   # 0.5% every day
    bench = _flat_bench(n + 1)
    score = sortino_score(bars, bench, window=30)
    assert score is not None
    assert score > 0


def test_sortino_score_volatile_coin():
    """A coin with alternating returns (±5%) should have a LOWER Sortino than a drifting coin
    (same mean ≈ 0 but high downside deviation)."""
    n = 35
    drift_bars = _noisy_bars(n, [0.003] * n)
    chop_bars = _noisy_bars(n, [0.05, -0.05] * (n // 2))
    bench = _flat_bench(n + 1)
    s_drift = sortino_score(drift_bars, bench, window=30)
    s_chop = sortino_score(chop_bars, bench, window=30)
    assert s_drift is not None and s_chop is not None
    assert s_drift > s_chop


def test_sortino_score_none_when_too_short():
    bars = _bars([100.0] * 5)
    bench = _flat_bench(40)
    assert sortino_score(bars, bench, window=30) is None


def test_sortino_score_bench_ignored():
    """sortino_score bench_bars is accepted but not used — result same for different bench."""
    n = 35
    bars = _noisy_bars(n, [0.01, -0.005] * (n // 2))
    bench1 = _flat_bench(n + 1)
    bench2 = _noisy_bars(n, [0.05] * n)  # very different bench
    s1 = sortino_score(bars, bench1, window=30)
    s2 = sortino_score(bars, bench2, window=30)
    assert s1 is not None and s2 is not None
    assert abs(s1 - s2) < 1e-10   # bench doesn't affect sortino


def test_sortino_score_no_negative_returns():
    """All-positive return history: no downside deviation → returns positive score (not None)."""
    n = 35
    bars = _noisy_bars(n, [0.01] * n)   # always up
    bench = _flat_bench(n + 1)
    score = sortino_score(bars, bench, window=30)
    assert score is not None
    assert score > 0


# ── amihud_score ──────────────────────────────────────────────────────────────

def test_amihud_score_high_ret_low_vol_wins():
    """Coin with high |ret| and low dollar-volume should have a HIGHER Amihud score."""
    n = 35
    # illiquid: big moves, small volume → high score
    illiq_bars = _noisy_bars(n, [0.1, -0.1] * (n // 2), volumes=[100.0] * (n + 1))
    # liquid: small moves, large volume → low score
    liquid_bars = _noisy_bars(n, [0.01, -0.01] * (n // 2), volumes=[1_000_000.0] * (n + 1))
    bench = _flat_bench(n + 1)
    s_illiq = amihud_score(illiq_bars, bench, window=30)
    s_liquid = amihud_score(liquid_bars, bench, window=30)
    assert s_illiq is not None and s_liquid is not None
    assert s_illiq > s_liquid


def test_amihud_score_none_when_too_short():
    bars = _bars([100.0] * 5)
    bench = _flat_bench(40)
    assert amihud_score(bars, bench, window=30) is None


def test_amihud_score_none_when_volume_zero():
    """Bars with zero volume should return None (no Amihud ratio can be computed)."""
    n = 35
    bars = _noisy_bars(n, [0.01] * n, volumes=[0.0] * (n + 1))
    bench = _flat_bench(n + 1)
    assert amihud_score(bars, bench, window=30) is None


def test_amihud_score_positive():
    """Amihud score is always non-negative (|ret|/volume ≥ 0)."""
    n = 35
    bars = _noisy_bars(n, [0.05, -0.03] * (n // 2), volumes=[500.0] * (n + 1))
    bench = _flat_bench(n + 1)
    score = amihud_score(bars, bench, window=30)
    assert score is not None and score >= 0


# ── score_fn registry ─────────────────────────────────────────────────────────

def test_score_fn_registry_contains_all():
    assert "idio_vol" in _SCORE_FNS
    assert "sortino" in _SCORE_FNS
    assert "amihud" in _SCORE_FNS


# ── rank_universe with score_fn param ─────────────────────────────────────────

def _make_universe(n_coins, n_bars, patterns, volumes=None):
    """Build candles_by_coin where coin i follows patterns[i]."""
    cbc = {}
    for i, pattern in enumerate(patterns):
        vols = (volumes[i] if volumes else None)
        cbc[f"COIN{i:02d}"] = _noisy_bars(n_bars, pattern, volumes=vols)
    return cbc


def test_rank_universe_sortino_longs_high_sortino():
    """With sortino score_fn: coin with best return/downside-dev should be a long."""
    n = 15
    window = 20
    nbars = window + 5
    # COIN14 has the best Sortino (persistent small positive drift, no downside)
    # COIN00 has the worst (persistent negative returns)
    patterns = []
    for i in range(n):
        if i == 14:
            patterns.append([0.008] * nbars)    # steady positive
        elif i == 0:
            patterns.append([-0.005] * nbars)   # steady negative
        else:
            patterns.append([0.002, -0.003] * (nbars // 2 + 1))  # mixed
    cbc = _make_universe(n, nbars, patterns)
    bench = _flat_bench(nbars + 1)

    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1, score_fn="sortino")
    assert len(book.longs) > 0 and len(book.shorts) > 0
    # The best Sortino coin (COIN14) should be a long; worst (COIN00) should be a short
    assert "COIN14" in book.longs
    assert "COIN00" in book.shorts


def test_rank_universe_idio_vol_backward_compatible():
    """Default score_fn="idio_vol" gives same result as old API (backward-compatible)."""
    n = 15
    window = 20
    nbars = window + 5
    vols = [0.001 * (i + 1) for i in range(n)]
    patterns = [([amp, -amp] * (nbars // 2 + 1))[:nbars] for amp in vols]
    cbc = _make_universe(n, nbars, patterns)
    bench = _flat_bench(nbars + 1)

    book_default = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1)
    book_explicit = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1, score_fn="idio_vol")
    assert book_default.longs == book_explicit.longs
    assert book_default.shorts == book_explicit.shorts


def test_rank_universe_unknown_score_fn_falls_back():
    """Unknown score_fn should fall back to idio_vol without crashing."""
    n = 15
    window = 20
    nbars = window + 5
    vols = [0.001 * (i + 1) for i in range(n)]
    patterns = [([amp, -amp] * (nbars // 2 + 1))[:nbars] for amp in vols]
    cbc = _make_universe(n, nbars, patterns)
    bench = _flat_bench(nbars + 1)

    # Should not raise — falls back to idio_vol
    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1, score_fn="UNKNOWN")
    assert isinstance(book, TargetBook)


def test_rank_universe_amihud_needs_volume():
    """Amihud rank_universe returns empty book when all bars have zero volume."""
    n = 15
    window = 20
    nbars = window + 5
    # All zero volume → amihud_score returns None for all coins → empty book
    zero_vols = [0.0] * (nbars + 1)
    patterns = [[0.01] * nbars for _ in range(n)]
    vol_lists = [zero_vols for _ in range(n)]
    cbc = _make_universe(n, nbars, patterns, volumes=vol_lists)
    bench = _flat_bench(nbars + 1)

    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1, score_fn="amihud")
    assert book.longs == [] and book.shorts == []


# ── kurtosis_score ─────────────────────────────────────────────────────────────

def test_kurtosis_score_fat_tailed_coin_higher_than_normal():
    """A coin with occasional large jumps (fat tails) should score higher kurtosis than a smooth coin."""
    n = 70
    # fat-tailed: mostly 0% but occasional ±10% spikes
    spiky_rets = [0.0] * n
    for i in range(0, n, 10):
        spiky_rets[i] = 0.10
        if i + 1 < n:
            spiky_rets[i + 1] = -0.10
    spiky_bars = _noisy_bars(n, spiky_rets)
    # normal-ish: persistent small alternating returns (thin tails)
    smooth_bars = _noisy_bars(n, [0.01, -0.01] * (n // 2))
    bench = _flat_bench(n + 1)
    k_spiky = kurtosis_score(spiky_bars, bench, window=60)
    k_smooth = kurtosis_score(smooth_bars, bench, window=60)
    assert k_spiky is not None and k_smooth is not None
    assert k_spiky > k_smooth


def test_kurtosis_score_positive_for_fat_tails():
    """Excess kurtosis > 0 for a fat-tailed distribution (kurtosis > 3 for Gaussian)."""
    n = 70
    # heavy-tailed: many zeros + a few large moves
    rets = [0.0] * n
    for i in range(0, n, 8):
        rets[i] = 0.15
    bars = _noisy_bars(n, rets)
    bench = _flat_bench(n + 1)
    k = kurtosis_score(bars, bench, window=60)
    assert k is not None
    assert k > 0   # fat-tailed → excess kurtosis > 0


def test_kurtosis_score_none_when_too_short():
    bars = _bars([100.0] * 5)
    bench = _flat_bench(70)
    assert kurtosis_score(bars, bench, window=60) is None


def test_kurtosis_score_none_on_empty():
    assert kurtosis_score([], _flat_bench(70), window=60) is None


def test_kurtosis_score_bench_ignored():
    """kurtosis_score bench_bars is accepted but not used — result same for different bench."""
    n = 70
    bars = _noisy_bars(n, [0.02, -0.01] * (n // 2))
    bench1 = _flat_bench(n + 1)
    bench2 = _noisy_bars(n, [0.05] * n)  # very different bench
    k1 = kurtosis_score(bars, bench1, window=60)
    k2 = kurtosis_score(bars, bench2, window=60)
    assert k1 is not None and k2 is not None
    assert abs(k1 - k2) < 1e-10   # bench doesn't affect kurtosis


def test_kurtosis_score_constant_returns_zero_kurtosis():
    """Constant returns → zero variance → degenerate. Returns 0.0 (not None)."""
    n = 70
    bars = _noisy_bars(n, [0.0] * n)   # all returns are 0
    bench = _flat_bench(n + 1)
    k = kurtosis_score(bars, bench, window=60)
    # constant returns have zero variance → returns 0.0 per the degenerate guard
    assert k is not None
    assert k == 0.0


def test_score_fn_registry_contains_kurtosis():
    """kurtosis must be in the _SCORE_FNS registry."""
    assert "kurtosis" in _SCORE_FNS


def test_rank_universe_kurtosis_longs_high_kurtosis():
    """With kurtosis score_fn: coin with fat-tailed returns should land in longs."""
    n = 15
    window = 65
    nbars = window + 5
    patterns = []
    for i in range(n):
        if i == 14:
            # fat-tailed: mostly zeros with occasional big jumps
            p = [0.0] * nbars
            for j in range(0, nbars, 8):
                p[j] = 0.15
            patterns.append(p)
        elif i == 0:
            # thin-tailed: tight alternating (normal-ish)
            patterns.append([0.005, -0.005] * (nbars // 2 + 1))
        else:
            patterns.append([0.003, -0.002] * (nbars // 2 + 1))
    cbc = _make_universe(n, nbars, patterns)
    bench = _flat_bench(nbars + 1)

    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1, score_fn="kurtosis")
    assert len(book.longs) > 0 and len(book.shorts) > 0
    assert "COIN14" in book.longs   # fat-tailed coin must be a long
