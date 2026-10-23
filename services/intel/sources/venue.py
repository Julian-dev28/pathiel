"""Hyperliquid venue positioning: funding, open interest, and volume for
every desk instrument, straight off the public `/info` endpoint — no key.

Verified live 2026-10-23 against api.hyperliquid.xyz/info:
  metaAndAssetCtxs {"dex": "xyz"}   -> universe + per-asset ctx (funding,
                                       openInterest, dayNtlVlm, markPx, ...)
                                       for every xyz-dex instrument in one
                                       call; {} (no "dex") for the main dex.
  fundingHistory {"coin","startTime","endTime"} -> hourly funding-rate
                                       history for one coin, real numbers,
                                       confirmed back 30+ days.

Long/short skew of the top traders on a coin: NOT built. Checked live
2026-10-23 — metaAndAssetCtxs's per-asset ctx has no such field (funding,
openInterest, premium, dayNtlVlm, markPx/oraclePx/midPx, impactPxs,
dayBaseVlm — nothing position-side), and openInterestHistory /
assetHistory / candleSnapshot all reject a bare {"type", "coin"} body
with "Failed to deserialize the JSON body into the target type" (not a
documented, stable shape). The brief's own hedge — "if the public info
API exposes it" — resolves to false; the one place that DOES exist (the
public leaderboard at stats-data.hyperliquid.xyz, see git history before
dc884e4 / pathiel/agents/hyperfeed.py) only gives per-TRADER positions,
so computing a per-COIN skew would mean one clearinghouseState lookup per
top trader per instrument — tens of extra HTTP calls per desk refresh for
a number this service cannot even verify is accurate (the public
leaderboard is a sample of top traders, not the full book). Not worth the
latency or the false precision; documented here instead of silently
omitted, per the brief's own "fine to document why instead" escape valve.

Open interest has no historical endpoint at all (see above), so its 24h
change is NOT read from Hyperliquid — it is grown locally: every REAL
(non-cached) fetch appends one {ts, oi_usd} sample to a small rolling
history file via services/intel/cache.py's append_history/read_history,
and the 24h change is the nearest sample 20-30h old. Until that much
history exists the 24h OI figure is reported as None with a note saying
so, never a guess.

Crowding flags (brief): funding at/above the 90th percentile of its own
trailing 30 days, and OI up >= 20% in a day. Both are carried in the
funding/OI DataPoint's `note` field rather than as separate rows — one
hard number plus its own crowding context, not two coupled facts split
across two ids.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from contracts.strategies.models import DataPoint, DeskSpec, Instrument
from services.intel import cache, http_client
from services.intel.textnorm import stable_id

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
FUNDING_LOOKBACK_DAYS = 30
MIN_FUNDING_SAMPLES_FOR_PCTL = 24 * 5  # 5 days of hourly samples, minimum
FUNDING_PCTL_CROWDED = 0.90
OI_JUMP_CROWDED_PCT = 0.20
OI_HISTORY_KEEP_S = 32 * 86400.0
OI_24H_WINDOW_S = (20 * 3600.0, 30 * 3600.0)  # accept a sample 20-30h old as "24h ago"
FUNDING_HTTP_TIMEOUT_S = 5.0
FUNDING_HTTP_RETRIES = 0  # best-effort per-coin call; see module docstring on why a
                          # slow or dead coin must not block the whole desk's packet


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _dex_of(coin: str) -> Optional[str]:
    return coin.split(":", 1)[0] if ":" in coin else None


def _instruments_by_dex(instruments: List[Instrument]) -> Dict[Optional[str], List[Instrument]]:
    out: Dict[Optional[str], List[Instrument]] = {}
    for i in instruments:
        out.setdefault(_dex_of(i.coin), []).append(i)
    return out


def fetch_meta_and_ctxs(dex: Optional[str]) -> Tuple[Optional[Dict[str, Dict[str, Any]]], Optional[str]]:
    """One dex's `metaAndAssetCtxs` -> {coin_name: ctx_dict}."""
    body: Dict[str, Any] = {"type": "metaAndAssetCtxs"}
    if dex:
        body["dex"] = dex
    data, err = http_client.fetch_json_post(HL_INFO_URL, body, timeout_s=6.0)
    if err:
        return None, f"venue:meta_and_ctxs:{dex or 'main'}:{err}"
    if not (isinstance(data, list) and len(data) == 2 and isinstance(data[0], dict)):
        return None, f"venue:meta_and_ctxs:{dex or 'main'}:bad_shape"
    universe = data[0].get("universe") or []
    ctxs = data[1] if isinstance(data[1], list) else []
    return {u.get("name"): c for u, c in zip(universe, ctxs) if u.get("name")}, None


