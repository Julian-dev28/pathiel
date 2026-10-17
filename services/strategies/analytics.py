"""Deterministic desk analytics. Every number here is a pure function of
candles + venue metadata — no LLM, no randomness, same inputs always give the
same outputs. That is the whole point: the Thesis (services/thesis) may only
quote numbers that live in here (contracts/strategies/README.md's rule), so
this module has to be trustworthy enough to quote verbatim.

Liquidation price matches scripts/pathiel-mcp-server.py:handle_get_liquidation_price
exactly — same formula, same inputs give the same liq price to the last cent:

    mmr = 1 / (2 * max_leverage)
    liq = entry - side * (entry/leverage - mmr*entry) / (1 - mmr*side)
    side: +1 long, -1 short

The leverage ladder ports the math from the reference scratch scripts
(oil_ladder.py / full_send.py — a path-dependent simulation over every
historical horizon-day window, using daily lows/highs as the intraday
liquidation/stop proxy) with one deliberate fix: the scratch reference
subtracted funding from BOTH longs and shorts the same way. That is wrong —
InstrumentSnapshot.funding_hourly's own doc says "+ = longs pay", so a short
should RECEIVE funding when it is positive. `_cost_frac` below applies the
sign by side; see test_funding_sign_long_vs_short for the regression test
that would have caught the original bug.

Candle and venue-metadata access are both injectable (`CandleFetcher`,
`MetaFetcher`), so every test here runs on recorded candles — no network,
deterministic, fast. `build_analytics()` is the one function that reaches
for the real network by default.
"""
from __future__ import annotations

import logging
import math
import statistics as st
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from contracts.strategies.models import (
    AccountSnapshot, ConditionalStats, DeskAnalytics, DeskSpec, ForwardStats,
    Instrument, InstrumentSnapshot, LadderRung, PairSnapshot, Quantiles, Side,
)
from pathiel.models.types import Candle

logger = logging.getLogger(__name__)

# ── tunables, named so a test can assert against them instead of a magic number ──

TAKER_FEE = 0.00045                  # HL taker fee, per side, per the MCP liquidation note
STOP_FRACTION_OF_LIQ = 0.60          # suggested stop = 60% of the distance to liquidation
DAILY_CANDLE_COUNT = 400             # history depth requested; HL returns whatever it actually has
MIN_WINDOWS_FOR_FORWARD_STATS = 10   # below this, forward stats / ladder are too thin to trust
CONDITIONAL_LOOKBACK_DAYS = 5
CONDITIONAL_MOVE_THRESHOLD = 0.05
CONDITIONAL_LABEL = f"after +{int(CONDITIONAL_MOVE_THRESHOLD * 100)}% {CONDITIONAL_LOOKBACK_DAYS}d run"
DEFAULT_ASSUMED_EQUITY_USD = 1000.0  # used only when no account credentials are configured

CandleFetcher = Callable[[str, str, int], Sequence[Candle]]
MetaFetcher = Callable[[], Dict[str, Dict[str, Any]]]
AccountFetcher = Callable[[], AccountSnapshot]


# ── pure numeric helpers ──────────────────────────────────────────────────────

def quantile(xs: Sequence[float], p: float) -> float:
    """Nearest-rank quantile, floor-indexed: matches the `q(xs,p)` helper in
    the reference scratch scripts (oil_ladder.py/full_send.py) exactly —
    NOT numpy's linearly-interpolated percentile. `sorted(xs)[min(n-1,
    int(p*n))]`. p=0 -> min, p->1 -> max (clamped to the last element),
    single-element input returns that element for any p.
    """
    if not xs:
        raise ValueError("quantile() of an empty sequence")
    s = sorted(xs)
    idx = min(len(s) - 1, int(p * len(s)))
    return s[max(0, idx)]


def quantiles(xs: Sequence[float]) -> Quantiles:
    return Quantiles(p10=quantile(xs, .10), p25=quantile(xs, .25), p50=quantile(xs, .50),
                     p75=quantile(xs, .75), p90=quantile(xs, .90))


def log_returns(closes: Sequence[float]) -> List[float]:
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]


def daily_vol(closes: Sequence[float], n: int) -> Optional[float]:
    """Population stdev of the last n daily log returns. None if there isn't
    enough history for a meaningful read (needs n+1 closes)."""
    if len(closes) < n + 1:
        return None
    rets = log_returns(closes)[-n:]
    return st.pstdev(rets) if len(rets) > 1 else 0.0


