"""Tests for the correlation-regime gate (agents/corr_gate.py).

Covers:
- avg_pairwise_corr: high for identical coins, low for uncorrelated
- compute_corr_regime: correct regime classification + scalar direction
- Neutral scalars when data is thin or history is empty
- cap enforcement
"""
from hermes_trader.agents.corr_gate import avg_pairwise_corr, compute_corr_regime, CorrRegimeState


def _bars(closes):
    return [{"t": i, "o": c, "h": c, "l": c, "c": c, "v": 1e6}
            for i, c in enumerate(closes)]


def _noisy_bars(n, pattern=None, daily_ret=None):
    """Build bars with a pattern (list) or constant return. Needs variance for Pearson."""
    if pattern is None and daily_ret is not None:
        # Use a sinusoidal pattern instead of constant so there's variance
        import math
        pattern = [daily_ret * math.sin(i * 0.5) for i in range(n)]
    p = 100.0
    closes = [p]
    for r in (pattern or [0.0] * n):
        p *= (1 + r)
        closes.append(p)
    return _bars(closes)


def _shared_pattern_bars(n, scale=1.0):
    """Bars driven by the same noisy pattern (scale multiplied). High corr when same scale."""
    import math
    pattern = [scale * 0.02 * math.sin(i * 0.7) for i in range(n)]
    p = 100.0
    closes = [p]
    for r in pattern:
        p *= (1 + r)
        closes.append(p)
    return _bars(closes)


# ── avg_pairwise_corr ─────────────────────────────────────────────────────────

def test_avg_corr_high_for_strongly_correlated_coins():
    """Coins all driven by the same noisy pattern → avg pairwise corr ≈ 1.0."""
    n = 40
    bars = _shared_pattern_bars(n, scale=1.0)
    # All same bars → all returns identical → Pearson undefined (returns 0 for degenerate)
    # Use slightly different levels but same pattern
    cbc = {}
    for i in range(6):
        cbc[f"C{i}"] = _shared_pattern_bars(n, scale=(1.0 + i * 0.01))  # near-identical
    avg = avg_pairwise_corr(cbc, window=20)
    assert avg is not None
    assert avg > 0.95


def test_avg_corr_lower_for_mixed_coins():
    """A universe with mix of positively and negatively driven coins should have lower avg corr."""
    n = 40
    # half driven positively, half negatively → negative cross-correlations drag avg down
    cbc = {}
    for i in range(3):
        cbc[f"UP{i}"] = _shared_pattern_bars(n, scale=1.0 + i * 0.01)
    for i in range(3):
        cbc[f"DN{i}"] = _shared_pattern_bars(n, scale=-(1.0 + i * 0.01))
    avg = avg_pairwise_corr(cbc, window=20)
    # up vs down is negative corr → average dragged below 1.0
    assert avg is not None
    assert avg < 0.5


def test_avg_corr_none_when_too_few_coins():
    """Fewer than 4 eligible coins → None."""
    n = 40
    bars = _shared_pattern_bars(n, scale=1.0)
    cbc = {"C1": bars, "C2": bars}   # only 2 coins
    assert avg_pairwise_corr(cbc, window=20) is None


def test_avg_corr_none_when_bars_too_short():
    """Bars shorter than 80% of window → coin excluded → too few → None."""
    short_bars = _bars([100.0] * 3)
    cbc = {f"C{i}": short_bars for i in range(6)}
    assert avg_pairwise_corr(cbc, window=20) is None


# ── compute_corr_regime ───────────────────────────────────────────────────────

def test_corr_regime_neutral_when_no_history():
    """With empty history, regime should be neutral (scalars = 1.0)."""
    n = 40
    cbc = {f"C{i}": _shared_pattern_bars(n, scale=1.0 + i * 0.01) for i in range(6)}
    state = compute_corr_regime(cbc, history=[], window=20)
    assert state.momentum_scalar == 1.0
    assert state.vol_disp_scalar == 1.0


def test_corr_regime_neutral_when_thin_universe():
    """Too few coins → avg_corr=None → neutral scalars."""
    bars = _bars([100.0] * 30)
    cbc = {"C1": bars, "C2": bars}
    state = compute_corr_regime(cbc, history=[0.3, 0.4, 0.5, 0.4, 0.3] * 3, window=20)
    assert state.momentum_scalar == 1.0
    assert state.vol_disp_scalar == 1.0


def test_corr_regime_high_corr_boosts_vol_disp():
    """In high-corr regime, vol_disp_scalar > 1.0, momentum_scalar < 1.0."""
    n = 40
    # Coins nearly identical pattern → high pairwise corr
    cbc = {f"C{i}": _shared_pattern_bars(n, scale=1.0 + i * 0.005) for i in range(6)}

    # History full of LOW corr values → current high corr is above median → corr_high=True
    history = [0.1, 0.15, 0.12, 0.13, 0.11] * 4  # median ≈ 0.12; current will be ~0.99
    state = compute_corr_regime(cbc, history=history, window=20, low_scalar=1.2, high_scalar=1.2)
    assert state.corr_high is True
    assert state.vol_disp_scalar > 1.0
    assert state.momentum_scalar < 1.0


def test_corr_regime_low_corr_boosts_momentum():
    """In low-corr regime, momentum_scalar > 1.0, vol_disp_scalar < 1.0."""
    # Half coins driven positively, half negatively → low avg pairwise corr
    n = 40
    cbc = {}
    for i in range(3):
        cbc[f"U{i}"] = _shared_pattern_bars(n, scale=1.0 + i * 0.01)
    for i in range(3):
        cbc[f"D{i}"] = _shared_pattern_bars(n, scale=-(1.0 + i * 0.01))

    # History full of HIGH corr values → current low corr is below median → corr_high=False
    history = [0.9, 0.85, 0.88, 0.91, 0.87] * 4
    state = compute_corr_regime(cbc, history=history, window=20, low_scalar=1.2, high_scalar=1.2)
    assert state.corr_high is False
    assert state.momentum_scalar > 1.0
    assert state.vol_disp_scalar < 1.0


def test_corr_regime_cap_enforced():
    """scalar must be ≤ cap regardless of low_scalar / high_scalar setting."""
    n = 40
    cbc = {f"C{i}": _shared_pattern_bars(n, scale=1.0 + i * 0.005) for i in range(6)}
    history = [0.1] * 20   # always low → corr_high=False → momentum gets boosted
    state = compute_corr_regime(
        cbc, history=history, window=20,
        cap=1.1, low_scalar=5.0, high_scalar=5.0,  # aggressive scalars should be capped
    )
    assert state.momentum_scalar <= 1.1
    assert state.vol_disp_scalar <= 1.1


def test_corr_regime_returns_dataclass():
    """Return type is always CorrRegimeState (not None)."""
    n = 40
    cbc = {f"C{i}": _shared_pattern_bars(n, scale=1.0 + i * 0.01) for i in range(6)}
    state = compute_corr_regime(cbc, history=[], window=20)
    assert isinstance(state, CorrRegimeState)
