"""MFI extreme trigger — shadow-mode wiring (surfacing bypass + accrual).

Trigger: `indicators.triggers.mfi_extreme` — volume-weighted RSI (Money Flow
Index) pinned in a washout (long) / euphoria (short) zone. Optional climax
guard: last-bar volume >= vol_confirm_ratio x mean over the MFI window.

Config block `mfi_extreme` (hot-read per scan):
  enabled      — trigger computes + hit appended + bypass armed
  shadow_mode  — trigger KEEPS computing but the surfacing bypass is
                 suppressed, research.py filters the hit out of the prompt
                 summary, and a `[mfi-extreme][SHADOW]` WARNING accrues
                 per fired hit.

Code default = block absent = trigger never runs = byte-identical merge.

All hermetic: no network, no LLM, no fetch. Perception path passes the config
dict directly and patches `_fetch_candles_sync`.
"""
from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_trader.agents import perception  # noqa: E402
from hermes_trader.agents import research  # noqa: E402
from hermes_trader.agents.config import get_config  # noqa: E402
from hermes_trader.indicators.math import mfi  # noqa: E402
from hermes_trader.indicators.triggers import mfi_extreme, composite_score  # noqa: E402
from hermes_trader.models.types import Candle  # noqa: E402

_T0 = 1_700_000_000_000
_STEP = 300_000  # 5m


def _c(i, o, h, l, c, v=1000.0):
    return Candle(t=_T0 + i * _STEP, o=o, h=h, l=l, c=c, v=v)


# ---------------------------------------------------------------------------
# Pure mfi() math
# ---------------------------------------------------------------------------

def _trend(n=60, start=100.0, step=-0.5, v=1000.0):
    cs = []
    px = start
    for i in range(n):
        px += step
        cs.append(_c(i, px, px + 0.3, px - 0.3, px, v))
    return cs


def test_mfi_all_down_flows_is_zero():
    """A pure decline on steady volume: every typical-price flow is negative
    -> MFI collapses to ~0 (all money flowing out)."""
    vals = mfi(_trend(40, step=-0.5), 14)
    warm = [v for v in vals if math.isfinite(v)]
    assert warm, "series should be warm"
    assert warm[-1] < 1.0


def test_mfi_all_up_flows_is_100():
    vals = mfi(_trend(40, step=+0.5), 14)
    warm = [v for v in vals if math.isfinite(v)]
    assert warm[-1] > 99.0


def test_mfi_flat_tp_flows_neutral_or_extreme_bounded():
    """Flat typical prices contribute NO directional flow (TP neither up nor
    down); with the seed window's first bar the smoothed ratio stays bounded
    in [0, 100] and never NaN once warm."""
    cs = [_c(i, 100.0, 100.05, 99.95, 100.0) for i in range(40)]
    vals = mfi(cs, 14)
    warm = [v for v in vals if math.isfinite(v)]
    assert warm
    assert all(0.0 <= v <= 100.0 for v in warm)


def test_mfi_insufficient_history_all_nan():
    cs = _trend(10)
    assert all(math.isnan(v) for v in mfi(cs, 14))


def test_mfi_volume_weighting_differs_from_rsi():
    """Same closes, different volume distribution -> MFI must move, proving
    the volume weighting is live (RSI would be identical). Needs BOTH up and
    down bars: in a one-directional tape one flow side is identically zero
    and the volume mix cannot move the ratio."""
    # zig-zag down-drift: alternating up/down bars, net drift down.
    def zig(vols):
        cs, px = [], 100.0
        for i, v in enumerate(vols):
            px += 0.4 if i % 2 else -0.9
            cs.append(Candle(t=_T0 + i * _STEP, o=px, h=px + 0.3, l=px - 0.3,
                             c=px, v=v))
        return cs
    n = 40
    even = zig([1000.0] * n)
    # heavier volume on the DOWN bars (i even) -> more outflow -> lower MFI
    heavy_down = zig([3000.0 if i % 2 == 0 else 1000.0 for i in range(n)])
    a = [v for v in mfi(even, 14) if math.isfinite(v)][-1]
    b = [v for v in mfi(heavy_down, 14) if math.isfinite(v)][-1]
    assert 0.0 < a < 100.0
    assert b < a - 5.0, f"volume mix must move MFI: even={a:.1f} heavy_down={b:.1f}"