def atr14_pct(candles: Sequence[Candle]) -> Optional[float]:
    """14-period ATR (true range mean) as a fraction of the latest close."""
    if len(candles) < 15:
        return None
    tr = [max(c.h - c.l, abs(c.h - p.c), abs(c.l - p.c))
          for p, c in zip(candles[:-1], candles[1:])]
    atr14 = sum(tr[-14:]) / 14
    px = candles[-1].c
    return atr14 / px if px else None


def _ret(closes: Sequence[float], n: int) -> Optional[float]:
    """close[-1]/close[-1-n] - 1, or None if there aren't n+1 closes."""
    if len(closes) < n + 1:
        return None
    return closes[-1] / closes[-1 - n] - 1.0


# ── liquidation + verdict ─────────────────────────────────────────────────────

def liquidation_price(entry_px: float, leverage: float, max_leverage: float, side: Side) -> float:
    """HL isolated-margin liquidation price. Ports
    scripts/pathiel-mcp-server.py:handle_get_liquidation_price verbatim:
        mmr = 1 / (2 * max_leverage); side_sign = +1 long, -1 short
        liq = entry - side_sign * (entry/leverage - mmr*entry) / (1 - mmr*side_sign)
    """
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if max_leverage <= 0:
        raise ValueError("max_leverage must be positive")
    s = 1.0 if side == "long" else -1.0
    mmr = 1.0 / (2.0 * max_leverage)
    return entry_px - s * (entry_px / leverage - mmr * entry_px) / (1.0 - mmr * s)


_VERDICT_ORDER = ("hold", "dip_only", "intraday_only", "avoid")

# Parametric second opinion. The empirical p_liq only knows the trailing
# sample; after a calm stretch it calls a 10x rung "hold" right before a
# volatility expansion liquidates it (verdict_backtest, 2026-10-17: 14 of 72
# desk/side/leverage rows put >15% of their "hold" days into liquidation).
# p_touch asks a different question from the same data: how likely is price
# to TOUCH the liquidation level within the horizon at today's volatility,
# using the larger of 20-day and full-sample daily vol, inflated for fat
# tails. Driftless Brownian touch probability: 2 * (1 - Phi(d / (sigma*sqrt(h)))).
TAIL_VOL_MULT = 1.25
# Fewer forward windows than this and the empirical p_liq is too thin to
# trust: the verdict drops one level.
MIN_RELIABLE_WINDOWS = 120


def _bucket(p: float) -> str:
    if p < 0.10:
        return "hold"
    if p < 0.35:
        return "dip_only"
    if p < 0.55:
        return "intraday_only"
    return "avoid"


def touch_probability(d_liq: float, sigma_daily: float, horizon_days: int) -> float:
    """P(price touches a level d_liq away, as a fraction, within horizon_days),
    driftless Brownian approximation. A level at or behind the price is
    already touched (1.0); zero volatility or zero horizon never moves (0.0).
    Unknown volatility is the caller's case: pass p_touch=None instead."""
    if d_liq <= 0:
        return 1.0
    if sigma_daily <= 0 or horizon_days <= 0:
        return 0.0
    z = d_liq / (sigma_daily * math.sqrt(horizon_days))
    phi = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return max(0.0, min(1.0, 2.0 * (1.0 - phi)))


def verdict_for_rung(p_liq: float, p_touch: Optional[float] = None,
                     n_windows: Optional[int] = None) -> str:
    """hold / dip_only / intraday_only / avoid.

    Each probability maps to a bucket with the same thresholds, half-open on
    the low end so a boundary reads as the SAFER bucket:

        p < 0.10            hold            survives the horizon >90% of the time
        0.10 <= p < 0.35    dip_only        size down, add on dips, don't carry casually
        0.35 <= p < 0.55    intraday_only   only as a same-day, actively managed trade
        p >= 0.55           avoid           coin-flip or worse on liquidation

    The verdict is the WORSE of the empirical bucket (p_liq, the ladder
    simulation) and the parametric bucket (p_touch, today's volatility), then
    one level worse again when n_windows < MIN_RELIABLE_WINDOWS. Called with
    p_liq alone it is the original single-signal rule.
    """
    levels = [_VERDICT_ORDER.index(_bucket(p_liq))]
    if p_touch is not None:
        levels.append(_VERDICT_ORDER.index(_bucket(p_touch)))
    level = max(levels)
    if n_windows is not None and n_windows < MIN_RELIABLE_WINDOWS:
        level = min(level + 1, len(_VERDICT_ORDER) - 1)
    return _VERDICT_ORDER[level]


