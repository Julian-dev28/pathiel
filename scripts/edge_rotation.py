#!/usr/bin/env python3
"""Capital-rotation A/B on ~17 days of REAL mover data (candle-derived — sidesteps the
16h/200-cap verdict memory). The live verdict-harness can't see this: movers that get
saturation-blocked are filtered at the margin preflight BEFORE research, so they never
log a verdict. So we reconstruct the mover stream from candles directly.

Walks a global 5m clock with a shared equity pool + live portfolio gates (max_concurrent,
gross cap, margin floor) and the live exit (2.5% stop + 0.10 trail). Two arms:
  HOLD   — when the book is full / capital-capped, SKIP the fresh mover (current behavior)
  ROTATE — ask the REAL decide_rotation() whether to evict the weakest non-winner
           (roe < protect_winner_roe, age >= min_hold) for the stronger fresh mover

Lookahead-safe (strength[i] uses bars <= i; entry close[i]; forward path strictly after).
OOS = first-half vs second-half of the clock. Reuses decide_rotation (the live code).
"""
import os
import sys
import time
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
from hermes_trader.indicators.math import candle_val
from hermes_trader.agents.rotation import decide_rotation

TF, BARS, LB24 = "5m", 5000, 288          # 288 5m-bars = 24h trailing window
STRENGTH_ENTRY = 12.0                      # fresh candidate: 24h% crosses up through 12%
ROT_MIN_STRENGTH = 18.0                    # composite analog: only rotate for a >=18% mover
EQUITY0, LEV, GROSS_CAP, MIN_MARGIN = 170.0, 12, 10.0, 0.10
FRAC, MAX_NOTIONAL = 0.20, 350.0           # live sizing: equity_fraction * lev, capped
STOP, PROTECT, RETRACE = 2.5, 1.25, 0.10   # live exit
PROTECT_WINNER_ROE, MIN_HOLD_MIN = 3.0, 30.0
COST = 0.0012
TOPN, VOL_FLOOR = 40, 5e6
REENTRY_COOLDOWN_BARS = 12                  # don't re-fire same coin within 60min
PENDING_BARS = 12                           # chase a fresh mover for up to 60min after it starts
FETCH_SLEEP_S = 0.25                        # pace fetches: be a gentle IP citizen vs the live loop


class Pos:
    __slots__ = ("coin", "entry_px", "notional", "margin", "entry_bar", "peak", "armed")
    def __init__(self, coin, entry_px, notional, margin, entry_bar):
        self.coin, self.entry_px, self.notional, self.margin = coin, entry_px, notional, margin
        self.entry_bar, self.peak, self.armed = entry_bar, entry_px, False

    def step(self, hi, lo):
        """Long-only exit check at one bar. Returns exit_px or None."""
        if lo <= self.entry_px * (1 - STOP / 100):
            return self.entry_px * (1 - STOP / 100)
        if hi > self.peak:
            self.peak = hi
        if (self.peak - self.entry_px) / self.entry_px * 100 >= PROTECT:
            self.armed = True
        if self.armed:
            floor = self.peak - (self.peak - self.entry_px) * RETRACE
            if lo <= floor:
                return floor
        return None

    def roe_pct(self, px):
        return (px - self.entry_px) / self.entry_px * LEV * 100.0

    def pnl(self, px):
        return self.notional * ((px - self.entry_px) / self.entry_px - COST)


def _fetch_one(c):
    bars = fetch_hl_candles(c, TF, BARS)
    if len(bars) < LB24 + 500:
        return None
    closes = [candle_val(b, "c") for b in bars]
    his = [candle_val(b, "h") for b in bars]
    los = [candle_val(b, "l") for b in bars]
    strg = [0.0] * len(closes)
    for i in range(LB24, len(closes)):
        base = closes[i - LB24]
        strg[i] = (closes[i] / base - 1) * 100 if base > 0 else 0.0
    return (closes, his, los, strg)