# ---------------------------------------------------------------------------
# Pure trigger math
# ---------------------------------------------------------------------------

def test_oversold_washout_fires_long():
    hit = mfi_extreme(_trend(60, step=-0.5))
    assert hit["fired"] is True
    assert hit["reason"].startswith("long — MFI")
    assert "oversold" in hit["reason"]
    assert 0 < hit["score"] <= 10


def test_overbought_euphoria_fires_short():
    hit = mfi_extreme(_trend(60, step=+0.5))
    assert hit["fired"] is True
    assert hit["reason"].startswith("short — MFI")
    assert "overbought" in hit["reason"]


def test_flat_series_does_not_fire():
    cs = [_c(i, 100.0, 100.05, 99.95, 100.0) for i in range(60)]
    hit = mfi_extreme(cs)
    assert hit["fired"] is False
    assert hit["score"] == 0


def test_insufficient_history():
    hit = mfi_extreme(_trend(10))
    assert hit["fired"] is False
    assert hit["reason"] == "insufficient_history"


def test_vol_confirm_clamps_unconfirmed_score():
    """With vol_confirm_ratio on, a quiet last bar in the zone still fires
    (the zone reading is the signal) but the score is clamped to <= 5."""
    quiet = _trend(60, step=-0.5, v=1000.0)
    quiet[-1] = _c(59, 70.5, 70.8, 70.2, 70.5, v=10.0)  # tiny volume
    hit = mfi_extreme(quiet, vol_confirm_ratio=1.5)
    assert hit["fired"] is True
    assert "no volume confirm" in hit["reason"]
    assert hit["score"] <= 5.0
    # climax volume -> confirmed, higher score
    loud = _trend(60, step=-0.5, v=1000.0)
    loud[-1] = _c(59, 70.5, 70.8, 70.2, 70.5, v=5000.0)
    hit2 = mfi_extreme(loud, vol_confirm_ratio=1.5)
    assert hit2["fired"] is True and "volume-confirmed" in hit2["reason"]
    assert hit2["score"] >= hit["score"]


def test_weight_zero_no_composite_impact():
    """The trigger is a pure surfacing bypass: weight 0 => adding a fired hit
    cannot change the composite score (no gate recalibration)."""
    cfg = get_config()
    weights = cfg["weights"]
    assert weights.get("mfiExtreme") == 0.0
    base_hits = [{"name": "breakout", "score": 8, "fired": True, "reason": "x"}]
    with_mfi = base_hits + [{"name": "mfiExtreme", "score": 10, "fired": True,
                             "reason": "long — MFI 12 <= 20 oversold"}]
    assert composite_score(base_hits, weights) == composite_score(with_mfi, weights)


# ---------------------------------------------------------------------------
# Perception wiring: enabled gate, bypass, shadow suppression, accrual line
# ---------------------------------------------------------------------------

_MARKET = {"coin": "TEST", "type": "perp"}
_MF_CFG = {"enabled": True, "mfi_period": 14, "oversold": 20.0,
           "overbought": 80.0, "vol_confirm_ratio": 0.0}


def _wire(monkeypatch, five_m):
    """5m candles = `five_m`; 1h = flat (kills the slow-burn triggers)."""
    def _fetch(coin, interval, count, cache_ttl_ms, **kw):
        return _flat() if interval != "5m" else five_m
    monkeypatch.setattr(perception, "_fetch_candles_sync", _fetch)


def _flat(n=100):
    return [_c(i, 100.0, 100.05, 99.95, 100.0) for i in range(n)]