# ── instrument snapshot ───────────────────────────────────────────────────────

def build_instrument_snapshot(instrument: Instrument, daily: Sequence[Candle],
                              meta_row: Optional[Dict[str, Any]]) -> Optional[InstrumentSnapshot]:
    """None if there isn't even one usable candle — the caller should treat
    a missing snapshot as "drop this instrument, note why", never fabricate
    zeros for it.

    With SOME history but not enough for a given window, the contract's
    required (non-Optional) fields — ret_1d/5d/20d, high_20d/low_20d — fall
    back to 0.0 / the full-history extreme rather than raising, since the
    model has no None to give them; the genuinely Optional fields (ret_60d,
    high_60d/low_60d, vol_daily_60d) report None instead of guessing. In
    practice every registered desk trades markets with hundreds of days of
    history, so this path is a test concern (see
    test_instrument_snapshot_minimal_history), not a production one.
    """
    if not daily:
        return None
    closes = [c.c for c in daily]
    highs = [c.h for c in daily]
    lows = [c.l for c in daily]
    mid = closes[-1]

    def hi(n: int) -> Optional[float]:
        return max(highs[-n:]) if len(highs) >= n else None

    def lo(n: int) -> Optional[float]:
        return min(lows[-n:]) if len(lows) >= n else None

    meta_row = meta_row or {}
    return InstrumentSnapshot(
        coin=instrument.coin, label=instrument.label, mid=mid,
        ret_1d=_ret(closes, 1) or 0.0, ret_5d=_ret(closes, 5) or 0.0,
        ret_20d=_ret(closes, 20) or 0.0, ret_60d=_ret(closes, 60),
        high_20d=hi(20) or max(highs), low_20d=lo(20) or min(lows),
        high_60d=hi(60), low_60d=lo(60),
        atr14_pct=atr14_pct(daily) or 0.0,
        vol_daily_10d=daily_vol(closes, 10) or 0.0,
        vol_daily_20d=daily_vol(closes, 20) or 0.0,
        vol_daily_60d=daily_vol(closes, 60),
        funding_hourly=_fnum(meta_row.get("funding")),
        open_interest_usd=_fnum(meta_row.get("openInterest")),
        volume_24h_usd=_fnum(meta_row.get("dayNtlVlm")),
        max_leverage=_fnum(meta_row.get("maxLeverage")),
    )