def load():
    uni = [m for m in get_universe(include_hip3=False)
           if float(m.get("dayNtlVlm") or 0) >= VOL_FLOOR]
    uni = sorted(uni, key=lambda m: float(m.get("dayNtlVlm") or 0), reverse=True)[:TOPN]
    names = [m.get("name") or m.get("coin") for m in uni]
    series, failed = {}, []
    for c in names:
        try:
            r = _fetch_one(c)
            series[c] = r if r else None
            if not r:
                failed.append(c)
        except Exception:
            failed.append(c)
        series.pop(c, None) if series.get(c) is None else None
        time.sleep(FETCH_SLEEP_S)
    # retry the ones the live loop's 429s knocked out — integrity over speed
    for c in failed:
        time.sleep(FETCH_SLEEP_S * 3)
        try:
            r = _fetch_one(c)
            if r:
                series[c] = r
        except Exception:
            pass
    bad = [c for c in series if series[c] is None]
    for c in bad:
        del series[c]
    if failed:
        print(f"# data: {len(series)} clean / {len(failed)} needed retry "
              f"({len([c for c in failed if c in series])} recovered)")
    return series


def run(series, max_concurrent, rotate, rot_min=ROT_MIN_STRENGTH):
    coins = list(series)
    block_reason = {"cand_weak": 0, "no_evictee": 0, "would_rotate": 0}
    N = min(len(v[0]) for v in series.values())
    equity = EQUITY0
    book = {}                       # coin -> Pos
    cooldown = {}                   # coin -> bar until which blocked
    first_cross = {}                # coin -> bar it most recently crossed UP through ENTRY
    closes_log = []                 # (bar, coin, pnl, reason)
    rotations = 0
    n_block = 0                     # times a fresh candidate was capital-blocked (rotation's domain)
    peak_eq, max_dd = equity, 0.0
    for t in range(LB24 + 1, N):
        # 1) exits
        for coin in list(book):
            cl, hi, lo, _ = series[coin]
            ex = book[coin].step(hi[t], lo[t])
            if ex is not None:
                pnl = book[coin].pnl(ex)
                equity += pnl
                closes_log.append((t, coin, pnl, "exit"))
                cooldown[coin] = t + REENTRY_COOLDOWN_BARS
                del book[coin]
        # 2) candidate stream: a coin is "fresh" while still strong AND within PENDING_BARS of
        #    its last upward cross through ENTRY (models chasing a mover for ~1h after it starts)
        fresh = []
        for coin in coins:
            strg = series[coin][3]
            if strg[t] >= STRENGTH_ENTRY and strg[t - 1] < STRENGTH_ENTRY:
                first_cross[coin] = t
            if (strg[t] >= STRENGTH_ENTRY and coin in first_cross
                    and t - first_cross[coin] <= PENDING_BARS):
                fresh.append((strg[t], coin))
        fresh.sort(reverse=True)
        for strength, coin in fresh:
            if coin in book or t < cooldown.get(coin, 0):
                continue
            cl, hi, lo, _ = series[coin]
            entry_px = cl[t]
            if entry_px <= 0:
                continue
            notional = min(equity * FRAC * LEV, MAX_NOTIONAL)
            gross = sum(p.notional for p in book.values())
            used_margin = sum(p.margin for p in book.values())
            new_margin = notional / LEV
            capital_block = (
                len(book) >= max_concurrent
                or gross + notional > equity * GROSS_CAP
                or (equity - used_margin - new_margin) / equity < MIN_MARGIN
            )
            if capital_block:
                n_block += 1
                if not rotate:
                    continue
                # ask the REAL decide_rotation
                descs = [{"coin": c2, "roe_pct": p.roe_pct(series[c2][0][t]),
                          "age_minutes": (t - p.entry_bar) * 5.0} for c2, p in book.items()]
                block_kind = ("max positions reached" if len(book) >= max_concurrent
                              else "total notional would exceed")
                dec = decide_rotation(
                    candidate_coin=coin, candidate_composite=strength,
                    blocked_reasons=[block_kind], open_positions=descs,
                    min_candidate_composite=rot_min,
                    min_hold_minutes=MIN_HOLD_MIN, protect_winner_roe_pct=PROTECT_WINNER_ROE)
                if not dec.should_rotate:
                    block_reason["cand_weak" if "not worth a rotation" in dec.reason
                                 else "no_evictee"] += 1
                    continue
                block_reason["would_rotate"] += 1
                ev = book[dec.evict_coin]
                px = series[dec.evict_coin][0][t]
                equity += ev.pnl(px)
                closes_log.append((t, dec.evict_coin, ev.pnl(px), "rotated_out"))
                cooldown[dec.evict_coin] = t + REENTRY_COOLDOWN_BARS
                del book[dec.evict_coin]
                rotations += 1
                # recheck margin/gross after freeing
                gross = sum(p.notional for p in book.values())
                used_margin = sum(p.margin for p in book.values())
                if gross + notional > equity * GROSS_CAP or \
                   (equity - used_margin - new_margin) / equity < MIN_MARGIN:
                    continue
            book[coin] = Pos(coin, entry_px, notional, new_margin, t)
        peak_eq = max(peak_eq, equity)
        max_dd = max(max_dd, peak_eq - equity)
    # close any stragglers at last bar
    for coin, p in book.items():
        px = series[coin][0][N - 1]
        equity += p.pnl(px)
        closes_log.append((N - 1, coin, p.pnl(px), "end"))
    return equity, closes_log, rotations, max_dd, N, n_block, block_reason


