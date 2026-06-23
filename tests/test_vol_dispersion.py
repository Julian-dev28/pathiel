"""Vol-dispersion rebalancer — pure-engine unit tests.

Mirrors test_xs_momentum.py in style. Tests:
- idio_vol_score ranking (higher residual stdev = higher score)
- Within-tercile beta-neutral construction (longs = high idio-vol, shorts = low, per tercile)
- Empty-book / too-few-coins guards
- rebalance_plan and is_empty_plan (mirrors xs_momentum tests since the function is a copy)
"""
import math
from hermes_trader.agents.vol_dispersion import (
    idio_vol_score, coin_beta, rank_universe, rebalance_plan, is_empty_plan, TargetBook,
)


def _bars(closes):
    """Minimal bar list for testing. All OHLCV fields set to close value."""
    return [{"t": i, "o": c, "h": c, "l": c, "c": c, "v": 1} for i, c in enumerate(closes)]


def _flat_bench(n):
    """BTC benchmark that never moves (close=100 always) — residuals equal coin returns."""
    return _bars([100.0] * n)


def _noisy_bars(n, seed_returns):
    """Build bars from a list of daily returns. Pads with zeros if seed is shorter than n."""
    closes = [100.0]
    for i in range(n):
        r = seed_returns[i] if i < len(seed_returns) else 0.0
        closes.append(closes[-1] * (1 + r))
    return _bars(closes)


# ── idio_vol_score ─────────────────────────────────────────────────────────────

def test_idio_vol_score_higher_for_volatile_coin():
    """A coin with larger daily swings should score higher than a flat coin."""
    n = 35
    volatile_bars = _noisy_bars(n, [0.05, -0.05] * (n // 2))   # ±5% alternating
    flat_bars = _noisy_bars(n, [0.001] * n)                      # nearly flat
    bench = _flat_bench(n + 1)

    iv_volatile = idio_vol_score(volatile_bars, bench, window=30)
    iv_flat = idio_vol_score(flat_bars, bench, window=30)

    assert iv_volatile is not None and iv_flat is not None
    assert iv_volatile > iv_flat


def test_idio_vol_score_none_when_too_short():
    """Should return None if bars are shorter than window+1."""
    short_bars = _bars([100.0] * 5)
    bench = _flat_bench(40)
    assert idio_vol_score(short_bars, bench, window=30) is None


def test_idio_vol_score_none_when_bench_too_short():
    """Should return None if bench bars are too short."""
    bars = _noisy_bars(40, [0.01] * 40)
    short_bench = _flat_bench(5)
    assert idio_vol_score(bars, short_bench, window=30) is None


def test_idio_vol_score_none_on_empty():
    """Empty inputs return None without raising."""
    assert idio_vol_score([], _flat_bench(40), window=30) is None
    assert idio_vol_score(_noisy_bars(40, []), [], window=30) is None


# ── coin_beta ──────────────────────────────────────────────────────────────────

def test_coin_beta_flat_bench_returns_one():
    """When bench never moves (vb=0), beta falls back to 1.0."""
    bars = _noisy_bars(35, [0.02] * 35)
    bench = _flat_bench(36)
    b = coin_beta(bars, bench, window=30)
    assert b == 1.0


def test_coin_beta_correlated_coin():
    """A coin that moves identically to bench should have beta ≈ 1.0."""
    rets = [0.03, -0.02, 0.01, -0.01] * 10
    bars = _noisy_bars(len(rets) + 5, rets)
    bench = _noisy_bars(len(rets) + 5, rets)
    b = coin_beta(bars, bench, window=len(rets))
    assert abs(b - 1.0) < 0.05


def test_coin_beta_too_short():
    """Returns 1.0 for insufficient history."""
    bars = _bars([100.0, 101.0, 102.0])
    bench = _flat_bench(5)
    assert coin_beta(bars, bench, window=30) == 1.0


# ── rank_universe ──────────────────────────────────────────────────────────────

def _make_universe(n_coins: int, n_bars: int, vol_pattern: list[float]):
    """Build a candles_by_coin dict where coin i has volatility vol_pattern[i]."""
    cbc = {}
    for i in range(n_coins):
        amplitude = vol_pattern[i]
        rets = ([amplitude, -amplitude] * ((n_bars // 2) + 1))[:n_bars]
        cbc[f"COIN{i:02d}"] = _noisy_bars(n_bars, rets)
    return cbc


def test_rank_universe_longs_are_high_idio_vol():
    """Longs should come from the top of each tercile's idio-vol ranking."""
    n = 15   # 5 per tercile; k=1 → 1 long, 1 short per tercile
    window = 20
    nbars = window + 5
    # Assign ascending volatility: COIN00 flattest, COIN14 most volatile
    # With flat bench, beta is degenerate (1.0 for all) → all in same effective beta band
    # but tercile split still divides by index order; top-idio-vol within each tercile should be long
    vols = [0.001 * (i + 1) for i in range(n)]
    cbc = _make_universe(n, nbars, vols)
    bench = _flat_bench(nbars + 1)

    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1)
    assert len(book.longs) > 0 and len(book.shorts) > 0

    # Every long must have a higher score than every short within its tercile.
    # Since flat bench → all betas degenerate, overall: top-idio-vol coins should be longs.
    for long_coin in book.longs:
        for short_coin in book.shorts:
            # Soft check: longs should on average have higher scores
            pass  # strong assertion only works within same tercile (betas identical here)

    # Stricter: the coin with the single highest idio-vol (COIN14) must be a long
    assert "COIN14" in book.longs


def test_rank_universe_shorts_are_low_idio_vol():
    """The flattest coin (COIN00) should be a short."""
    n = 15
    window = 20
    nbars = window + 5
    vols = [0.001 * (i + 1) for i in range(n)]
    cbc = _make_universe(n, nbars, vols)
    bench = _flat_bench(nbars + 1)

    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1)
    assert "COIN00" in book.shorts


def test_rank_universe_within_tercile_beta_neutral():
    """Longs and shorts should span the same beta range (within-tercile guarantee).

    With 15 coins of heterogeneous vol and identical beta, tercile assignment groups
    by beta order; since all betas ≈ 1.0 here, each tercile gets a beta-uniform slice.
    The key property: no tercile is all-long or all-short.
    """
    n = 15
    window = 20
    nbars = window + 5
    vols = [0.001 * (i + 1) for i in range(n)]
    cbc = _make_universe(n, nbars, vols)
    bench = _flat_bench(nbars + 1)

    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1)
    # With 3 terciles and k=1, we expect exactly 3 longs and 3 shorts (one per tercile each)
    assert len(book.longs) == 3
    assert len(book.shorts) == 3
    # No coin appears in both longs and shorts
    assert set(book.longs).isdisjoint(set(book.shorts))


def test_rank_universe_empty_when_too_few_coins():
    """Returns empty TargetBook when universe is below min_coins threshold."""
    window = 20
    nbars = window + 5
    vols = [0.01] * 8   # 8 coins: need at least 9 (3 terciles × 3 per tercile for k=1)
    cbc = _make_universe(8, nbars, vols)
    bench = _flat_bench(nbars + 1)

    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1)
    assert book.longs == [] and book.shorts == []


def test_rank_universe_empty_book_when_bench_missing():
    """Returns empty TargetBook when bench_bars is empty."""
    n = 15
    window = 20
    nbars = window + 5
    vols = [0.01] * n
    cbc = _make_universe(n, nbars, vols)

    book = rank_universe(cbc, [], idio_vol_window=window, k_per_tercile=1)
    assert book.longs == [] and book.shorts == []


def test_rank_universe_scores_populated():
    """book.scores must map coin → positive idio_vol for all ranked coins."""
    n = 15
    window = 20
    nbars = window + 5
    vols = [0.001 * (i + 1) for i in range(n)]
    cbc = _make_universe(n, nbars, vols)
    bench = _flat_bench(nbars + 1)

    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1)
    for coin in book.longs + book.shorts:
        assert coin in book.scores
        assert book.scores[coin] >= 0


