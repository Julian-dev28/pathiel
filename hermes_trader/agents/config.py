"""Agent trigger configuration: trigger weights, thresholds, and scan settings."""

from __future__ import annotations

from typing import Any, Dict


TRIGGER_CONFIG: Dict[str, Any] = {
    "weights": {
        # RE-WEIGHTED 2026-06-02 to MEASURED MARGINAL LIFT (fired vs not-fired ROE,
        # n=497 trades). Prior weights were inverted: the 1h slow-burn signals carried
        # the heaviest weight (0.60/0.55/0.40) but had ~0/negative lift, while
        # trendStrength (the BEST signal, +2.08% lift) was only 0.10. Weights now
        # track lift; net-negative triggers (trendFlip1h -2.10%, rangeCompression
        # -3.08%) are ZEROED out of scoring.
        "trendStrength": 0.55,    # lift +2.08% (was 0.10) — strongest edge
        "pctMoveSpike": 0.40,     # lift +1.49%
        "breakout": 0.30,         # lift +1.29%
        "volumeSpike": 0.25,      # lift +1.05%
        "momentumBurst": 0.20,    # lift +0.77% (n=9, kept modest)
        "volumeBuildup1h": 0.15,  # lift +0.41% (was 0.60 — overweighted)
        "higherLows1h": 0.0,      # lift -0.51% — removed
        "trendFlip1h": 0.0,       # lift -2.10% — removed (net loser)
        "rangeCompression": 0.0,  # lift -3.08% — removed (worst)
        # Symmetric directional SURFACING triggers — weight 0 so they don't touch
        # the composite denominator (no gate recalibration). They surface trending
        # coins via the bypass in perception, not via score. Removes the long-bias
        # in surfacing so down-movers reach research and can be shorted.
        "uptrendMomentum": 0.0,
        "downtrendMomentum": 0.0,
        "dailyMover": 0.0,
        # Band snapback — SURFACING BYPASS (weight 0): a wick poke-out + snapback
        # into a flat MA band (rolling avg of highs/low...[truncated]
        "bandSnapback": 0.0,
        # RSI divergence — SURFACING BYPASS (weight 0): price prints a new
        # extreme while RSI refuses to (momentum fading into the push).
        "rsiDivergence": 0.0,
        # MFI extreme — SURFACING BYPASS (weight 0): volume-weighted RSI
        # pinned in a washout (long) / euphoria (short) zone.
        "mfiExtreme": 0.0,
        # VWAP deviation — SURFACING BYPASS (weight 0): px stretched past
        # fade_z sigmas from the swing-anchored VWAP (fade) or pulled back
        # to it mid-move (retest). Cost-basis level, volume-weighted.
        "vwapDeviation": 0.0,
    },
    "thresholds": {
        "sigmaThreshold": 2.0,
        "trendMomentumLookback": 72,  # 5m bars (~6h) for sustained up/down trend surfacing
        "trendMomentumPct": 5.0,      # min |%| move over ~6h to surface (5%: 3.0 over-surfaced — 22 triggers/scan, ~4.5x AI cost, flooded longs)
        "breakoutLookback": 48,
        "bbLength": 20,
        "bbStdDev": 2,
        "adxPeriod": 14,
        "momentumLookback": 2,   # 5m bars in the momentum_burst window (-> 10 min)
        "momentumPct": 4.0,      # min % move over that window to fire momentum_burst
        "volBuildupRatio": 2.5,  # 4h vs prior 20h avg, on 1h candles
        "trendFlipBars": 3,      # EMA8/21 cross within last N 1h bars
        "higherLowsRequired": 4, # of last 6 1h bars
    },
    # t_62c58463 — momentum trigger estimator (t_38944f65 NXPC post-mortem).
    # The close-to-close net reads a pump bar's MEMORY, not a trend: it fired
    # uptrendMomentum +13.3% UP through six red hours on 2026-10-15. Theil-Sen
    # (median of pairwise log-price slopes) is pump-immune AND reads genuine
    # regime changes; 11/11 labeled bake-off, 82% vs net 81% forward-2h on an
    # 8-perp basket. mode: "off" = net only (legacy) | "shadow" = net decides,
    # Theil-Sen accrues [momentum-shadow] comparison lines | "live" = Theil-Sen
    # decides fired/score (net still logged as flight recorder). Hot-reload.
    "momentum_theilsen": {
        "mode": "off",
    },
    "scan": {
        "minCompositeScore": 54,  # recalibrated for new weights: P230 zeroed 3 triggers -> denom 2.85->1.85 -> scores ~1.54x. 54 preserves the old-35 selectivity (35*1.54). Without this the gate silently loosened.
        "candleInterval": "5m",
        "candleCount": 100,
        "cacheTtlMs": 50_000,
        # 1h candles don't change mid-hour; cache 10min so we only refetch
        # every 10 scans, keeping the per-cycle weight budget intact.
        "cacheTtlMs1h": 600_000,
    },
    # GATE CONFIGURATION — runtime overrideable (env vars, hotpatch, or MCP)
    "gates": {
        # Liquidity — skips markets below 24h notional volume
        "min_market_volume_usd": 5_000_000,
        "min_hip3_volume_usd": 500_000,
        # Short-side liquidity — thin markets squeeze shorts 17x harder (data from 72h segmentation)
        "min_short_volume_usd": 0,  # 0 = off; 5M+ recommended if shorts bleed on thin book
        # Position management — concurrent and correlation limits
        "max_concurrent": 3,
        "max_crypto_long_correlated": 2,
        # Risk caps — notional, daily, equity exposure
        "max_trade_notional_usd": 300,      # per-trade hard cap
        # Equity-relative daily kill: thr = clamp(pct*equity, min, cap).
        # Drives BOTH the entry gate (risk_gates.daily_loss) and the
        # heartbeat's hard flatten (trading_loop). 0 = disabled.
        "daily_kill_pct_of_equity": 0.10,   # 10% of account equity per day
        "daily_kill_cap_usd": 100,          # absolute ceiling whatever the equity says
        "daily_kill_min_usd": 8,            # small-account floor (noise guard)
        "daily_kill_flatten_mult": 1.25,    # hard flatten fires at mult× the
                                            # halt threshold (grace band for
                                            # the book to recover; 1.0 = old
                                            # flat behaviour)
        "daily_loss_halt": {                # halt timer (breakable, not UTC-locked)
            "halt_min": 360,                # default halt length after a breach
            "release_band_pct": 0.5,        # clear early once day PnL > -(thr*this)
        },
        # Longer per-coin re-entry lock after a max_loss (stop) close; plain
        # loss cooldown stays at `loss_cooldown_min`. 0 = same as plain.
        "stop_loss_cooldown_min": 360,
        "max_total_notional_pct": 1.0,      # total notional as % of equity
        # Regime discipline — counter-trend bar (0.70-0.80 calibrated from 2026-06-06 bleed)
        "counter_regime_min_conf": 0.7,
        # C.6 hard bar: when True, counter-TREND trades cannot use the composite-score
        # escape (conf vs counter_regime_min_conf or nothing). Aligned/neutral trades
        # unaffected. Default False = historical behaviour; live config sets it True.
        "counter_regime_no_composite_escape": False,
        # C.16 follow-up (2026-10-07): divergent-sizing SHADOW accrual. When
        # enabled, an executed entry whose coin_vs_btc_diverged flag is set logs
        # the would-be notional haircut WITHOUT applying it (never touches
        # trade_notional). Accrual-only until the n>=15 pullweight re-judge.
        "divergent_sizing_shadow": {"enabled": False, "haircut": 0.5},
        # C.21 (2026-10-08): favorable-tail ACCRUAL — the size-up counterpart
        # of the divergent haircut. When enabled, every executed entry logs the
        # per-model favorable-tail score (max q90[:6] long / -min q10[:6]
        # short, pp) WITHOUT touching size. threshold_pp marks the survey's
        # top-decile class (~4pp) as hot= in the line. Accrual-only until the
        # n>=40 cross-regime re-judge (see WATCHLIST C.21).
        "fav_tail_shadow": {"enabled": False, "threshold_pp": 4.0},
        # Daily giveback lock — once day peaks, no new entries if PnL retraces > halt_pct
        "daily_giveback_halt_pct": 0.0,
        "daily_giveback_min_peak_usd": 20.0,
    },
}


def get_config() -> Dict[str, Any]:
    """Return the default trigger configuration."""
    return TRIGGER_CONFIG