def fetch_funding_history(coin: str, now: datetime) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    start_ms = int((now - timedelta(days=FUNDING_LOOKBACK_DAYS)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    data, err = http_client.fetch_json_post(
        HL_INFO_URL, {"type": "fundingHistory", "coin": coin, "startTime": start_ms, "endTime": end_ms},
        timeout_s=FUNDING_HTTP_TIMEOUT_S, retries=FUNDING_HTTP_RETRIES,
    )
    if err:
        return None, f"venue:funding_history:{coin}:{err}"
    if not isinstance(data, list):
        return None, f"venue:funding_history:{coin}:bad_shape"
    return data, None


def _funding_pctl_note(funding_now: float, history: List[Dict[str, Any]]) -> Tuple[str, bool]:
    rates = [_safe_float(r.get("fundingRate")) for r in history]
    if len(rates) < MIN_FUNDING_SAMPLES_FOR_PCTL:
        return f"insufficient 30d funding history ({len(rates)} samples)", False
    pctl = sum(1 for r in rates if r <= funding_now) / len(rates)
    crowded = pctl >= FUNDING_PCTL_CROWDED
    note = f"pct30d={pctl:.2f}" + (" CROWDED (>=0.90)" if crowded else "")
    return note, crowded


def _funding_24h_ago(now: datetime, history: List[Dict[str, Any]]) -> Optional[float]:
    target_ms = (now - timedelta(hours=24)).timestamp() * 1000
    best, best_dist = None, None
    for r in history:
        t = _safe_float(r.get("time"))
        dist = abs(t - target_ms)
        if dist <= 3 * 3600 * 1000 and (best_dist is None or dist < best_dist):
            best, best_dist = _safe_float(r.get("fundingRate")), dist
    return best


def _oi_24h_ago_usd(history: List[Dict[str, Any]], now_s: float) -> Optional[float]:
    lo, hi = OI_24H_WINDOW_S
    best, best_dist = None, None
    for sample in history:
        ts = _safe_float(sample.get("ts"))
        age = now_s - ts
        if lo <= age <= hi:
            dist = abs(age - 24 * 3600.0)
            if best_dist is None or dist < best_dist:
                best, best_dist = _safe_float(sample.get("oi_usd")), dist
    return best


def _instrument_datapoints(inst: Instrument, ctx: Dict[str, Any], funding_history: Optional[List[Dict]],
                            desk_dir: str, now: datetime) -> List[DataPoint]:
    out: List[DataPoint] = []
    mark_px = _safe_float(ctx.get("markPx"))
    funding_now = _safe_float(ctx.get("funding"))
    oi_base = _safe_float(ctx.get("openInterest"))
    oi_usd = oi_base * mark_px
    volume_usd = _safe_float(ctx.get("dayNtlVlm"))
    now_s = now.timestamp()
    safe_coin = inst.coin.replace(":", "_")

    # -- funding ----------------------------------------------------------
    if funding_history is not None:
        change = None
        prior = None
        note, crowded = _funding_pctl_note(funding_now, funding_history)
        prior = _funding_24h_ago(now, funding_history)
        if prior is not None:
            change = funding_now - prior
        out.append(DataPoint(
            id=stable_id("venue", "funding", inst.coin, now.isoformat()),
            source="venue_hl", label=f"{inst.label} funding rate (hourly)",
            value=funding_now, prior=prior, change=change, unit="rate_per_hr",
            as_of=now, note=note, relevance=0.8 if crowded else 0.55,
        ))

    # -- open interest (own rolling history; see module docstring) --------
    history = cache.append_history(
        desk_dir, f"venue_oi_hist_{safe_coin}", {"ts": now_s, "oi_usd": oi_usd},
        keep_s=OI_HISTORY_KEEP_S, now=now_s,
    )
    oi_prior = _oi_24h_ago_usd(history[:-1], now_s)  # exclude the sample just appended
    oi_change = None
    oi_note = "insufficient OI history for a 24h change (<20h of samples so far)"
    crowded_oi = False
    if oi_prior is not None and oi_prior > 0:
        oi_change = oi_usd - oi_prior
        pct = oi_change / oi_prior
        crowded_oi = pct >= OI_JUMP_CROWDED_PCT
        oi_note = f"chg_24h={pct:+.1%}" + (" CROWDED (>=20%)" if crowded_oi else "")
    out.append(DataPoint(
        id=stable_id("venue", "oi", inst.coin, now.isoformat()),
        source="venue_hl", label=f"{inst.label} open interest",
        value=oi_usd, prior=oi_prior, change=oi_change, unit="usd",
        as_of=now, note=oi_note, relevance=0.8 if crowded_oi else 0.5,
    ))

    # -- 24h volume ---------------------------------------------------------
    out.append(DataPoint(
        id=stable_id("venue", "volume", inst.coin, now.isoformat()),
        source="venue_hl", label=f"{inst.label} 24h volume",
        value=volume_usd, prior=None, change=None, unit="usd",
        as_of=now, note=None, relevance=0.4,
    ))
    return out


def fetch_venue_positioning(spec: DeskSpec, now: datetime,
                             desk_dir: str) -> Tuple[List[DataPoint], List[str]]:
    """Not an ATOMIC_FETCHERS-standard `(spec) -> (items, err)` signature —
    this is the one source in services/intel that needs its own resolved
    `desk_dir` (for the OI rolling history), so services/intel/api.py wires
    it in explicitly rather than through the shared `_submit` convention
    every other source uses. `desk_dir` must already be resolved in the
    caller's main thread; see cache.py's module docstring for why."""
    errors: List[str] = []
    datapoints: List[DataPoint] = []

    by_dex = _instruments_by_dex(spec.instruments)
    ctx_by_coin: Dict[str, Dict[str, Any]] = {}
    for dex, insts in by_dex.items():
        ctxs, err = fetch_meta_and_ctxs(dex)
        if err:
            errors.append(err)
            continue
        for inst in insts:
            if inst.coin in ctxs:
                ctx_by_coin[inst.coin] = ctxs[inst.coin]
            else:
                errors.append(f"venue:meta_and_ctxs:{inst.coin}:not_in_universe")

    for inst in spec.instruments:
        ctx = ctx_by_coin.get(inst.coin)
        if ctx is None:
            continue
        funding_history, fh_err = fetch_funding_history(inst.coin, now)
        if fh_err:
            errors.append(fh_err)
        datapoints.extend(_instrument_datapoints(inst, ctx, funding_history, desk_dir, now))

    return datapoints, errors


# No ATOMIC_FETCHERS tuple here on purpose: fetch_venue_positioning's
# desk_dir-carrying signature is wired into services/intel/api.py directly
# (see that module's "venue" category), not fanned out through the generic
# `_submit(..., fn, (spec,))` convention every args-uniform source uses.
