#!/usr/bin/env python3
"""W-E shared lib — Lane E (HIP-3 tokenized-equity cross-asset frontier).

US-RTH session calendar in UTC + loaders for W-E_dataset.json (built by
W-E0_fetch.py) + episode helpers. Imported by W-E1..W-E4.

Session math (all UTC):
  EST (until 2026-03-07):  RTH 14:30 - 21:00
  EDT (from  2026-03-08):  RTH 13:30 - 20:00
  Holidays (full close) inside the data window (2025-12-13 .. 2026-07-09):
    2025-12-25, 2026-01-01, 2026-01-19 (MLK), 2026-02-16 (Presidents),
    2026-04-03 (Good Friday), 2026-05-25 (Memorial), 2026-06-19 (Juneteenth),
    2026-07-03 (Jul-4 observed).
  Half day excluded from episodes: 2025-12-24.

Bar convention: HL candle t = bar OPEN time (ms). A 1h bar t covers [t, t+1h);
its close prints at t+1h. Lookahead-safe fill = open of the NEXT bar after the
decision bar's close.
"""
from __future__ import annotations
import json, statistics, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "lib"))   # alpha_lib / mc_null
DATASET = _HERE / "W-E_dataset.json"

HOUR = 3_600_000
T, O, H, L, C, V = 0, 1, 2, 3, 4, 5

HOLIDAYS = {date(2025, 12, 25), date(2026, 1, 1), date(2026, 1, 19),
            date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
            date(2026, 6, 19), date(2026, 7, 3)}
HALF_DAYS = {date(2025, 12, 24)}   # excluded from episodes entirely
DST_START = date(2026, 3, 8)       # US clocks spring forward (EDT)

INDEX_NAMES = {"xyz:SP500", "xyz:XYZ100", "xyz:SMH", "xyz:EWY", "xyz:EWJ"}


def load() -> dict:
    return json.loads(DATASET.read_text())


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS and d not in HALF_DAYS


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def rth_utc(d: date) -> tuple[int, int]:
    """(open_ms, close_ms) of the US RTH session on trading day d."""
    edt = d >= DST_START
    open_h, open_m = (13, 30) if edt else (14, 30)
    close_h = 20 if edt else 21
    base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    o = int((base + timedelta(hours=open_h, minutes=open_m)).timestamp() * 1000)
    c = int((base + timedelta(hours=close_h)).timestamp() * 1000)
    return o, c


def by_t(bars: list) -> dict[int, list]:
    return {int(b[T]): b for b in bars}


def close_at(idx: dict[int, list], t_close_ms: int, iv_ms: int = HOUR):
    """Close price printed exactly at t_close_ms (bar opening one interval before)."""
    b = idx.get(t_close_ms - iv_ms)
    return float(b[C]) if b else None


def open_at(idx: dict[int, list], t_open_ms: int):
    b = idx.get(t_open_ms)
    return float(b[O]) if b else None


def last_rth_close(idx: dict[int, list], d: date):
    """Close of the last full 1h bar inside RTH on day d (== the 20:00/21:00 print)."""
    _, c = rth_utc(d)
    return close_at(idx, c)


def preopen_close(idx: dict[int, list], d: date):
    """Close of the last full 1h bar that ends at or before RTH open on day d.
    EDT open 13:30 -> bar t=12:00 closes 13:00 (decision info cutoff 13:00).
    EST open 14:30 -> bar t=13:00 closes 14:00."""
    o, _ = rth_utc(d)
    t_close = (o // HOUR) * HOUR          # 13:00 (EDT) / 14:00 (EST)
    return close_at(idx, t_close), t_close


def hold_return(idx: dict[int, list], fill_t: int, exit_close_t: int, side: str,
                stop_pct: float | None = None) -> float | None:
    """Enter at open of bar fill_t, exit at the close printing at exit_close_t.
    Walk bars for the optional stop. Lookahead-safe (only bars >= fill_t used)."""
    entry = open_at(idx, fill_t)
    if entry is None:
        return None
    sign = 1.0 if side == "long" else -1.0
    t = fill_t
    last_close = None
    while t <= exit_close_t - HOUR:
        b = idx.get(t)
        if b is not None:
            if stop_pct is not None:
                if side == "long" and float(b[L]) <= entry * (1 - stop_pct):
                    return -stop_pct
                if side == "short" and float(b[H]) >= entry * (1 + stop_pct):
                    return -stop_pct
            last_close = float(b[C])
        t += HOUR
    if last_close is None:
        return None
    return sign * (last_close - entry) / entry


def basket_by_key(trades: list[dict], key: str = "ep") -> list[dict]:
    """Collapse per-name trades into one equal-weight basket trade per episode key
    (dedup: one weekend / one day = ONE independent episode)."""
    from collections import defaultdict
    g = defaultdict(list)
    for tr in trades:
        g[tr[key]].append(tr)
    out = []
    for k in sorted(g):
        rows = g[k]
        out.append({"t": min(r["t"] for r in rows), "ep": k,
                    "ret": statistics.mean(r["ret"] for r in rows),
                    "n_names": len(rows)})
    return out


def fmt_summary(s: dict) -> str:
    if s.get("n", 0) == 0:
        return "  n=0"
    lines = [f"  n={s['n']}"]
    for bps in (0, 6, 12, 25, 50):
        k = f"slip{bps}"
        if k in s:
            r = s[k]
            lines.append(f"  {bps:>2}bps: mean {r['mean_ret_pct']:+.3f}%  "
                         f"win {r['win_rate']:.2f}  sharpe-like {r['sharpe_like']:+.2f}")
    oos = s.get("oos_12bps", {})
    lines.append(f"  OOS@12bps: H1 {oos.get('first_half_mean_pct')}% (n={oos.get('n_first')})"
                 f" | H2 {oos.get('second_half_mean_pct')}% (n={oos.get('n_second')})"
                 f" -> {s.get('verdict')}")
    return "\n".join(lines)
