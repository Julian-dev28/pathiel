"""Shadow-mode wiring for the free signal suite ([[project_free_signals_suite]]).

Gathers every applicable free signal for a trade CANDIDATE and LOGS what it would
say — without affecting the trade. This is how we validate the signals forward on
real candidates before any of them is allowed to gate an entry (we have no clean
way to backtest GEX/short-vol/whale history).

Signals by asset class:
  - xyz: equity perp  -> GEX (gamma walls/regime) + FINRA short-volume
  - crypto coin       -> Binance aggTrades whale flow (rolling window)
  - both              -> GDELT news catalyst (best-effort, by base symbol)

CRITICAL: every signal here is a NETWORK fetch. To honor the API-amplification
lesson ([[project_atr_sizing_api_amplification]]) this MUST run OFF the execute
hot path — call `run_shadow_async()`, which snapshots the inputs and does all the
fetching on a daemon thread. Underlying signals are TTL-cached + thread-safe.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _base_symbol(coin: str) -> str:
    """xyz:WDC -> WDC ; kPEPE -> PEPE ; BTC -> BTC (for a news query)."""
    t = coin.split(":", 1)[1] if ":" in coin else coin
    if t.startswith("k") and t[1:].isupper() and len(t) > 2:
        t = t[1:]
    return t


def gather_shadow_signals(coin: str, side: str, sub: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute every applicable free signal for `coin`. Each is individually
    try/excepted so one outage never blanks the rest. Returns a compact dict."""
    sub = sub or {}
    out: Dict[str, Any] = {}
    is_hip3 = ":" in (coin or "")

    if is_hip3:
        if sub.get("gex", True):
            try:
                from hermes_trader.agents.options_gex import gex_signal_cached
                r = gex_signal_cached(coin)
                if r:
                    out["gex"] = {"regime": r.regime, "call_wall": r.call_wall,
                                  "put_wall": r.put_wall, "gamma_flip": r.gamma_flip,
                                  "max_pain": r.max_pain, "spot": r.spot}
            except Exception as e:
                logger.debug(f"[shadow] gex {coin}: {e}")
        if sub.get("short_volume", True):
            try:
                from hermes_trader.agents.short_volume import short_volume_signal
                r = short_volume_signal(coin)
                if r:
                    out["short_vol"] = {"ratio": r.ratio, "regime": r.regime, "trend": r.trend}
            except Exception as e:
                logger.debug(f"[shadow] short_vol {coin}: {e}")
    else:
        if sub.get("crypto_whale", True):
            try:
                from hermes_trader.agents.crypto_whale import crypto_whale_signal
                r = crypto_whale_signal(coin, window_minutes=float(sub.get("whale_window_min", 15)))
                if r:
                    out["whale"] = {"bias": r.bias, "net_usd": r.net_usd,
                                    "whale_n": r.whale_n, "window_min": r.window_minutes}
            except Exception as e:
                logger.debug(f"[shadow] whale {coin}: {e}")

    if sub.get("news", True):
        try:
            from hermes_trader.agents.news_catalyst import catalyst_scan
            r = catalyst_scan(_base_symbol(coin), timespan="1h")
            if r and (r.breaking or r.surge_x >= 1.5):
                top = r.headlines[0].title[:80] if r.headlines else ""
                out["news"] = {"breaking": r.breaking, "surge_x": r.surge_x,
                               "n": r.n_recent, "top": top}
        except Exception as e:
            logger.debug(f"[shadow] news {coin}: {e}")
    return out


def shadow_summary(sig: Dict[str, Any]) -> str:
    """One-line human summary for the log."""
    parts = []
    if "gex" in sig:
        g = sig["gex"]
        parts.append(f"gex={g['regime']} wall={g['call_wall']}/{g['put_wall']}")
    if "short_vol" in sig:
        s = sig["short_vol"]
        parts.append(f"shortvol={s['ratio']*100:.0f}%/{s['regime']}/{s['trend']}")
    if "whale" in sig:
        w = sig["whale"]
        parts.append(f"whale={w['bias']} net${w['net_usd']:+,.0f}({w['whale_n']}p/{w['window_min']:g}m)")
    if "news" in sig:
        n = sig["news"]
        flag = "BREAKING" if n["breaking"] else f"surge{n['surge_x']}x"
        parts.append(f"news={flag}:{n['top']!r}")
    return " | ".join(parts) if parts else "(no signal data)"


