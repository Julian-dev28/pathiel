#!/usr/bin/env python
"""W-CC1: leader profile + viability math for a 10-wallet "copycat" book.

Operator wants a copy mode that mirrors 10 leaderboard wallets at once, each
getting an equal slice of a 25% sleeve (our_equity * 0.25 / 10). Before any
code trades real money the open question is simple: at OUR equity, is a
typical copy of each leader's typical trade even big enough to clear
Hyperliquid's $10.50 order minimum? This script answers that with public
/info reads only (READ-ONLY, no signed/exchange calls).

This is NOT a backtest. EVIDENCE_DOCTRINE.md requires a backtest before a book
earns capital, and W-CP1 already ran one on wallet 0xe282...df29 (one of the
10 leaders here) and REFUTED it: liquidated in every start month at every
leverage tested. This script does not re-litigate that. It only asks: given
the rule is live by operator override anyway, is our account even big enough
to express it without every order getting skipped for being too small? That
is a sizing question, not an edge question.

Public endpoints used (all POST https://api.hyperliquid.xyz/info):
  clearinghouseState       main dex ("") and the "xyz" HIP-3 dex
  spotClearinghouseState   spot balances (USDC + other tokens)
  delegatorSummary         staked HYPE (delegated/undelegated/pending)
  userFillsByTime          last 30 days of fills
  portfolio                30d/all-time PnL curves
  userRole                 vault / subAccount / user
  spotMetaAndAssetCtxs     mark prices, to value non-USDC spot balances
  allMids                  HYPE mid, to value staked HYPE in USD

Paced at ~0.4s between calls (on top of the repo's own token-bucket rate
limiter in pathiel.client.rate_limit) per 10 leaders x ~8 calls = manageable
within HL's ~1200 weight/min budget.

Run: .venv/bin/python research/alpha_swarm/hypotheses/W-CC1_leader_profile.py
Writes: W-CC1_results.json (next to this file).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = str(Path(__file__).resolve().parents[3])
sys.path.insert(0, _REPO)

# Load .env.local before anything resolves our address (resolve_user_address
# reads HYPERLIQUID_MASTER_ADDRESS / HYPERLIQUID_WALLET_ADDRESS from os.environ
# at call time, not import time, but populate it up front regardless — same
# contract as conftest.py / scripts/_state_env.load_env_local).
_ENV_FILE = Path(_REPO) / ".env.local"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from pathiel.client.hl_client import _http_post, resolve_user_address  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "W-CC1_results.json")

PACE_S = 0.5
MIN_ORDER_USD = 10.50                 # pathiel.client.exchange.MIN_ORDER_USD
MIN_ORDER_BUMP_FRAC = 0.25            # task-specified viability floor (25% of the minimum)
SLEEVE_FRAC = 0.25                    # matches .agent-config.json copy_trade.sleeve_frac convention
N_LEADERS = 10
DAY_MS = 86_400_000
LOOKBACK_DAYS = 30
DEXES = ("", "xyz")

LEADERS = [
    "0x95da8596c44dd09f4b8becce87ad3b7894fb2328",
    "0xac142fee46f8dacbfef23e1e41d79f1f7233487e",
    "0xe2823659be02e0f48a4660e4da008b5e1abfdf29",
    "0x0b38c01118342098d6f108cc3741349089cd9590",
    "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1",
    "0x04a97ae7f350a22cd0cdb6b1875e8905b76495aa",
    "0xe09726ff25f5001f37b15049f54116cb83d7d0fe",
    "0xf5079c84c34051d7c4cd87494fda534fe7ecdf6d",
    "0xd260b2216b735277da6771564a01c04856e78321",
    "0xbe10fd36393c8b677281d0bc2cf1bb8c98ad4b34",
]

# Fills with these `dir` values are real perp position events (opens / closes /
# flips). "Buy"/"Sell" are spot fills; "Spot Dust Conversion" is housekeeping.
# A flip ("Long > Short" / "Short > Long") both closes the old leg and opens a
# new one in a single fill.
_OPEN_DIRS = ("Open Long", "Open Short")
_CLOSE_DIRS = ("Close Long", "Close Short")
_FLIP_DIRS = ("Long > Short", "Short > Long")


def post(payload: Dict[str, Any], timeout: int = 20, retries: int = 9) -> Any:
    """_http_post already rate-limits + retries transient None; this adds a
    few more attempts for the research run (no silent gaps in the record).
    Four parallel research agents share this IP's rate budget this session,
    so 429 bursts are expected — back off hard rather than give up."""
    for attempt in range(retries):
        raw = _http_post("/info", payload, timeout=timeout)
        time.sleep(PACE_S)
        if raw is not None:
            return raw
        time.sleep(min(2.0 ** attempt, 25.0))
    raise RuntimeError(f"info request failed {retries}x: {payload.get('type')}")


def page_fills(user: str, start_ms: int, end_ms: int, max_pages: int = 12) -> List[Dict[str, Any]]:
    """Page userFillsByTime forward. HL returns at most ~2000 rows per call;
    most wallets clear 30d in one page, but page anyway so a very active
    leader's record isn't silently truncated."""
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    start = start_ms
    for _ in range(max_pages):
        batch = post({"type": "userFillsByTime", "user": user,
                     "startTime": start, "endTime": end_ms})
        if not isinstance(batch, list) or not batch:
            break
        fresh = [r for r in batch if r.get("hash") not in seen]
        for r in fresh:
            seen.add(r.get("hash"))
        rows.extend(fresh)
        if len(batch) < 1900:
            break
        start = max(int(r["time"]) for r in batch)
    rows.sort(key=lambda r: r["time"])
    return rows


# ── fill analysis ─────────────────────────────────────────────────────────

def hold_hours(fills: List[Dict[str, Any]]) -> Tuple[Optional[float], int]:
    """Median hold time in hours for trips that both open AND close within the
    fetched fill window, derived per-coin from the dir/startPosition sequence.

    Returns (median_hours_or_None, n_complete_trips). A trip open before the
    window start or still open at fetch time is excluded by construction (its
    open or close fill is outside what we have) — this undercounts, never
    fabricates a duration. That is the "if derivable" the operator asked for.
    """
    by_coin: Dict[str, List[Dict[str, Any]]] = {}
    for f in fills:
        if f.get("dir") in _OPEN_DIRS + _CLOSE_DIRS + _FLIP_DIRS:
            by_coin.setdefault(f["coin"], []).append(f)

    durations: List[float] = []
    for coin, rows in by_coin.items():
        rows.sort(key=lambda r: r["time"])
        open_t: Optional[int] = None
        for f in rows:
            d = f.get("dir")
            if d in _OPEN_DIRS:
                if open_t is None:
                    open_t = f["time"]
            elif d in _CLOSE_DIRS:
                if open_t is not None:
                    durations.append((f["time"] - open_t) / 3_600_000)
                    open_t = None
            elif d in _FLIP_DIRS:
                if open_t is not None:
                    durations.append((f["time"] - open_t) / 3_600_000)
                open_t = f["time"]   # the flip also opens the new leg
    if not durations:
        return None, 0
    return statistics.median(durations), len(durations)


def fill_stats(fills: List[Dict[str, Any]]) -> Dict[str, Any]:
    count = len(fills)
    coins = {f["coin"] for f in fills}
    opens = [f for f in fills if f.get("dir") in _OPEN_DIRS]
    open_notionals = [abs(float(f["sz"])) * float(f["px"]) for f in opens]
    xyz_fills = sum(1 for f in fills if f["coin"].startswith("xyz:"))
    med_hold, n_trips = hold_hours(fills)
    return {
        "count_30d": count,
        "distinct_coins_30d": len(coins),
        "opens_per_day": round(len(opens) / LOOKBACK_DAYS, 3) if count else 0.0,
        "n_opens_30d": len(opens),
        "median_open_notional_usd": round(statistics.median(open_notionals), 2) if open_notionals else None,
        "frac_fills_on_xyz": round(xyz_fills / count, 4) if count else 0.0,
        "median_hold_hours": round(med_hold, 2) if med_hold is not None else None,
        "n_complete_trips_30d": n_trips,
    }


# ── account reads ────────────────────────────────────────────────────────

def dex_state(user: str, dex: str) -> Dict[str, Any]:
    st = post({"type": "clearinghouseState", "user": user, "dex": dex})
    if not isinstance(st, dict):
        return {"accountValue": 0.0, "totalNtlPos": 0.0, "positions": []}
    ms = st.get("marginSummary") or {}
    positions = []
    for ap in st.get("assetPositions", []) or []:
        p = ap.get("position") or {}
        szi = float(p.get("szi") or 0)
        if szi == 0:
            continue
        coin = p["coin"] if (not dex or ":" in p["coin"]) else f"{dex}:{p['coin']}"
        lev = p.get("leverage") or {}
        positions.append({
            "coin": coin,
            "side": "long" if szi > 0 else "short",
            "szi": szi,
            "entry_px": float(p.get("entryPx") or 0),
            "notional_usd": abs(float(p.get("positionValue") or 0)),
            "leverage_type": lev.get("type"),
            "leverage_value": lev.get("value"),
            "max_leverage": p.get("maxLeverage"),
            "liquidation_px": float(p["liquidationPx"]) if p.get("liquidationPx") else None,
            "unrealized_pnl": float(p.get("unrealizedPnl") or 0),
        })
    return {
        "accountValue": float(ms.get("accountValue") or 0),
        "totalNtlPos": float(ms.get("totalNtlPos") or 0),
        "totalMarginUsed": float(ms.get("totalMarginUsed") or 0),
        "positions": positions,
    }


def spot_state(user: str, price_by_token: Dict[str, Tuple[float, float]]) -> Dict[str, Any]:
    """price_by_token: coin -> (markPx, dayNtlVlm_usd). A balance's mark-price
    USD value is flagged illiquid when it is far larger than the whole
    market's day volume — that value is not realizable at that mark, it is a
    mark-to-fiction on a thin book (seen live: 10.2M "MAX" tokens marked at
    $10.96 = $111.7M against a token that trades $32.8K/day)."""
    sp = post({"type": "spotClearinghouseState", "user": user})
    balances = sp.get("balances", []) if isinstance(sp, dict) else []
    usdc = 0.0
    other: List[Dict[str, Any]] = []
    for b in balances:
        coin = b.get("coin", "")
        total = float(b.get("total") or 0)
        if total == 0:
            continue
        if coin in ("USDC", "USDT", "USD"):
            usdc += total
            continue
        px, vlm = price_by_token.get(coin, (None, None))
        usd = round(total * px, 2) if px else None
        entry = {"coin": coin, "amount": total, "usd": usd}
        if usd is not None and vlm is not None and usd > 10 * max(vlm, 1.0):
            entry["illiquid_mark_price_fiction"] = True
            entry["day_volume_usd"] = round(vlm, 2)
        other.append(entry)
    return {"usdc": usdc, "other": other}


def staking_state(user: str, hype_px: float) -> Dict[str, Any]:
    d = post({"type": "delegatorSummary", "user": user})
    if not isinstance(d, dict):
        return {"delegated": 0.0, "undelegated": 0.0, "pending_withdrawal": 0.0, "usd": 0.0}
    delegated = float(d.get("delegated") or 0)
    undelegated = float(d.get("undelegated") or 0)
    pending = float(d.get("totalPendingWithdrawal") or 0)
    return {
        "delegated_hype": delegated,
        "undelegated_hype": undelegated,
        "pending_withdrawal_hype": pending,
        "usd": round((delegated + undelegated + pending) * hype_px, 2) if hype_px else None,
    }


def portfolio_pnl(user: str) -> Dict[str, Optional[float]]:
    raw = post({"type": "portfolio", "user": user})
    buckets = {label: data for label, data in raw} if isinstance(raw, list) else {}

    def last_pnl(label: str) -> Optional[float]:
        hist = (buckets.get(label) or {}).get("pnlHistory") or []
        return float(hist[-1][1]) if hist else None

    return {
        "pnl_30d_usd": last_pnl("month"),
        "pnl_all_time_usd": last_pnl("allTime"),
        "note": "'month' is HL's own bucket (calendar-ish, not strictly trailing-30d); "
                "'allTime' is since the account's first recorded activity.",
    }


def role_of(user: str) -> str:
    r = post({"type": "userRole", "user": user})
    if isinstance(r, dict) and r.get("role"):
        return str(r["role"])
    return "unknown"


def spot_price_map() -> Dict[str, Tuple[float, float]]:
    """token name -> (USD mark price, day notional volume USD), from pairs
    quoted in USDC. The volume travels with the price so callers can tell a
    real mark from a mark-to-fiction on a thin book."""
    raw = post({"type": "spotMetaAndAssetCtxs"})
    if not isinstance(raw, list) or len(raw) != 2:
        return {}
    meta, ctxs = raw
    tokens = {t["index"]: t["name"] for t in meta.get("tokens", [])}
    out: Dict[str, Tuple[float, float]] = {}
    for i, u in enumerate(meta.get("universe", [])):
        try:
            base_name = tokens[u["tokens"][0]]
            quote_name = tokens[u["tokens"][1]]
        except (KeyError, IndexError):
            continue
        if quote_name != "USDC":
            continue
        ctx = ctxs[i] if i < len(ctxs) else {}
        mark = ctx.get("markPx")
        if mark is not None:
            out[base_name] = (float(mark), float(ctx.get("dayNtlVlm") or 0))
    return out


def hype_mid() -> float:
    mids = post({"type": "allMids"})
    try:
        return float(mids.get("HYPE", 0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


# ── per-leader profile ──────────────────────────────────────────────────

def profile_leader(user: str, price_by_token: Dict[str, float], hype_px: float) -> Dict[str, Any]:
    now = int(time.time() * 1000)
    main = dex_state(user, "")
    xyz = dex_state(user, "xyz")
    spot = spot_state(user, price_by_token)
    staking = staking_state(user, hype_px)
    fills = page_fills(user, now - LOOKBACK_DAYS * DAY_MS, now)
    pnl = portfolio_pnl(user)
    role = role_of(user)

    # His equity as the sizing formula sees it (copy_trade_live.LiveVenue.
    # leader_state): perp accountValue on every queried dex + spot USDC. His
    # spot USDC is his collateral (W-CP1_copy_0xe282.md).
    equity = main["accountValue"] + xyz["accountValue"] + spot["usdc"]
    total_ntl = main["totalNtlPos"] + xyz["totalNtlPos"]
    positions = main["positions"] + xyz["positions"]

    liq_distances = []
    for p in positions:
        ml = p.get("max_leverage")
        if ml:
            liq_distances.append({
                "coin": p["coin"],
                "max_isolated_leverage": ml,
                # Same approximation the repo's own backtest used (W-CP1:
                # "liquidation checked against notional / (2 x maxLeverage)"):
                # maintenance margin ~= half the initial margin fraction, so
                # the adverse move needed to liquidate at max leverage is
                # ~= 1 / (2 x maxLeverage) of price.
                "distance_to_liq_pct_at_max_lev": round(100.0 / (2 * ml), 3),
            })

    return {
        "address": user,
        "role": role,
        "equity_main_usd": round(main["accountValue"], 2),
        "equity_xyz_usd": round(xyz["accountValue"], 2),
        "spot_usdc": round(spot["usdc"], 2),
        "spot_other": spot["other"],
        "staked_hype": staking,
        "equity_for_sizing_usd": round(equity, 2),
        "total_notional_usd": round(total_ntl, 2),
        "effective_leverage": round(total_ntl / equity, 3) if equity > 0 else None,
        "positions": positions,
        "liquidation_distance_at_max_leverage": liq_distances,
        "fills_30d": fill_stats(fills),
        "pnl": pnl,
    }


def our_account() -> Dict[str, Any]:
    user = resolve_user_address()
    if not user:
        return {"address": None, "equity_usd": 0.0, "error": "resolve_user_address() returned empty "
                "(HYPERLIQUID_MASTER_ADDRESS / HYPERLIQUID_WALLET_ADDRESS not set)"}
    main = dex_state(user, "")
    xyz = dex_state(user, "xyz")
    return {
        "address": user,
        "equity_main_usd": round(main["accountValue"], 2),
        "equity_xyz_usd": round(xyz["accountValue"], 2),
        "equity_usd": round(main["accountValue"] + xyz["accountValue"], 2),
    }


# ── viability math ───────────────────────────────────────────────────────

def viability(leader: Dict[str, Any], our_equity: float) -> Dict[str, Any]:
    per_leader_alloc = our_equity * SLEEVE_FRAC / N_LEADERS
    med_open = (leader.get("fills_30d") or {}).get("median_open_notional_usd")
    his_equity = leader.get("equity_for_sizing_usd") or 0.0

    if not med_open or his_equity <= 0:
        return {
            "per_leader_allocation_usd": round(per_leader_alloc, 2),
            "typical_open_ratio_of_his_equity": None,
            "our_copy_notional_usd": None,
            "below_min_order": None,
            "below_bump_floor_25pct": None,
            "min_equity_for_median_copy_usd": None,
            "why": "no derivable median open (no 'Open' fills in the 30d window) "
                   "or his sizing-equity is zero/negative",
        }

    ratio = med_open / his_equity
    our_notional = per_leader_alloc * ratio
    below_min = our_notional < MIN_ORDER_USD
    below_bump = our_notional < MIN_ORDER_USD * MIN_ORDER_BUMP_FRAC
    # our_equity * SLEEVE_FRAC / N_LEADERS * ratio >= MIN_ORDER_USD
    #   => our_equity >= MIN_ORDER_USD * N_LEADERS / (SLEEVE_FRAC * ratio)
    min_equity = (MIN_ORDER_USD * N_LEADERS / (SLEEVE_FRAC * ratio)) if ratio > 0 else None

    return {
        "per_leader_allocation_usd": round(per_leader_alloc, 2),
        "typical_open_ratio_of_his_equity": round(ratio, 6),
        "our_copy_notional_usd": round(our_notional, 2),
        "below_min_order": below_min,
        "below_bump_floor_25pct": below_bump,
        "min_equity_for_median_copy_usd": round(min_equity, 2) if min_equity else None,
    }


def main() -> None:
    print(f"[W-CC1] our account + {len(LEADERS)} leaders, read-only /info calls, paced {PACE_S}s")

    ours = our_account()
    our_equity = ours.get("equity_usd", 0.0)
    print(f"[W-CC1] our equity: ${our_equity:.2f}" if ours.get("address")
          else f"[W-CC1] WARNING: {ours.get('error')}")

    price_by_token = spot_price_map()
    hype_px = hype_mid()
    print(f"[W-CC1] HYPE mid ${hype_px:.4f}, {len(price_by_token)} spot pairs priced in USDC")

    leaders_out = []
    for i, addr in enumerate(LEADERS, 1):
        print(f"[W-CC1] ({i}/{len(LEADERS)}) profiling {addr} ...")
        prof = None
        last_err = None
        for leader_attempt in range(3):
            try:
                prof = profile_leader(addr, price_by_token, hype_px)
                break
            except Exception as e:
                last_err = e
                print(f"[W-CC1]   attempt {leader_attempt + 1}/3 failed: {e}; cooling off 15s")
                time.sleep(15)
        if prof is None:
            print(f"[W-CC1]   FAILED after 3 attempts: {last_err}")
            leaders_out.append({"address": addr, "error": str(last_err)})
            continue
        prof["viability_at_our_equity"] = viability(prof, our_equity)
        leaders_out.append(prof)
        v = prof["viability_at_our_equity"]
        print(f"[W-CC1]   equity ${prof['equity_for_sizing_usd']:.0f}  "
              f"eff_lev {prof['effective_leverage']}  "
              f"fills30d {prof['fills_30d']['count_30d']}  "
              f"copy_notional {v.get('our_copy_notional_usd')}")

    result = {
        "generated_ms": int(time.time() * 1000),
        "leaders": LEADERS,
        "our_account": ours,
        "sleeve_frac": SLEEVE_FRAC,
        "n_leaders": N_LEADERS,
        "min_order_usd": MIN_ORDER_USD,
        "min_order_bump_floor_usd": round(MIN_ORDER_USD * MIN_ORDER_BUMP_FRAC, 3),
        "leader_profiles": leaders_out,
    }
    with open(OUT, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=False)
    print(f"[W-CC1] wrote {OUT}")


if __name__ == "__main__":
    main()
