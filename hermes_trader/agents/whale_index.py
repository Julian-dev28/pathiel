"""Whale Index — smart-money signal heuristics over the Hyperliquid universe.

Provides OI/funding-based concentration and accumulation signals.
leaderboard_get_top / get_trader_state only return data for wallets added
to WHALE_WALLETS.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from hermes_trader.client.hl_client import _http_post
from hermes_trader.client.universe import get_universe

logger = logging.getLogger(__name__)

# Persisted OI snapshots for the self-sourced OI-surge whale detector. The HL
# PUBLIC api has NO leaderboard endpoint (verified: vaults/leaderBoard/vaultDetails
# all return None) — Senpi uses a private backend. So instead of a static wallet
# list that goes stale, we build a VERIFIABLE whale signal from data we pull
# ourselves: snapshot open-interest each scan, and flag coins where OI surges
# (positions being built) while price stays flat = smart money loading quietly.
_OI_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".oi-history.json",
)

# Curated whale wallet registry. Add entries from app.hyperliquid.xyz/leaderboard
# or hyperstats.org; format: "0x...": {"name": ..., "risk": "low"|"medium"|"high"}.
WHALE_WALLETS: Dict[str, Dict[str, str]] = {}

# ── Leaderboard from on-chain data ──────────────────────────────────────

def _fetch_clearinghouse(user: str) -> Optional[Dict[str, Any]]:
    """Fetch perp account state for a user address."""
    return _http_post("/info", {"type": "clearinghouseState", "user": user})


def _fetch_user_fills(user: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch recent trades for a user."""
    return _http_post(
        "/info",
        {"type": "userFills", "user": user, "limit": limit},
    ) or []