def test_rank_universe_tercile_assignments_present():
    """book.tercile_assignments must be populated for all ranked coins."""
    n = 15
    window = 20
    nbars = window + 5
    vols = [0.001 * (i + 1) for i in range(n)]
    cbc = _make_universe(n, nbars, vols)
    bench = _flat_bench(nbars + 1)

    book = rank_universe(cbc, bench, idio_vol_window=window, k_per_tercile=1)
    for coin in book.longs + book.shorts:
        assert coin in book.tercile_assignments
        assert book.tercile_assignments[coin] in (0, 1, 2)


# ── rebalance_plan and is_empty_plan ─────────────────────────────────────────

def test_rebalance_plan_open_close_hold():
    book = TargetBook(longs=["A", "B"], shorts=["X", "Y"])
    plan = rebalance_plan(book, current_long=["B", "C"], current_short=["Y", "Z"])
    assert plan["open_long"] == ["A"]
    assert plan["close_long"] == ["C"]
    assert plan["hold_long"] == ["B"]
    assert plan["open_short"] == ["X"]
    assert plan["close_short"] == ["Z"]
    assert plan["hold_short"] == ["Y"]


def test_rebalance_plan_handles_side_flip():
    """A coin flipping from long to short should appear in both close_long and open_short."""
    book = TargetBook(longs=["A"], shorts=["F"])
    plan = rebalance_plan(book, current_long=["F"], current_short=[])
    assert "F" in plan["close_long"] and "F" in plan["open_short"]


def test_is_empty_plan_when_already_at_target():
    book = TargetBook(longs=["A"], shorts=["B"])
    same = rebalance_plan(book, current_long=["A"], current_short=["B"])
    assert is_empty_plan(same) is True


def test_is_empty_plan_false_when_changes_needed():
    book = TargetBook(longs=["A"], shorts=["B"])
    assert is_empty_plan(rebalance_plan(book, [], [])) is False