# ── LIVE enforcement (Veto + Boost) ──────────────────────────────────────────
# CRITICAL: every read here is CACHE-ONLY (allow_fetch=False) — enforcement runs
# ON the execute hot path, so it must NEVER trigger a network fetch (the
# [[project_atr_sizing_api_amplification]] lesson). The async advisor warms the
# caches; on a cold cache enforcement FAILS OPEN (no veto, no boost = behaves
# exactly like today). Scope: the FORCED-OVERRIDE path only, LONG side only.

@dataclass(frozen=True)
class Enforcement:
    veto: bool = False
    veto_reason: str = ""
    boost: bool = False
    boost_reason: str = ""


def enforce_signals(coin: str, side: str, cfg: Dict[str, Any]) -> Enforcement:
    """Decide Veto/Boost for a forced-override LONG candidate from CACHED signals."""
    if (side or "long").lower() != "long":
        return Enforcement()
    en = cfg.get("signal_enforcement") or {}
    if not en.get("enabled", False):
        return Enforcement()
    is_hip3 = ":" in (coin or "")
    do_veto = bool(en.get("veto", True))
    do_boost = bool(en.get("boost", True))

    veto = False
    veto_reason = ""
    boost = False
    boost_reasons = []

    # ── VETO ──
    if do_veto:
        if is_hip3 and bool(en.get("gex_veto", True)):
            try:
                from hermes_trader.agents.options_gex import gex_override_caution
                near = float((cfg.get("gex_signal") or {}).get("caution_near_wall_pct", 1.0))
                sup, why = gex_override_caution(coin, "long", near_wall_pct=near, allow_fetch=False)
                if sup:
                    veto, veto_reason = True, why
            except Exception as e:
                logger.debug(f"[enforce] gex veto {coin}: {e}")
        if (not is_hip3) and not veto:
            try:
                from hermes_trader.agents.crypto_whale import crypto_whale_signal
                w = crypto_whale_signal(coin, window_minutes=float(en.get("whale_window_min", 15)),
                                        allow_fetch=False)
                min_net = float(en.get("whale_veto_min_usd", 250_000))
                if w and w.bias == "whale_selling" and abs(w.net_usd) >= min_net:
                    veto = True
                    veto_reason = (f"whales dumping: net ${w.net_usd:+,.0f} aggressive "
                                   f"sell ({w.whale_n} prints/{w.window_minutes:g}m)")
            except Exception as e:
                logger.debug(f"[enforce] whale veto {coin}: {e}")

    if veto:
        return Enforcement(veto=True, veto_reason=veto_reason)

    # ── BOOST ── (lower the override bar; never bypasses risk/regime gates)
    if do_boost:
        # breaking news on this name
        try:
            from hermes_trader.agents.news_catalyst import catalyst_scan
            r = catalyst_scan(_base_symbol(coin), timespan="1h", allow_fetch=False)
            if r and r.breaking:
                boost = True
                boost_reasons.append(f"breaking news (surge {r.surge_x}x)")
        except Exception as e:
            logger.debug(f"[enforce] news boost {coin}: {e}")
        if is_hip3:
            try:
                from hermes_trader.agents.short_volume import short_volume_signal
                s = short_volume_signal(coin, allow_fetch=False)
                if s and s.regime == "crowded_short_squeeze_fuel":
                    boost = True
                    boost_reasons.append(f"crowded short {s.ratio*100:.0f}% (squeeze fuel)")
            except Exception as e:
                logger.debug(f"[enforce] shortvol boost {coin}: {e}")
        else:
            try:
                from hermes_trader.agents.crypto_whale import crypto_whale_signal
                w = crypto_whale_signal(coin, window_minutes=float(en.get("whale_window_min", 15)),
                                        allow_fetch=False)
                min_net = float(en.get("whale_boost_min_usd", 250_000))
                if w and w.bias == "whale_buying" and w.net_usd >= min_net:
                    boost = True
                    boost_reasons.append(f"whales buying net ${w.net_usd:+,.0f}")
            except Exception as e:
                logger.debug(f"[enforce] whale boost {coin}: {e}")

    return Enforcement(boost=boost, boost_reason="; ".join(boost_reasons))


def run_shadow_async(coin: str, side: str, sub: Optional[Dict[str, Any]] = None) -> None:
    """Fire-and-forget shadow gather+log on a daemon thread. NEVER blocks the
    caller (the execute path), so it can't add latency or amplify the hot path."""
    sub = dict(sub or {})

    def _worker():
        try:
            sig = gather_shadow_signals(coin, side, sub)
            if sig:
                logger.info(f"[shadow-signals] {coin} ({side}): {shadow_summary(sig)}")
        except Exception as e:                              # pragma: no cover
            logger.debug(f"[shadow-signals] {coin} failed: {e}")

    threading.Thread(target=_worker, name=f"shadow-{coin}", daemon=True).start()