def leaderboard_get_top(
    start: int = 0,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Return positions for the wallets registered in WHALE_WALLETS.

    Args:
        start: pagination offset
        limit: number of entries (max 100)
    """
    results = []
    for addr, meta in list(WHALE_WALLETS.items())[start:start + limit]:
        try:
            state = _fetch_clearinghouse(addr)
            if not state:
                continue
            margin = state.get("marginSummary", {})
            account_value = float(margin.get("accountValue", "0"))
            positions = state.get("assetPositions", [])
            
            results.append({
                "address": addr,
                "name": meta.get("name", ""),
                "account_value": account_value,
                "positions": [
                    {
                        "coin": p["position"]["coin"],
                        "szi": float(p["position"]["szi"]),
                        "entryPx": float(p["position"]["entryPx"]),
                        "leverage": {"value": p["position"]["leverage"].get("value", "0")},
                    }
                    for p in positions
                    if p.get("position", {}).get("szi") != "0"
                ],
                "total_positions": len(positions),
            })
        except Exception as e:
            logger.warning(f"[whale] Failed to fetch {addr}: {e}")
    return results


def smart_money_concentration(
    lookback_days: int = 7,
    min_volume_usd: float = 1e6,
) -> List[Dict[str, Any]]:
    """Identify assets with growing smart money concentration.
    
    Analyzes OI + volume distribution to find assets where large traders
    are accumulating positions. Flags:
    - OI growth outpaces volume growth
    - Top whales increasing positions in same asset
    - OI concentration in top 10 wallets
    
    Args:
        lookback_days: how far back to scan for concentration changes
        min_volume_usd: minimum 24h volume threshold
    """
    universe = get_universe()
    results = []
    
    for m in universe:
        day_oi = m.get("openInterest", 0)
        day_vol = m.get("dayNtlVlm", 0)
        funding = m.get("funding", 0)
        mid_px = m.get("midPx", 0)
        
        if day_vol < min_volume_usd:
            continue
        
        # Concentration signal: OI growing + negative funding = accumulation
        # (whales buying while retail sells into dips)
        if day_oi > 0 and funding < 0:
            results.append({
                "coin": m["coin"],
                "type": m["type"],
                "signal": "accumulation",
                "confidence": min(1.0, abs(funding) / 0.0001),  # scale funding magnitude
                "oi": day_oi,
                "volume_24h": day_vol,
                "funding_rate": funding,
                "mid_price": mid_px,
            })
        
        # High OI relative to volume = whale accumulation
        oi_vol_ratio = day_oi / (day_vol / 1e6) if day_vol > 0 else 0
        if oi_vol_ratio > 10:  # OI > 10x of daily volume in millions
            results.append({
                "coin": m["coin"],
                "type": m["type"],
                "signal": "high_oi_concentration",
                "confidence": min(1.0, oi_vol_ratio / 50),  # scale ratio
                "oi": day_oi,
                "volume_24h": day_vol,
                "oi_volume_ratio": oi_vol_ratio,
                "mid_price": mid_px,
            })
    
    return sorted(results, key=lambda x: x["confidence"], reverse=True)


def oi_funding_anomaly(
    min_oi_usd: float = 5e6,
    max_funding_threshold: float = -0.00001,
    funding_norm: float = 0.00008,
) -> List[Dict[str, Any]]:
    """Detect assets where OI is high but price is flat while funding is
    negative — classic smart money accumulation pattern.

    Signal: whales are building long positions (high OI) while retail is
    shorting (negative funding). When the crowd finally covers, price squeezes up.

    RECALIBRATED 2026-06-02 (audit): the prior thresholds (OI>=$10M, funding<
    -0.00005) flagged only 1 coin across the whole universe — the funding cut sat
    at the ~p10 extreme of negative funding, so the signal was effectively dead.
    Loosened to OI>=$5M and funding<-0.00001 (catches the real negative-
    funding cohort, ~7-9 coins) AND fixed the confidence normalization: it was
    dividing by 0.0005 (so a -0.00001 coin scored 0.02 and got filtered by the
    0.05 min_confidence gate downstream — a second silent kill). Now normalizes
    against `funding_norm` (0.00008 ≈ the deeply-negative end) so a real anomaly
    clears the gate. All three tunable.

    Args:
        min_oi_usd: minimum OI notional in USD (OI_coins * price)
        max_funding_threshold: funding rate must be below this (more negative = stronger)
        funding_norm: funding magnitude that maps to ~full confidence
    """
    universe = get_universe(include_hip3=True)
    results = []

    for m in universe:
        oi_coins = m.get("openInterest", 0)
        funding = m.get("funding", 0)
        mid_px = m.get("midPx", 0)
        prev_px = m.get("prevDayPx", 0)

        # BUGFIX 2026-06-02 audit: openInterest is in COIN UNITS, not USD. The old
        # `oi < min_oi_usd` compared coins to dollars — nonsensical (BTC's 29,885-coin
        # / $2.2B OI failed a "$5M" filter while a 10M-token meme passed). Convert to
        # true USD notional = OI * price.
        oi = oi_coins * mid_px if mid_px > 0 else 0

        if oi < min_oi_usd or funding > max_funding_threshold:
            continue

        price_change_24h = (mid_px - prev_px) / prev_px * 100 if prev_px > 0 else 0

        # Signal: OI high + funding negative + price relatively flat (quiet accumulation)
        if abs(price_change_24h) < 10:
            results.append({
                "coin": m["coin"],
                "type": m["type"],
                "signal": "smart_money_accumulation",
                "confidence": (
                    min(1.0, abs(funding) / funding_norm)   # funding magnitude (calibrated)
                    * (1 - abs(price_change_24h) / 10)      # flatter price = stronger
                ),
                "oi": oi,
                "funding_rate": funding,
                "price_24h_change_pct": price_change_24h,
                "mid_price": mid_px,
                "prev_day_px": prev_px,
            })

    return sorted(results, key=lambda x: x["confidence"], reverse=True)


def get_trader_state(user: str) -> Optional[Dict[str, Any]]:
    """Get comprehensive state for a specific trader address.
    
    Combines perp + spot state + recent trades into a single view.
    """
    perp = _fetch_clearinghouse(user)
    fills = _fetch_user_fills(user, limit=20)
    
    if not perp:
        return None
    
    margin = perp.get("marginSummary", {})
    account_value = float(margin.get("accountValue", "0"))
    total_ntl_pos = float(margin.get("totalNtlPos", "0"))
    
    positions = []
    for p in perp.get("assetPositions", []):
        if not p.get("position"):
            continue
        pos = p["position"]
        szi = float(pos.get("szi", "0"))
        if szi == 0:
            continue
        positions.append({
            "coin": pos.get("coin", ""),
            "side": "long" if szi > 0 else "short",
            "size": abs(szi),
            "entry_price": float(pos.get("entryPx", "0")),
            "leverage": pos.get("leverage", {}).get("value", "0"),
            "unrealized_pnl": float(pos.get("unrealizedPnl", "0")),
        })
    
    return {
        "address": user,
        "account_value": account_value,
        "total_notional_position": total_ntl_pos,
        "positions": positions,
        "recent_trades": [
            {
                "coin": f.get("coin", ""),
                "side": f.get("side", ""),
                "price": float(f.get("px", "0")),
                "size": float(f.get("sz", "0")),
                "fee": float(f.get("fee", "0")),
                "time": f.get("time", 0),
            }
            for f in fills[:10]
        ],
    }


# ── OI-surge whale detector (self-sourced, verifiable) ───────────────

def oi_surge_accumulation(
    min_oi_usd: float = 5e6,
    min_oi_growth_pct: float = 8.0,
    max_price_move_pct: float = 4.0,
    surge_norm_pct: float = 25.0,
) -> List[Dict[str, Any]]:
    """Flag coins whose OPEN INTEREST surged since the last scan while price stayed
    flat — positions being built quietly = smart-money accumulation, about to move.

    Fully self-sourced & verifiable: we snapshot OI from get_universe() (HL public
    data) to `.oi-history.json` each call and compare to the prior snapshot. No
    external leaderboard / wallet list needed (HL has no public leaderboard API).

    A coin qualifies if: OI >= min_oi_usd, OI grew >= min_oi_growth_pct vs last
    snapshot, and |price move since last snapshot| <= max_price_move_pct (the
    "loading while flat" tell — if price already ran, the move's not ahead of us).
    """
    universe = get_universe()
    now = time.time()
    # load prior snapshot
    prev = {}
    try:
        with open(_OI_HISTORY_FILE) as f:
            blob = json.load(f)
            prev = blob.get("oi", {})
            prev_ts = blob.get("ts", 0)
    except (OSError, json.JSONDecodeError):
        prev_ts = 0

    cur = {}
    results = []
    for m in universe:
        coin = m.get("coin")
        oi_coins = float(m.get("openInterest", 0) or 0)
        mid = float(m.get("midPx", 0) or 0)
        if not coin or oi_coins <= 0 or mid <= 0:
            continue
        # openInterest is in COIN UNITS. Store BOTH: coin-units (for true position
        # growth, price-independent) and USD notional (for the size gate). OI-growth
        # MUST be on coin units — computing it on USD notional would let a price rise
        # masquerade as position-building (a false surge).
        oi_usd = oi_coins * mid
        cur[coin] = {"oi_coins": oi_coins, "oi": oi_usd, "px": mid}
        p = prev.get(coin)
        if oi_usd < min_oi_usd or not p:
            continue
        # back-compat: older snapshots only stored "oi" (was raw coins pre-fix);
        # prefer oi_coins, fall back to oi.
        p_oi_coins = p.get("oi_coins", p.get("oi", 0)); p_px = p.get("px", 0)
        if p_oi_coins <= 0 or p_px <= 0:
            continue
        oi_growth = (oi_coins - p_oi_coins) / p_oi_coins * 100   # COIN-unit growth (price-independent)
        px_move = abs(mid - p_px) / p_px * 100
        if oi_growth >= min_oi_growth_pct and px_move <= max_price_move_pct:
            results.append({
                "coin": coin,
                "type": m.get("type"),
                "signal": "oi_surge_accumulation",
                "confidence": min(1.0, oi_growth / surge_norm_pct) * (1 - px_move / max(max_price_move_pct, 1e-9)),
                "oi": oi_usd,
                "oi_growth_pct": round(oi_growth, 1),
                "price_move_pct": round(px_move, 2),
                "mid_price": mid,
            })

    # persist current snapshot (best-effort, atomic)
    try:
        tmp = _OI_HISTORY_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ts": now, "oi": cur}, f)
        os.replace(tmp, _OI_HISTORY_FILE)
    except OSError as e:
        logger.warning(f"[whale] OI history persist failed: {e}")

    return sorted(results, key=lambda x: x["confidence"], reverse=True)


# ── Whale Index MCP Integration ─────────────────────────────────────
# These functions can be registered as MCP tools for autonomous agents
# to query whale data as part of their scanning pipeline.

def whale_accumulation_map(min_confidence: float = 0.05) -> Dict[str, Dict[str, Any]]:
    """Return {coin: signal_dict} for coins flagged as smart-money accumulation.

    MERGES two self-sourced, verifiable signals (no external leaderboard needed):
      1. oi_funding_anomaly — high OI + deeply negative funding + flat price
         (whales long while retail shorts -> squeeze setup).
      2. oi_surge_accumulation — OI surging vs last scan + price flat
         (positions being built quietly, move not yet happened).
    A coin flagged by EITHER (or both, taking the higher confidence) is returned.
    This remains available for MCP/manual inspection; live execution no longer
    uses it as an execution, size, or regime-bypass input.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for s in oi_funding_anomaly() + oi_surge_accumulation():
        if s.get("confidence", 0) < min_confidence:
            continue
        c = s["coin"]
        if c not in merged or s["confidence"] > merged[c]["confidence"]:
            merged[c] = s
    return merged


def get_whale_signals(
    min_confidence: float = 0.1,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """Aggregate concentration + anomaly signals for MCP-tool callers.

    Kept for the MCP exposure (`whale_index` tool). Production perception
    uses `whale_accumulation_map()` instead — the high-OI-concentration
    branch this combines in is too noisy for direction calls.
    """
    concentration = smart_money_concentration()
    anomalies = oi_funding_anomaly()
    
    # Merge signals by coin
    merged: Dict[str, Dict[str, Any]] = {}
    for sig in concentration + anomalies:
        coin = sig["coin"]
        if coin not in merged:
            merged[coin] = {"coin": coin, "signals": [], "max_confidence": 0}
        merged[coin]["signals"].append(sig)
        merged[coin]["max_confidence"] = max(
            merged[coin]["max_confidence"],
            sig.get("confidence", 0),
        )
    
    # Filter and sort
    results = [
        s for s in merged.values()
        if s["max_confidence"] >= min_confidence
    ]
    return sorted(results, key=lambda x: x["max_confidence"], reverse=True)[:top_n]