def _scan(monkeypatch, cfg_block, min_score, five_m=None):
    cfg = {**get_config(), "mfi_extreme": cfg_block}
    _wire(monkeypatch, five_m or _trend(100, step=-0.5))
    return perception._scan_single_market(_MARKET, 100.0, cfg, min_score,
                                          None, False, False)


def test_disabled_by_default_no_hit(monkeypatch):
    """Block absent (the merge no-op): no mfiExtreme hit in triggers even
    when the geometry would fire."""
    cfg = get_config()
    _wire(monkeypatch, _trend(100, step=-0.5))
    ok, res = perception._scan_single_market(_MARKET, 100.0, cfg, 0, None, False, False)
    assert ok
    if res is not None:
        assert all(h["name"] != "mfiExtreme" for h in res["triggers"])


def test_enabled_live_bypasses_composite_gate(monkeypatch):
    """enabled + shadow OFF: the fired washout surfaces the coin even with
    min_score above anything the series scores (bypass)."""
    ok, res = _scan(monkeypatch, dict(_MF_CFG), min_score=99)
    assert ok and res is not None, "live MFI bypass must surface"
    hit = next(h for h in res["triggers"] if h["name"] == "mfiExtreme")
    assert hit["fired"] is True


def test_shadow_on_suppresses_surfacing(monkeypatch):
    """shadow ON: same fired geometry, bypass suppressed -> coin dropped."""
    ok, res = _scan(monkeypatch, {**_MF_CFG, "shadow_mode": True}, min_score=99)
    assert ok and res is None, f"shadow ON must suppress surfacing (got {res})"


def test_shadow_on_logs_accrual_once(monkeypatch, caplog):
    ok, res = _scan(monkeypatch, {**_MF_CFG, "shadow_mode": True}, min_score=0)
    lines = [r for r in caplog.records
             if "[mfi-extreme][SHADOW]" in r.getMessage()]
    assert len(lines) == 1, f"expected exactly one accrual line, got {lines!r}"
    assert lines[0].levelno == logging.WARNING
    msg = lines[0].getMessage()
    assert "TEST" in msg and "MFI" in msg and "oversold" in msg
    assert "log-only" in msg


def test_shadow_off_is_silent(monkeypatch, caplog):
    _scan(monkeypatch, dict(_MF_CFG), min_score=0)
    lines = [r.getMessage() for r in caplog.records
             if "[mfi-extreme][SHADOW]" in r.getMessage()]
    assert lines == []


def test_compute_stays_alive_shadow_on(monkeypatch):
    """shadow ON does not stop the computation: with min_score 0 the coin is
    surfaced and the hit is inspectable (the counterfactual record)."""
    ok, res = _scan(monkeypatch, {**_MF_CFG, "shadow_mode": True}, min_score=0)
    assert ok and res is not None
    hit = next(h for h in res["triggers"] if h["name"] == "mfiExtreme")
    assert hit["fired"] is True and hit["reason"]


# ---------------------------------------------------------------------------
# Prompt side: shadow_mode filters the hit out of the trigger summary
# ---------------------------------------------------------------------------

_FIRED_HIT = {"name": "mfiExtreme", "fired": True, "score": 9.0,
              "reason": "long — MFI 12 <= 20 oversold (no volume confirm)"}


def _build_prompt(shadow_mode):
    perception_dict = {"type": "perp", "mid": 9.0, "composite_score": 40,
                       "triggers": [dict(_FIRED_HIT)]}
    snap = {"ema8": None, "ema21": None, "last_close": 9.0}
    real_read = research.read_agent_config
    cfg = {"mfi_extreme": {"enabled": True}}
    if shadow_mode:
        cfg["mfi_extreme"]["shadow_mode"] = True
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
    assert "mfiExtreme" in msg


def test_prompt_shadow_on_hides_hit():
    msg = _build_prompt(shadow_mode=True)
    assert "mfiExtreme" not in msg
    assert "oversold" not in msg
