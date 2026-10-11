"""P3 — band_snapback.shadow_mode (the single umbrella flag).

Adds ONE new config key `band_snapback.shadow_mode` (code default FALSE =
current behavior, so the merge is a no-op). When TRUE (with `enabled` still
TRUE), every band-snapback action point becomes LOG-ONLY while the trigger
keeps computing:

  1. research.py prompt block — the fired branch AND the "band trending"
     context branch render NOTHING (snapback_block -> empty).
  2. perception.py snapback_bypass — additionally requires NOT shadow_mode.
  3. risk_gates.py band_counter_breach_gate — the shadow decision ORs in the
     umbrella so the gate goes would-block-only even with its own
     shadow_mode at default. (Covered in test_band_counter_breach_gate.py.)
  4. perception.py accrual line — on every FIRED snapback while shadow_mode,
     emit exactly one `[band-snapback][SHADOW] {coin} {side} — {reason}
     (shadow: prompt+surfacing suppressed, gate log-only)` line; silent when
     shadow_mode is false.
  5. trigger keeps computing — `fired`/`reason` keep populating regardless of
     shadow_mode.

All hermetic: no network, no LLM, no candle fetch, no model. Config is
written to the temp file the test suite is isolated on (config_store.CONFIG_PATH)
for the prompt path and passed directly to `_scan_single_market` for the
perception path.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_trader.agents import perception  # noqa: E402
from hermes_trader.agents.config import get_config  # noqa: E402
from hermes_trader.agents import research  # noqa: E402
from hermes_trader.models.types import Candle  # noqa: E402


# ---------------------------------------------------------------------------
# Prompt block (research.py) — the fired + trending branches render empty in
# shadow mode; the trigger hit stays in perception's triggers.
# ---------------------------------------------------------------------------
# A fired band-snapback trigger hit (the exact reason shape the live scan
# produces — starts with the side word, carries the drift %).
FIRED_REASONS = {
    "long": ("long — lower wick 0.8x ATR past projected EMA/24-bar band edge "
             "(de-lagged 1 bar), snapped back inside (drift 0.0%)"),
    "short": ("short — upper wick 0.9x ATR past projected EMA/24-bar band edge "
              "(de-lagged 1 bar), snapped back inside (drift 0.0%)"),
}
# A non-fired, band-trending trigger hit (drift gate vetoes the fade).
TRENDING_REASON = ("band trending DOWN (5.1% drift > 1.5% over 24-bar window; "
                   "px +0.3% vs upper edge, +0.5% vs lower edge)")

# The three snapback_block shapes — their leading signatures. In shadow mode
# ALL of these must be absent from the rendered prompt.
FIRED_SIG = "Band snapback signal (fired"
TRENDING_SIG = "Band context (no snapback signal"
NOTPRESENT_SIG = "Band snapback signal: not present"


def _build_prompt(coin, trigger, shadow_mode):
    """Render the user message with a single bandSnapback trigger in
    perception, patching read_agent_config to carry the band_snapback block.
    The prompt path reads the key per call via research.read_agent_config."""
    perception_dict = {
        "type": "perp", "mid": 9.0, "composite_score": 40,
        "triggers": [trigger],
    }
    snap = {"ema8": None, "ema21": None, "last_close": 9.0}
    real_read = research.read_agent_config
    cfg = {"band_snapback": {"enabled": True}}
    if shadow_mode:
        cfg["band_snapback"]["shadow_mode"] = True
    try:
        research.read_agent_config = lambda: cfg
        return research._build_user_message(
            coin, perception_dict, snap, snap, snap, "0.01%/hr", "no news",
            250.0, [], "LIVE",
        )
    finally:
        research.read_agent_config = real_read


def _fired_hit(side="long"):
    return {"name": "bandSnapback", "fired": True, "reason": FIRED_REASONS[side]}


def _trending_hit():
    return {"name": "bandSnapback", "fired": False, "reason": TRENDING_REASON}


def test_prompt_fired_shadow_off_present():
    """Shadow OFF (the no-op default): a FIRED band renders its fired block."""
    msg = _build_prompt("TEST", _fired_hit("long"), shadow_mode=False)
    assert FIRED_SIG in msg


def test_prompt_fired_shadow_on_absent():
    """Shadow ON: a FIRED band renders NOTHING — fired block, the trending
    context block, and the 'not present' fallback are all gone, AND the
    trigger-summary line no longer leaks the fired hit's name+reason
    (FIXED: the P3 umbrella originally left the summary as a side door,
    contradicting its own accrual-line claim 'prompt+surfacing suppressed'
    and the code comment 'NO band-snapback text reaches the prompt')."""
    msg = _build_prompt("TEST", _fired_hit("long"), shadow_mode=True)
    assert FIRED_SIG not in msg
    assert TRENDING_SIG not in msg
    assert NOTPRESENT_SIG not in msg
    # summary side door closed: neither the trigger name nor its reason text
    assert "bandSnapback" not in msg
    assert "lower wick" not in msg


def test_prompt_fired_shadow_off_summary_lists_hit():
    """Shadow OFF: the fired hit appears in the generic trigger summary
    (pre-existing mechanism listing every fired trigger) alongside its block."""
    msg = _build_prompt("TEST", _fired_hit("long"), shadow_mode=False)
    assert "bandSnapback" in msg
    assert "lower wick" in msg


def test_prompt_trending_shadow_off_present():
    """Shadow OFF: a band-trending (non-fired) hit renders its context block."""
    msg = _build_prompt("TEST", _trending_hit(), shadow_mode=False)
    assert TRENDING_SIG in msg


def test_prompt_trending_shadow_on_absent():
    """Shadow ON: the trending-context branch is suppressed too (both the fired
    AND the trending branches vanish; the 'not present' fallback is gone as
    well, so no band text at all)."""
    msg = _build_prompt("TEST", _trending_hit(), shadow_mode=True)
    assert TRENDING_SIG not in msg
    assert FIRED_SIG not in msg
    assert NOTPRESENT_SIG not in msg


# ---------------------------------------------------------------------------
# Perception (surfacing bypass + accrual line + compute-stays-alive).
#
# `_scan_single_market` is called directly with a full config dict (the band
# block is passed in `config`), and `perception._fetch_candles_sync` is patched
# to return synthetic candles — no network. This mirrors how `scan_once` feeds
# a merged config (read_agent_config merged in) into the per-market worker.
# ---------------------------------------------------------------------------
SPAN = 24
NEED = 2 * SPAN  # 48
_T0 = 1_700_000_000_000
_STEP = 900_000  # 15m


def _candle(i, o, h, l, c):
    return Candle(t=_T0 + i * _STEP, o=o, h=h, l=l, c=c, v=1000.0)


def _chop(n=None, mid=100.0, half=0.3):
    """Flat oscillating chop: closes pinned near mid, wicks to +/-half.
    For flat chop the MA-of-highs sits at ~mid+half, MA-of-lows at ~mid-half,
    so the band is near-flat and the drift gate passes (a snapback can fire)."""
    n = n or NEED
    out = []
    for i in range(n):
        c = mid + (0.05 if i % 2 == 0 else -0.05)
        o = mid + (-0.05 if i % 2 == 0 else 0.05)
        out.append(_candle(i, o, c + half, c - half, c))
    return out


def _fired_band_candles():
    """Fired geometry in LIVE framing (include_partial=True, the way
    perception calls the trigger): 48 chop + the poke bar (last CLOSED) + a
    forming bar whose close is the current px. A lower wick pokes out and
    snaps back inside -> LONG fires (verified against the real trigger)."""
    cs = _chop() + [
        _candle(NEED, 99.9, 100.0, 99.2, 99.85),      # poke bar (candles[-2])
        _candle(NEED + 1, 99.85, 99.9, 99.8, 99.85),  # forming bar (candles[-1])
    ]
    return cs


# Flat 5m candles so no momentum/breakout/trend trigger fires and the ONLY
# thing that can surface the coin is the band-snapback bypass under test.
FLAT_5M = [_candle(i, 100.0, 100.05, 99.95, 100.0) for i in range(100)]

_BAND_CFG = {
    "enabled": True, "ma_type": "ema", "band_span": SPAN,
    "max_drift_pct": 1.5, "min_poke_atr": 0.5, "max_project_atr": 0.25,
    "interval": "1h",
}
_MARKET = {"coin": "TEST", "type": "perp"}


def _wire_band(monkeypatch, band_candles):
    """Patch the perception candle fetch: '1h' -> band geometry, '5m' -> flat."""
    def _fetch(coin, interval, count, cache_ttl_ms, **kw):
        if interval == "1h":
            return band_candles
        return FLAT_5M
    monkeypatch.setattr(perception, "_fetch_candles_sync", _fetch)


def _scan(shadow, min_score):
    bs = dict(_BAND_CFG)
    if shadow:
        bs["shadow_mode"] = True
    cfg = {**get_config(), "band_snapback": bs}
    ok, res = perception._scan_single_market(
        _MARKET, 100.0, cfg, min_score, None, False, True)
    return ok, res


def test_surfacing_shadow_off_fired_is_surfaced(monkeypatch):
    """Shadow OFF: a fired band-snapback bypasses the composite gate and the
    coin IS surfaced for research (the pre-P3 behavior — no-op)."""
    _wire_band(monkeypatch, _fired_band_candles())
    ok, res = _scan(shadow=False, min_score=54)
    assert ok and res is not None, "shadow OFF + fired must surface the coin"
    hit = next(h for h in res["triggers"] if h["name"] == "bandSnapback")
    assert hit["fired"] is True


def test_surfacing_shadow_on_fired_is_suppressed(monkeypatch):
    """Shadow ON: the same fired band does NOT surface — the bypass is
    suppressed and, with no other trigger and score below the gate, the coin is
    dropped. The trigger still computed (see the accrual + compute tests)."""
    _wire_band(monkeypatch, _fired_band_candles())
    ok, res = _scan(shadow=True, min_score=54)
    assert ok and res is None, (
        "shadow ON must suppress the band-snapback surfacing bypass "
        f"(got res={res})")


def test_accrual_line_fired_shadow_on_logs_once(monkeypatch, caplog):
    """The crux of 'shadow, log-only': with a FIRED snapback and shadow_mode ON,
    emit EXACTLY ONE `[band-snapback][SHADOW]` line naming the coin + side,
    carrying the existing band-state reason."""
    _wire_band(monkeypatch, _fired_band_candles())
    with caplog.at_level(logging.WARNING, logger="hermes_trader.agents.perception"):
        _scan(shadow=True, min_score=54)
    lines = [r.getMessage() for r in caplog.records
             if "[band-snapback][SHADOW]" in r.getMessage()]
    assert len(lines) == 1, f"expected exactly one accrual line, got {lines!r}"
    line = lines[0]
    assert "TEST" in line                       # the coin
    assert "long" in line                        # the side (from the reason)
    assert "prompt+surfacing suppressed" in line
    assert "gate log-only" in line
    assert "drift" in line                       # the band-state reason carried
    # A visible line, not debug — this is the counterfactual accrual record.
    assert any(r.levelno == logging.WARNING
               for r in caplog.records if "[band-snapback][SHADOW]" in r.getMessage())


def test_accrual_line_shadow_off_is_silent(monkeypatch, caplog):
    """Shadow OFF: the trigger fires but NO accrual line is emitted (the
    shadow-only record must stay silent when the umbrella is off)."""
    _wire_band(monkeypatch, _fired_band_candles())
    with caplog.at_level(logging.WARNING, logger="hermes_trader.agents.perception"):
        _scan(shadow=False, min_score=54)
    lines = [r.getMessage() for r in caplog.records
             if "[band-snapback][SHADOW]" in r.getMessage()]
    assert lines == [], f"shadow OFF must not log the accrual line: {lines!r}"


def test_compute_stays_alive_shadow_on(monkeypatch):
    """Shadow ON does NOT stop the computation: with a low composite gate the
    coin is still surfaced so the hit is inspectable — the bandSnapback trigger
    is present with fired=True and a populated reason (the data source for the
    accrual line and any later replay)."""
    _wire_band(monkeypatch, _fired_band_candles())
    ok, res = _scan(shadow=True, min_score=0)  # min 0 -> not dropped by score
    assert ok and res is not None
    hit = next(h for h in res["triggers"] if h["name"] == "bandSnapback")
    assert hit["fired"] is True
    assert hit.get("reason"), "reason must stay populated under shadow"
    # The reason is the band-state string (carries drift / edge / side).
    assert "drift" in hit["reason"]