def _fnum(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── pair snapshot ─────────────────────────────────────────────────────────────

def build_pair_snapshot(label: str, long_coin: str, short_coin: str,
                        long_daily: Sequence[Candle], short_daily: Sequence[Candle]
                        ) -> Optional[PairSnapshot]:
    """Spread = long mid - short mid, matching contracts/strategies/models.py's
    PairSnapshot doc. Aligned by candle timestamp (`t`), so the two legs'
    history lengths don't need to match exactly."""
    by_t = {c.t: c.c for c in short_daily}
    paired = [(c.c, by_t[c.t]) for c in long_daily if c.t in by_t]
    if len(paired) < 2:
        return None
    spread = [lg - sh for lg, sh in paired]

    def mean(n: int) -> Optional[float]:
        return st.mean(spread[-n:]) if len(spread) >= n else None

    n20 = mean(20)
    if n20 is None:
        return None

    rel = None
    if len(spread) >= 8:
        d7 = [spread[i + 7] - spread[i] for i in range(len(spread) - 7)]
        if d7:
            rel = quantiles(d7)

    # pair_vol_daily is "sd of daily log(long/short) change" per this field's
    # own doc comment in contracts/strategies/models.py — a dimensionless
    # relative-return vol, NOT the stdev of the raw dollar spread's day-over-
    # day change (that conflates the pair's relative risk with however
    # expensive the legs happen to be, e.g. gold-vs-silver's $4000-vs-$57
    # mismatch would swamp it). Matches oil_spread_risk.py's `rel` series.
    log_ratio = [math.log(lg / sh) for lg, sh in paired]
    ratio_changes = [log_ratio[i] - log_ratio[i - 1] for i in range(1, len(log_ratio))]
    pair_vol = st.pstdev(ratio_changes) if len(ratio_changes) > 1 else 0.0

    return PairSnapshot(
        label=label, long=long_coin, short=short_coin, spread_now=spread[-1],
        spread_mean_20d=n20, spread_mean_60d=mean(60),
        spread_min_60d=min(spread[-60:]) if len(spread) >= 60 else None,
        spread_max_60d=max(spread[-60:]) if len(spread) >= 60 else None,
        pair_vol_daily=pair_vol, spread_change_7d=rel,
    )


# ── forward stats ─────────────────────────────────────────────────────────────

def _forward_windows(daily: Sequence[Candle], horizon_days: int) -> List[Dict[str, float]]:
    """One entry per historical entry-day i: final return, long MAE (worst
    drawdown a long would see, <=0), short MAE (worst adverse move for a
    short, >=0). Ports the `wins` comprehension in oil_ladder.py exactly."""
    out = []
    for i in range(len(daily) - horizon_days):
        e = daily[i].c
        window = daily[i + 1:i + 1 + horizon_days]
        lo = min(c.l for c in window)
        hi = max(c.h for c in window)
        fin = window[-1].c
        out.append({"entry": e, "mae_long": lo / e - 1.0, "mae_short": hi / e - 1.0,
                   "fin": fin / e - 1.0})
    return out


def build_forward_stats(coin: str, daily: Sequence[Candle], horizon_days: int
                        ) -> Optional[ForwardStats]:
    wins = _forward_windows(daily, horizon_days)
    if len(wins) < MIN_WINDOWS_FOR_FORWARD_STATS:
        return None
    closes = [c.c for c in daily]
    fins = [w["fin"] for w in wins]

    conditional: List[ConditionalStats] = []
    lb = CONDITIONAL_LOOKBACK_DAYS
    if len(closes) > lb:
        cond_idx = [i for i in range(lb, len(wins))
                   if closes[i] / closes[i - lb] - 1.0 >= CONDITIONAL_MOVE_THRESHOLD]
        if cond_idx:
            cf = [wins[i]["fin"] for i in cond_idx]
            conditional.append(ConditionalStats(
                label=CONDITIONAL_LABEL, n=len(cf), mean=st.mean(cf), p50=quantile(cf, .5),
                hit_rate_up=sum(f > 0 for f in cf) / len(cf),
            ))

    return ForwardStats(
        coin=coin, horizon_days=horizon_days, n_windows=len(wins),
        sample_start=_candle_dt(daily[0]), sample_end=_candle_dt(daily[-1]),
        final_ret=quantiles(fins), final_ret_mean=st.mean(fins),
        long_mae=quantiles([w["mae_long"] for w in wins]),
        short_mae=quantiles([w["mae_short"] for w in wins]),
        conditional=conditional,
    )


def _candle_dt(c: Candle) -> datetime:
    return datetime.fromtimestamp(c.t / 1000.0, tz=timezone.utc)


# ── leverage ladder ────────────────────────────────────────────────────────────

def _cost_frac(side: Side, leverage: float, funding_hourly: Optional[float], horizon_days: int) -> float:
    """Round-trip taker + horizon funding, as a fraction of EQUITY (not
    notional) — scales by leverage since the position is sized at leverage*equity.

    Funding sign is side-aware: InstrumentSnapshot.funding_hourly's own doc
    says "+ = longs pay". A long's funding is therefore a COST (added here);
    a short's is a BENEFIT when funding is positive (subtracted here). The
    reference scratch script (full_send.py) applied the same sign to both
    sides — that's the "sloppiness" the build brief says to fix, not port.
    """
    funding_hourly = funding_hourly or 0.0
    taker_cost = 2.0 * TAKER_FEE * leverage
    side_sign = 1.0 if side == "long" else -1.0
    funding_cost = side_sign * funding_hourly * 24.0 * horizon_days * leverage
    return taker_cost + funding_cost


def simulate_ladder_rung(coin: str, side: Side, leverage: float, max_leverage: float,
                         equity_usd: float, daily: Sequence[Candle], horizon_days: int,
                         funding_hourly: Optional[float]) -> Optional[LadderRung]:
    """Path-dependent outcome of putting 100% of `equity_usd` up as isolated
    margin at `leverage`, over EVERY historical horizon-day window, using
    each day's low (long) / high (short) as the intraday liquidation+stop
    proxy. Ports full_send.py's `run()` exactly (liq checked before stop
    within a day; fin computed from the window's last close when neither
    triggers), with the funding-sign fix described in `_cost_frac`.

    None if there isn't enough history (MIN_WINDOWS_FOR_FORWARD_STATS) to
    trust the result, or if `leverage` exceeds `max_leverage` (can't open
    that position on this venue at all).

    One deliberate break from the reference scratch output (contracts/
    strategies/fixtures/oil_analytics.json's ladder numbers): that fixture's
    p_liq/p_win/ev/equity_ret came from oil_ladder.py's NO-STOP vectorized
    check, while its p_stop came from full_send.py's SEPARATE day-by-day
    run with the stop active — two different simulations spliced into one
    row. Checked by hand against both scripts: the fixture's p_liq for xyz:CL
    5/10/20x (0.032/0.284/0.647) reproduces only the no-stop branch, while its
    p_stop (0.183/0.317/0.248) reproduces only the with-stop branch, and the
    two are not a coherent partition of outcome space (p_liq+p_stop isn't
    <=1 in a way that means anything — they're different probability spaces).
    That is the "sloppiness" the build brief says to fix, not port: this
    function runs ONE simulation per rung, stop active throughout, so every
    field describes the same probability space and p_liq + p_stop + P(rode
    to the horizon close) == 1.0 exactly (see test_ladder_outcomes_partition).
    It also means a rung's numbers are what Julien actually gets if he
    honors the suggested stop_px — the more useful question than "what if I
    ignored my own stop."
    """
    if leverage > max_leverage + 1e-9:
        return None
    windows_n = len(daily) - horizon_days
    if windows_n < MIN_WINDOWS_FOR_FORWARD_STATS:
        return None

    entry_px = daily[-1].c
    liq_px = liquidation_price(entry_px, leverage, max_leverage, side)
    liq_dist_pct = (liq_px - entry_px) / entry_px
    d_liq = abs(liq_dist_pct)
    stop_dist = STOP_FRACTION_OF_LIQ * d_liq
    stop_px = entry_px * (1.0 - stop_dist) if side == "long" else entry_px * (1.0 + stop_dist)

    cost = _cost_frac(side, leverage, funding_hourly, horizon_days)

    outcomes: List[float] = []
    liq_hits = 0
    stop_hits = 0
    for i in range(windows_n):
        e = daily[i].c
        window = daily[i + 1:i + 1 + horizon_days]
        mult: Optional[float] = None
        for c in window:
            adv = (c.l / e - 1.0) if side == "long" else -(c.h / e - 1.0)
            if adv <= -d_liq:
                mult = 0.0
                liq_hits += 1
                break
            if adv <= -stop_dist:
                mult = 1.0 - stop_dist * leverage
                stop_hits += 1
                break
        if mult is None:
            fin = window[-1].c / e - 1.0
            fin = fin if side == "long" else -fin
            mult = 1.0 + fin * leverage
        mult -= cost
        outcomes.append(max(0.0, mult))

    n = len(outcomes)
    rets = [o - 1.0 for o in outcomes]
    return LadderRung(
        coin=coin, side=side, leverage=leverage, equity_usd=equity_usd,
        notional_usd=equity_usd * leverage, entry_px=entry_px, liq_px=liq_px,
        liq_dist_pct=liq_dist_pct, p_liq=liq_hits / n, p_win=sum(o > 1.0 for o in outcomes) / n,
        ev_pct=st.mean(rets), equity_ret=quantiles(rets),
        p_double=sum(o >= 2.0 for o in outcomes) / n, p_lose_half=sum(o <= 0.5 for o in outcomes) / n,
        fees_funding_pct=cost, stop_px=stop_px, p_stop=stop_hits / n,
        verdict=verdict_for_rung(liq_hits / n, p_touch=_p_touch(d_liq, daily, horizon_days),
                                 n_windows=n),
    )


def _p_touch(d_liq: float, daily: Sequence[Candle], horizon_days: int) -> Optional[float]:
    sigma = _sigma_for_verdict(daily)
    return None if sigma is None else touch_probability(d_liq, sigma, horizon_days)


def _sigma_for_verdict(daily: Sequence[Candle]) -> Optional[float]:
    """Larger of 20-day and full-sample daily log-return stdev, times
    TAIL_VOL_MULT. None if there are too few closes to say."""
    closes = [c.c for c in daily if c.c and c.c > 0]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if len(rets) < 2:
        return None
    full = st.pstdev(rets)
    recent = st.pstdev(rets[-20:]) if len(rets) >= 20 else full
    return max(full, recent) * TAIL_VOL_MULT


# ── default I/O (live network; injectable for tests) ─────────────────────────

def default_candle_fetcher(coin: str, interval: str, count: int) -> Sequence[Candle]:
    from pathiel.client.hl_client import fetch_hl_candles
    return fetch_hl_candles(coin, interval, count)


def default_meta_fetcher() -> Dict[str, Dict[str, Any]]:
    from pathiel.client.universe import get_universe
    universe = get_universe(include_hip3=True)
    return {m["coin"]: m for m in universe if m.get("coin")}


def default_account_snapshot() -> AccountSnapshot:
    try:
        from pathiel.client.hl_client import fetch_account_state, resolve_user_address
        user = resolve_user_address()
        if user:
            state = fetch_account_state(user, include_hip3=True)
            equity = float(state.get("equity") or 0.0)
            if equity > 0:
                return AccountSnapshot(equity_usd=equity, source="venue")
    except Exception as exc:  # noqa: BLE001 - account read is best-effort for analytics
        logger.info(f"[strategies.analytics] live account read unavailable: {exc}")
    return AccountSnapshot(equity_usd=DEFAULT_ASSUMED_EQUITY_USD, source="assumed")


# ── entrypoint ─────────────────────────────────────────────────────────────────

def build_analytics(spec: DeskSpec, *, candle_fetcher: Optional[CandleFetcher] = None,
                    meta_fetcher: Optional[MetaFetcher] = None,
                    account_fetcher: Optional[AccountFetcher] = None,
                    now: Optional[datetime] = None) -> DeskAnalytics:
    """The one function that assembles a full DeskAnalytics. Candle fetches
    for every instrument + both legs of every pair run in parallel (same
    ThreadPoolExecutor pattern as hl_client.fetch_account_state's HIP-3
    fan-out) so a multi-instrument desk (ai: 11 instruments) doesn't pay for
    each one serially — this is what keeps `brief oil` under the 10s budget
    and keeps every other desk in the same ballpark.
    """
    candle_fetcher = candle_fetcher or default_candle_fetcher
    meta_fetcher = meta_fetcher or default_meta_fetcher
    account_fetcher = account_fetcher or default_account_snapshot
    now = now or datetime.now(timezone.utc)

    coins = [i.coin for i in spec.instruments]
    meta = meta_fetcher()

    daily_by_coin: Dict[str, Sequence[Candle]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(coins)))) as pool:
        futures = {coin: pool.submit(candle_fetcher, coin, "1d", DAILY_CANDLE_COUNT) for coin in coins}
        for coin, fut in futures.items():
            try:
                daily_by_coin[coin] = fut.result()
            except Exception as exc:  # noqa: BLE001 - one bad coin shouldn't sink the desk
                logger.warning(f"[strategies.analytics] candle fetch failed for {coin}: {exc}")
                daily_by_coin[coin] = []

    notes: List[str] = []
    instruments: List[InstrumentSnapshot] = []
    for instrument in spec.instruments:
        daily = daily_by_coin.get(instrument.coin) or []
        snap = build_instrument_snapshot(instrument, daily, meta.get(instrument.coin))
        if snap is None:
            notes.append(f"no candle data for {instrument.coin}; dropped from this read")
            continue
        instruments.append(snap)

    pairs: List[PairSnapshot] = []
    for pair in spec.pairs:
        ps = build_pair_snapshot(pair.label, pair.long, pair.short,
                                 daily_by_coin.get(pair.long) or [], daily_by_coin.get(pair.short) or [])
        if ps is None:
            notes.append(f"insufficient aligned history for pair '{pair.label}'")
            continue
        pairs.append(ps)

    forward: List[ForwardStats] = []
    ladder: List[LadderRung] = []
    account = account_fetcher()
    primaries = [i for i in spec.instruments if i.role == "primary"]
    for instrument in primaries:
        daily = daily_by_coin.get(instrument.coin) or []
        fs = build_forward_stats(instrument.coin, daily, spec.horizon_days)
        if fs is None:
            notes.append(f"insufficient history for {instrument.coin} forward stats "
                        f"(need >= {MIN_WINDOWS_FOR_FORWARD_STATS + spec.horizon_days} daily bars)")
        else:
            forward.append(fs)

        snap = next((s for s in instruments if s.coin == instrument.coin), None)
        max_lev = snap.max_leverage if snap else None
        funding = snap.funding_hourly if snap else None
        if max_lev is None:
            continue
        for lev in spec.leverage_ladder:
            for side in ("long", "short"):
                rung = simulate_ladder_rung(instrument.coin, side, float(lev), max_lev,
                                           account.equity_usd or DEFAULT_ASSUMED_EQUITY_USD,
                                           daily, spec.horizon_days, funding)
                if rung is not None:
                    ladder.append(rung)

    return DeskAnalytics(
        desk_id=spec.id, as_of=now, instruments=instruments, pairs=pairs,
        forward=forward, ladder=ladder, account=account, notes=notes,
    )