def summ(label, mc, eq, log, rot, dd, N):
    n = len(log)
    wins = [x for x in log if x[2] > 0]
    net = eq - EQUITY0
    half = N // 2
    n1 = sum(x[2] for x in log if x[0] < half)
    n2 = sum(x[2] for x in log if x[0] >= half)
    print(f"{label:>7} {mc:>4} {n:>6} {len(wins)/n*100 if n else 0:>4.0f}% "
          f"{net:>+8.2f} {eq:>7.0f} {dd:>6.1f} {rot:>4}  OOS {n1:>+6.2f}/{n2:>+6.2f} "
          f"{'Y' if n1>0 and n2>0 else '-'}")
    return net


def main():
    print(f"# loading top {TOPN} movers, {TF} ~{BARS} bars (~17d)...")
    series = load()
    Ndays = (min(len(v[0]) for v in series.values()) - LB24) * 5 / 60 / 24
    print(f"# {len(series)} coins | ~{Ndays:.0f}d clock | entry@24h%>={STRENGTH_ENTRY} "
          f"rotate@>={ROT_MIN_STRENGTH} | exit {STOP}%stop/{RETRACE}trail | lev{LEV} "
          f"gross{GROSS_CAP:.0f}x margin{MIN_MARGIN:.0%} cost{COST*1e4:.0f}bps")
    print(f"# protect_winner_roe>={PROTECT_WINNER_ROE}% min_hold>={MIN_HOLD_MIN:.0f}m (live rotation cfg)\n")
    print(f"{'arm':>7} {'conc':>4} {'trades':>6} {'win':>4} {'net$':>8} {'endEq':>7} "
          f"{'maxDD':>6} {'rot':>4}  OOS h1/h2 rob")
    # conc=3 is where this 27-coin universe actually saturates; sweep the rotation
    # strength bar (live default 40-composite analog = 18%; also test 12 = entry bar).
    for mc in (3, 4):
        eqH, logH, _, ddH, N, nblk, _ = run(series, mc, rotate=False)
        baseH = summ("HOLD", mc, eqH, logH, 0, ddH, N)
        for rmin in (18.0, 12.0):
            eqR, logR, rot, ddR, _, _, br = run(series, mc, rotate=True, rot_min=rmin)
            baseR = summ(f"ROT@{rmin:.0f}", mc, eqR, logR, rot, ddR, N)
            print(f"{'':7} {'':4}  blocked-bars {nblk:>3} | of those: cand_weak "
                  f"{br['cand_weak']:>3}, no_evictee {br['no_evictee']:>3}, "
                  f"would-rotate {br['would_rotate']:>3} | Δnet {baseR-baseH:>+7.2f}")
        print()


if __name__ == "__main__":
    main()
