"""VWAP deviation trigger — shadow-mode wiring (surfacing bypass + accrual).

Trigger: `indicators.triggers.vwap_deviation` — distance from the
SWING-ANCHORED VWAP (average cost basis since the most recent confirmed
swing extreme) in volume-weighted sigmas. Shapes: FADE (px stretched
>= fade_z from that cost basis) and RETEST (orderly pullback back to the
VWAP mid-move, |z| <= touch_z with >= min_trend_pct since the anchor).

Config block `vwap_deviation` (hot-read per scan):
  enabled      — trigger computes + hit appended + bypass armed
  shadow_mode  — trigger KEEPS computing but the surfacing bypass is
                 suppressed, research.py filters the hit out of the prompt
                 summary, and a `[vwap-deviation][SHADOW]` WARNING accrues
                 per fired hit.

Code default = block absent = trigger never runs = byte-identical merge.

All hermetic: no network, no LLM, no fetch. Perception path passes the
config dict directly and patches `_fetch_candles_sync`.
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
from hermes_trader.indicators.math import anchored_vwap  # noqa: E402
from hermes_trader.indicators.triggers import vwap_deviation, composite_score  # noqa: E402
from hermes_trader.models.types import Candle  # noqa: E402

_T0 = 1_700_000_000_000
_STEP = 900_000  # 15m


def _c(i, o, h, l, c):
    return Candle(t=_T0 + i * _STEP, o=o, h=h, l=l, c=c, v=1000.0)


# ---------------------------------------------------------------------------
# Pure trigger math
# ---------------------------------------------------------------------------

def _bull_fade_series():
    """Rally to a single-bar swing high at 29, slow grind down, then a
    capitulation leg: px ends ~2.7 sigma BELOW the VWAP anchored at the
    swing high. 100 bars."""
    cs = []
    px = 100.0
    for i in range(30):
        px += 1.0
        cs.append(_c(i, px - 1.0, px + 0.5, px - 0.5, px))
    for i in range(30, 80):
        px -= 0.125
        cs.append(_c(i, px + 0.125, px + 0.5, px - 0.5, px))
    for i in range(80, 100):
        px -= 1.0
        cs.append(_c(i, px + 1.0, px + 0.5, px - 0.5, px))
    return cs


def _bear_fade_series():
    """Mirror: decline to a swing low at 29, slow climb, then a sharp
    rally: px ~2.7 sigma ABOVE the swing-low-anchored VWAP."""
    cs = []
    px = 100.0
    for i in range(30):
        px -= 1.0
        cs.append(_c(i, px + 1.0, px + 0.5, px - 0.5, px))
    for i in range(30, 80):
        px += 0.125
        cs.append(_c(i, px - 0.125, px + 0.5, px - 0.5, px))
    for i in range(80, 100):
        px += 1.0
        cs.append(_c(i, px - 1.0, px + 0.5, px - 0.5, px))
    return cs


def _bull_retest_series(end=112.0):
    """Swing low at 29, +20% rally to 120 by 60, orderly pullback to ~the
    swing-low-anchored VWAP by 99: retest entry shape."""
    cs = []
    px = 100.0
    for i in range(30):
        px -= 0.5
        cs.append(_c(i, px + 0.5, px + 0.5, px - 0.5, px))
    for i in range(30, 61):
        px += 20.0 / 31
        cs.append(_c(i, px - 20.0 / 31, px + 0.5, px - 0.5, px))
    for i in range(61, 100):
        px -= (120.0 - end) / 39
        cs.append(_c(i, px + (120.0 - end) / 39, px + 0.5, px - 0.5, px))
    return cs


def _bear_retest_series(end=103.0):
    """Mirror: swing high at 29, -17% decline to 95 by 60, bounce back to
    the swing-high-anchored VWAP by 99."""
    cs = []
    px = 100.0
    for i in range(30):
        px += 0.5
        cs.append(_c(i, px - 0.5, px + 0.5, px - 0.5, px))
    for i in range(30, 61):
        px -= 20.0 / 31
        cs.append(_c(i, px + 20.0 / 31, px + 0.5, px - 0.5, px))
    for i in range(61, 100):
        px += (end - 95.0) / 39
        cs.append(_c(i, px - (end - 95.0) / 39, px + 0.5, px - 0.5, px))
    return cs


def _flat_series(n=100):
    return [_c(i, 100.0, 100.05, 99.95, 100.0) for i in range(n)]


def test_bull_fade_fires_long():
    hit = vwap_deviation(_bull_fade_series())
    assert hit["fired"] is True
    assert hit["reason"].startswith("long — vwap fade")
    assert "swing high" in hit["reason"]
    assert 0 < hit["score"] <= 10


def test_bear_fade_fires_short():
    hit = vwap_deviation(_bear_fade_series())
    assert hit["fired"] is True
    assert hit["reason"].startswith("short — vwap fade")
    assert "swing low" in hit["reason"]


def test_bull_retest_fires_long():
    hit = vwap_deviation(_bull_retest_series())
    assert hit["fired"] is True
    assert hit["reason"].startswith("long — vwap retest")
    assert "since anchor" in hit["reason"]


def test_bear_retest_fires_short():
    hit = vwap_deviation(_bear_retest_series())
    assert hit["fired"] is True
    assert hit["reason"].startswith("short — vwap retest")


def test_flat_series_does_not_fire():
    hit = vwap_deviation(_flat_series())
    assert hit["fired"] is False
    assert hit["score"] == 0


def test_mild_stretch_reports_current_z():
    """Near-miss observability: a non-fired hit carries the CURRENT z so
    offline threshold sweeps see the distribution, not just the fires."""
    hit = vwap_deviation(_bull_retest_series(end=118.0))  # shallow pullback
    assert hit["fired"] is False
    assert "no setup" in hit["reason"] and "σ" in hit["reason"]


def test_insufficient_history():
    hit = vwap_deviation(_flat_series(8))
    assert hit["fired"] is False
    assert hit["reason"] == "insufficient_history"


def test_anchor_age_bounds_respected():
    """A swing younger than min_anchor_age is skipped: with min_anchor_age
    past every candidate's age, no anchor exists -> no opinion, never a
    fired hit. (90 passes the length guard but exceeds the series' only
    swing's age of 70.)"""
    hit = vwap_deviation(_bull_fade_series(), min_anchor_age=90)
    assert hit["fired"] is False
    assert hit["reason"] == "no_swing"


def test_anchored_vwap_volume_weighting():
    """VWAP is dollar-weighted: a high-volume bar pins the level even when
    it is NOT the arithmetic mean of typical prices."""
    lo = _c(0, 10, 10, 10, 10)                       # tp 10, vol 1000
    hi = Candle(t=_T0 + _STEP, o=90, h=90, l=90, c=90, v=9000.0)  # tp 90, vol 9x
    vwap, sigma, n = anchored_vwap([lo, hi], 0)
    assert n == 2
    assert abs(vwap - (10 * 1000 + 90 * 9000) / 10000) < 1e-9
    assert vwap > 50.0  # pulled toward the high-volume bar, not the mean (50)
    dead = anchored_vwap([Candle(t=_T0, o=1, h=1, l=1, c=1, v=0.0)], 0)
    assert dead[2] == 0 and dead[0] != dead[0]  # (nan, nan, 0) on dead tape


def test_weight_zero_no_composite_impact():
    """The trigger is a pure surfacing bypass: weight 0 => adding a fired
    hit cannot change the composite score (no gate recalibration)."""
    cfg = get_config()
    weights = cfg["weights"]
    assert weights.get("vwapDeviation") == 0.0
    base_hits = [{"name": "breakout", "score": 8, "fired": True, "reason": "x"}]
    with_vwap = base_hits + [{"name": "vwapDeviation", "score": 10, "fired": True,
                              "reason": "long — vwap fade: px -2.7σ"}]
    assert composite_score(base_hits, weights) == composite_score(with_vwap, weights)


# ---------------------------------------------------------------------------
# Perception wiring: enabled gate, bypass, shadow suppression, accrual line
# ---------------------------------------------------------------------------

_MARKET = {"coin": "TEST", "type": "perp"}
_VW_CFG = {"enabled": True, "interval": "15m", "swing_wick": 2,
           "min_anchor_age": 8, "max_anchor_age": 96,
           "fade_z": 2.2, "touch_z": 0.4, "min_trend_pct": 4.0}


def _wire(monkeypatch, vwap_series):
    """15m candles = `vwap_series`; everything else flat (kills the other
    triggers so the composite stays ~0 and only the bypass can surface)."""
    def _fetch(coin, interval, count, cache_ttl_ms, **kw):
        return vwap_series if interval == "15m" else _flat_series()
    monkeypatch.setattr(perception, "_fetch_candles_sync", _fetch)


def _scan(monkeypatch, cfg_block, min_score, series=None):
    cfg = {**get_config(), "vwap_deviation": cfg_block}
    _wire(monkeypatch, series or _bull_fade_series())
    return perception._scan_single_market(_MARKET, 100.0, cfg, min_score,
                                          None, False, False)


def test_disabled_by_default_no_hit(monkeypatch):
    """Block absent (the merge no-op): no vwapDeviation hit in triggers even
    when the geometry would fire."""
    cfg = get_config()
    _wire(monkeypatch, _bull_fade_series())
    ok, res = perception._scan_single_market(_MARKET, 100.0, cfg, 0, None, False, False)
    assert ok
    if res is not None:
        assert all(h["name"] != "vwapDeviation" for h in res["triggers"])


def test_enabled_live_bypasses_composite_gate(monkeypatch):
    """enabled + shadow OFF: the fired fade surfaces the coin even with
    min_score above anything the series scores (bypass)."""
    ok, res = _scan(monkeypatch, dict(_VW_CFG), min_score=99)
    assert ok and res is not None, "live vwap fade bypass must surface"
    hit = next(h for h in res["triggers"] if h["name"] == "vwapDeviation")
    assert hit["fired"] is True


def test_shadow_on_suppresses_surfacing(monkeypatch):
    """shadow ON: same fired geometry, bypass suppressed -> coin dropped."""
    ok, res = _scan(monkeypatch, {**_VW_CFG, "shadow_mode": True}, min_score=99)
    assert ok and res is None, f"shadow ON must suppress surfacing (got {res})"


def test_shadow_on_logs_accrual_once(monkeypatch, caplog):
    ok, res = _scan(monkeypatch, {**_VW_CFG, "shadow_mode": True}, min_score=0)
    lines = [r for r in caplog.records
             if "[vwap-deviation][SHADOW]" in r.getMessage()]
    assert len(lines) == 1, f"expected exactly one accrual line, got {lines!r}"
    assert lines[0].levelno == logging.WARNING
    msg = lines[0].getMessage()
    assert "TEST" in msg and "vwap fade" in msg
    assert "log-only" in msg


def test_shadow_off_is_silent(monkeypatch, caplog):
    _scan(monkeypatch, dict(_VW_CFG), min_score=0)
    lines = [r.getMessage() for r in caplog.records
             if "[vwap-deviation][SHADOW]" in r.getMessage()]
    assert lines == []


def test_compute_stays_alive_shadow_on(monkeypatch):
    """shadow ON does not stop the computation: with min_score 0 the coin is
    surfaced and the hit is inspectable (the counterfactual record)."""
    ok, res = _scan(monkeypatch, {**_VW_CFG, "shadow_mode": True}, min_score=0)
    assert ok and res is not None
    hit = next(h for h in res["triggers"] if h["name"] == "vwapDeviation")
    assert hit["fired"] is True and hit["reason"]


# ---------------------------------------------------------------------------
# Prompt side: shadow_mode filters the hit out of the trigger summary
# ---------------------------------------------------------------------------

_FIRED_HIT = {"name": "vwapDeviation", "fired": True, "score": 6.2,
              "reason": ("long — vwap fade: px -2.7σ from VWAP 123.037 "
                         "anchored at swing high 70 bars ago (anchor px 130.5)")}


def _build_prompt(shadow_mode):
    perception_dict = {"type": "perp", "mid": 9.0, "composite_score": 40,
                       "triggers": [dict(_FIRED_HIT)]}
    snap = {"ema8": None, "ema21": None, "last_close": 9.0}
    real_read = research.read_agent_config
    cfg = {"vwap_deviation": {"enabled": True}}
    if shadow_mode:
        cfg["vwap_deviation"]["shadow_mode"] = True
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
    assert "vwapDeviation" in msg


def test_prompt_shadow_on_hides_hit():
    msg = _build_prompt(shadow_mode=True)
    assert "vwapDeviation" not in msg
    assert "vwap fade" not in msg


# ---------------------------------------------------------------------------
# Dedicated prompt block: gated on BOTH flags (enabled AND not shadow_mode)
# ---------------------------------------------------------------------------

_NOT_FIRED_HIT = {"name": "vwapDeviation", "fired": False, "score": 0,
                  "reason": ("no setup (px +1.4σ vs VWAP anchored at "
                             "swing low 42 bars ago)")}
_NO_OPINION_HIT = {"name": "vwapDeviation", "fired": False, "score": 0,
                   "reason": "no_swing"}


def _build_block_prompt(cfg_block, hit):
    """Build the user message with an arbitrary vwap_deviation config block
    and an arbitrary vwapDeviation trigger hit (None = hit absent)."""
    triggers = [] if hit is None else [dict(hit)]
    perception_dict = {"type": "perp", "mid": 9.0, "composite_score": 40,
                       "triggers": triggers}
    snap = {"ema8": None, "ema21": None, "last_close": 9.0}
    real_read = research.read_agent_config
    try:
        research.read_agent_config = lambda: ({"vwap_deviation": cfg_block}
                                              if cfg_block is not None else {})
        return research._build_user_message(
            "TEST", perception_dict, snap, snap, snap, "0.01%/hr", "no news",
            250.0, [], "LIVE",
        )
    finally:
        research.read_agent_config = real_read


def test_block_absent_config_renders_nothing_even_if_hit_present():
    """The DEDICATED block's gate is the FLAGS, not hit presence: config
    block absent -> no block text even with a fired hit riding in
    perception. (The generic summary line still lists the hit — that is the
    historical family behavior; the summary filter keys on shadow_mode, and
    a hit can only exist in perception if the trigger was enabled at scan
    time anyway.)"""
    msg = _build_block_prompt(None, _FIRED_HIT)
    assert "VWAP deviation signal" not in msg
    assert "VWAP context" not in msg
    assert "vwapDeviation" in msg  # summary line: enabled unknown -> not filtered


def test_block_disabled_flag_renders_nothing():
    msg = _build_block_prompt({"enabled": False}, _FIRED_HIT)
    assert "VWAP deviation signal" not in msg
    assert "VWAP context" not in msg


def test_block_shadow_flag_renders_nothing():
    """shadow_mode ON suppresses BOTH prompt surfaces: the summary line AND
    the dedicated block (the band-snapback leak lesson)."""
    msg = _build_block_prompt({"enabled": True, "shadow_mode": True}, _FIRED_HIT)
    assert "VWAP deviation signal" not in msg
    assert "vwap fade" not in msg
    assert "vwapDeviation" not in msg


def test_block_live_fired_renders_fired_branch():
    msg = _build_block_prompt({"enabled": True}, _FIRED_HIT)
    assert "VWAP deviation signal" in msg
    assert "swing high 70 bars ago" in msg
    assert "LONG" in msg  # side named positively from the long-side reason
    assert "never itself a reason to PASS" in msg


def test_block_live_not_fired_renders_context():
    """A non-fired hit with a current-z reason renders the informative
    context branch (extension vs cost basis) — entry context even when the
    coin surfaced via another trigger."""
    msg = _build_block_prompt({"enabled": True}, _NOT_FIRED_HIT)
    assert "VWAP context" in msg
    assert "+1.4σ" in msg


def test_block_live_no_opinion_renders_nothing():
    """no_swing / no_vwap / insufficient_history -> no opinion -> empty."""
    for reason in ("no_swing", "no_vwap", "insufficient_history", "flat"):
        msg = _build_block_prompt({"enabled": True},
                                  {**_NO_OPINION_HIT, "reason": reason})
        assert "VWAP" not in msg, f"reason={reason} leaked"


def test_block_empty_is_byte_identical():
    """Gated-off states add ZERO bytes (no stray join newlines): a shadowed
    build WITH a fired hit must be byte-identical to the same build with no
    hit at all, and likewise for enabled=false + a non-fired hit."""
    shadowed_hit = _build_block_prompt({"enabled": True, "shadow_mode": True}, _FIRED_HIT)
    shadowed_none = _build_block_prompt({"enabled": True, "shadow_mode": True}, None)
    assert shadowed_hit == shadowed_none
    off_hit = _build_block_prompt({"enabled": False}, _NOT_FIRED_HIT)
    off_none = _build_block_prompt({"enabled": False}, None)
    assert off_hit == off_none
