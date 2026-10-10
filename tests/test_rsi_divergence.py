"""RSI divergence trigger — shadow-mode wiring (surfacing bypass + accrual).

Trigger: `indicators.triggers.rsi_divergence` — price prints a new extreme
while RSI prints the opposite. Guards: min_extreme_atr x ATR price margin,
min_rsi_gap RSI-point margin, zone filter (bull_rsi_max / bear_rsi_min).

Config block `rsi_divergence` (hot-read per scan):
  enabled      — trigger computes + hit appended + bypass armed
  shadow_mode  — trigger KEEPS computing but the surfacing bypass is
                 suppressed, research.py filters the hit out of the prompt
                 summary, and a `[rsi-divergence][SHADOW]` WARNING accrues
                 per fired hit.

Code default = block absent = trigger never runs = byte-identical merge.

All hermetic: no network, no LLM, no fetch. Perception path passes the config
dict directly and patches `_fetch_candles_sync`.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_trader.agents import perception  # noqa: E402
from hermes_trader.agents import research  # noqa: E402
from hermes_trader.agents.config import get_config  # noqa: E402
from hermes_trader.indicators.triggers import rsi_divergence, composite_score  # noqa: E402
from hermes_trader.models.types import Candle  # noqa: E402

_T0 = 1_700_000_000_000
_STEP = 300_000  # 5m


def _c(i, o, h, l, c):
    return Candle(t=_T0 + i * _STEP, o=o, h=h, l=l, c=c, v=1000.0)


# ---------------------------------------------------------------------------
# Pure trigger math
# ---------------------------------------------------------------------------

def _bull_divergence_series():
    """Uptrend, a steep sell-off leg (RSI washout at the trough), a small
    bounce, then a marginal NEW lower low made on much weaker momentum:
    price lower low, RSI higher low. 100 bars."""
    cs = []
    px = 100.0
    for i in range(55):                      # gentle uptrend
        px += 0.2
        cs.append(_c(i, px - 0.2, px + 0.3, px - 0.3, px))
    for i in range(55, 65):                  # steep sell-off leg (strong down momentum)
        px -= 1.2
        cs.append(_c(i, px + 1.2, px + 0.3, px - 0.3, px))
    for i in range(65, 85):                  # small bounce
        px += 0.3
        cs.append(_c(i, px - 0.3, px + 0.3, px - 0.3, px))
    for i in range(85, 100):                 # new low, weak momentum (RSI higher low)
        px -= 0.42
        cs.append(_c(i, px + 0.42, px + 0.3, px - 0.3, px))
    return cs


def _bear_divergence_series():
    """Mirror: downtrend, steep rally leg (RSI euphoria at the high), small
    pullback, marginal NEW higher high on weaker momentum."""
    cs = []
    px = 100.0
    for i in range(55):
        px -= 0.2
        cs.append(_c(i, px + 0.2, px + 0.3, px - 0.3, px))
    for i in range(55, 65):
        px += 1.2
        cs.append(_c(i, px - 1.2, px + 0.3, px - 0.3, px))
    for i in range(65, 85):
        px -= 0.3
        cs.append(_c(i, px + 0.3, px + 0.3, px - 0.3, px))
    for i in range(85, 100):
        px += 0.42
        cs.append(_c(i, px - 0.42, px + 0.3, px - 0.3, px))
    return cs


def _flat_series(n=100):
    return [_c(i, 100.0, 100.05, 99.95, 100.0) for i in range(n)]


def test_bullish_divergence_fires_long():
    hit = rsi_divergence(_bull_divergence_series())
    assert hit["fired"] is True
    assert hit["reason"].startswith("long — bullish RSI divergence")
    assert "RSI" in hit["reason"]
    assert 0 < hit["score"] <= 10


def test_bearish_divergence_fires_short():
    hit = rsi_divergence(_bear_divergence_series())
    assert hit["fired"] is True
    assert hit["reason"].startswith("short — bearish RSI divergence")


def test_flat_series_does_not_fire():
    hit = rsi_divergence(_flat_series())
    assert hit["fired"] is False
    assert hit["score"] == 0


def test_strong_momentum_new_low_does_not_fire():
    """A second leg DOWN made with STRONGER momentum (steeper) is not a
    divergence — RSI makes the lower low too."""
    cs = []
    px = 100.0
    for i in range(50):                      # gentle decline
        px -= 0.2
        cs.append(_c(i, px + 0.2, px + 0.5, px - 0.5, px))
    for i in range(50, 100):                 # accelerating collapse
        px -= 1.2
        cs.append(_c(i, px + 1.2, px + 1.5, px - 0.5, px))
    hit = rsi_divergence(cs)
    assert hit["fired"] is False, f"no divergence expected, got {hit['reason']!r}"


def test_insufficient_history():
    hit = rsi_divergence(_flat_series(30))
    assert hit["fired"] is False
    assert hit["reason"] == "insufficient_history"


def test_weight_zero_no_composite_impact():
    """The trigger is a pure surfacing bypass: weight 0 => adding a fired hit
    cannot change the composite score (no gate recalibration)."""
    cfg = get_config()
    weights = cfg["weights"]
    assert weights.get("rsiDivergence") == 0.0
    base_hits = [{"name": "breakout", "score": 8, "fired": True, "reason": "x"}]
    with_div = base_hits + [{"name": "rsiDivergence", "score": 10, "fired": True,
                             "reason": "long — bullish RSI divergence"}]
    assert composite_score(base_hits, weights) == composite_score(with_div, weights)


# ---------------------------------------------------------------------------
# Perception wiring: enabled gate, bypass, shadow suppression, accrual line
# ---------------------------------------------------------------------------

_MARKET = {"coin": "TEST", "type": "perp"}
_RD_CFG = {"enabled": True, "rsi_period": 14, "lookback": 48,
           "min_rsi_gap": 3.0, "min_extreme_atr": 0.25,
           "bull_rsi_max": 45.0, "bear_rsi_min": 55.0}


def _wire(monkeypatch, five_m):
    """5m candles = `five_m`; 1h = flat (kills the slow-burn triggers)."""
    def _fetch(coin, interval, count, cache_ttl_ms, **kw):
        return _flat_series() if interval != "5m" else five_m
    monkeypatch.setattr(perception, "_fetch_candles_sync", _fetch)


def _scan(monkeypatch, cfg_block, min_score, five_m=None):
    cfg = {**get_config(), "rsi_divergence": cfg_block}
    _wire(monkeypatch, five_m or _bull_divergence_series())
    return perception._scan_single_market(_MARKET, 100.0, cfg, min_score,
                                          None, False, False)


def test_disabled_by_default_no_hit(monkeypatch):
    """Block absent (the merge no-op): no rsiDivergence hit in triggers even
    when the geometry would fire."""
    cfg = get_config()
    _wire(monkeypatch, _bull_divergence_series())
    ok, res = perception._scan_single_market(_MARKET, 100.0, cfg, 0, None, False, False)
    assert ok
    if res is not None:
        assert all(h["name"] != "rsiDivergence" for h in res["triggers"])


def test_enabled_live_bypasses_composite_gate(monkeypatch):
    """enabled + shadow OFF: the fired divergence surfaces the coin even with
    min_score above anything the series scores (bypass)."""
    ok, res = _scan(monkeypatch, dict(_RD_CFG), min_score=99)
    assert ok and res is not None, "live divergence bypass must surface"
    hit = next(h for h in res["triggers"] if h["name"] == "rsiDivergence")
    assert hit["fired"] is True


def test_shadow_on_suppresses_surfacing(monkeypatch):
    """shadow ON: same fired geometry, bypass suppressed -> coin dropped."""
    ok, res = _scan(monkeypatch, {**_RD_CFG, "shadow_mode": True}, min_score=99)
    assert ok and res is None, f"shadow ON must suppress surfacing (got {res})"


def test_shadow_on_logs_accrual_once(monkeypatch, caplog):
    ok, res = _scan(monkeypatch, {**_RD_CFG, "shadow_mode": True}, min_score=0)
    lines = [r for r in caplog.records
             if "[rsi-divergence][SHADOW]" in r.getMessage()]
    assert len(lines) == 1, f"expected exactly one accrual line, got {lines!r}"
    assert lines[0].levelno == logging.WARNING
    msg = lines[0].getMessage()
    assert "TEST" in msg and "bullish RSI divergence" in msg
    assert "log-only" in msg


def test_shadow_off_is_silent(monkeypatch, caplog):
    _scan(monkeypatch, dict(_RD_CFG), min_score=0)
    lines = [r.getMessage() for r in caplog.records
             if "[rsi-divergence][SHADOW]" in r.getMessage()]
    assert lines == []


def test_compute_stays_alive_shadow_on(monkeypatch):
    """shadow ON does not stop the computation: with min_score 0 the coin is
    surfaced and the hit is inspectable (the counterfactual record)."""
    ok, res = _scan(monkeypatch, {**_RD_CFG, "shadow_mode": True}, min_score=0)
    assert ok and res is not None
    hit = next(h for h in res["triggers"] if h["name"] == "rsiDivergence")
    assert hit["fired"] is True and hit["reason"]


# ---------------------------------------------------------------------------
# Prompt side: shadow_mode filters the hit out of the trigger summary
# ---------------------------------------------------------------------------

_FIRED_HIT = {"name": "rsiDivergence", "fired": True, "score": 7.0,
              "reason": ("long — bullish RSI divergence: price lower low "
                         "-1.2% beyond prior, RSI 38 vs 29 (+9.0)")}


def _build_prompt(shadow_mode):
    perception_dict = {"type": "perp", "mid": 9.0, "composite_score": 40,
                       "triggers": [dict(_FIRED_HIT)]}
    snap = {"ema8": None, "ema21": None, "last_close": 9.0}
    real_read = research.read_agent_config
    cfg = {"rsi_divergence": {"enabled": True}}
    if shadow_mode:
        cfg["rsi_divergence"]["shadow_mode"] = True
    try:
        research.read_agent_config = lambda: cfg
        return research._build_user_message(
            "TEST", perception_dict, snap, snap, snap, "0.01%/hr", "no news",
            250.0, [], "LIVE",
        )
    finally:
        research.read_agent_config = real_read


def test_prompt_shadow_off_shows_hit():
    msg = _build_prompt(shadow_mode=False)
    assert "rsiDivergence" in msg


def test_prompt_shadow_on_hides_hit():
    msg = _build_prompt(shadow_mode=True)
    assert "rsiDivergence" not in msg
    assert "bullish RSI divergence" not in msg
