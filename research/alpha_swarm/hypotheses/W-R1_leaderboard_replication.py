"""W-R1 — honest replication of the "25/25 leaderboard" strategy families.

Pre-registered 2026-07-12 in findings/W-R_leaderboard_replication.md (spec
section written BEFORE this script produced any number). Seven families, one
standard LONG interpretation each, frozen mini-grids, train/validate/test by
time, next-open fills (no intra-bar optimism), 25bps round trip + funding
proxy for holds >=8h, matched same-coin random-time nulls (2000 MC),
Bonferroni alpha 0.05/9 = 0.0056 per family.

Data (all cached, zero network):
  W-R_cache_hourly.json  — 40 coins, 1h x ~5000 bars (2025-12-13..2026-07-09)
  W-U_cache_daily.json   — 90 coins daily OHLC (no volume), up to 2001 bars

Usage: .venv/bin/python research/alpha_swarm/hypotheses/W-R1_leaderboard_replication.py
Writes: W-R1_results.json (same dir).
"""
from __future__ import annotations

import json
import os
import random
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
HOURLY_CACHE = os.path.join(HERE, "W-R_cache_hourly.json")
DAILY_CACHE = os.path.join(HERE, "W-U_cache_daily.json")
BTC_DAILY_CACHE = os.path.join(HERE, "W-R_cache_btc_daily.json")
RESULTS = os.path.join(HERE, "W-R1_results.json")

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
COST_RT = 0.0025            # 25bps round trip
FUND_PER_H = 1.25e-5        # HL baseline 0.01%/8h, longs pay, holds >=8h
N_NULL = 2000
ALPHA = 0.05 / 9            # per-family Bonferroni over the declared 3x3 grid
MIN_TRAIN_N = 30
MIN_OOS_N = 10
MIN_DAILY_BARS = 400
random.seed(20260712)


# ── data loading ─────────────────────────────────────────────────────────────

def load_hourly() -> dict[str, list[list[float]]]:
    return json.load(open(HOURLY_CACHE))["candles"]


def load_daily() -> dict[str, list[list[float]]]:
    raw = json.load(open(DAILY_CACHE))
    # W-U cache has no BTC (unlock-study universe) — BTC daily fetched once,
    # 2s-paced, into its own cache (calendar + dominance numerator).
    raw = {**raw, **json.load(open(BTC_DAILY_CACHE))}
    out = {}
    for coin, bars in raw.items():
        conv = []
        for b in bars:
            t, o, h, l, c = (b.get(k) for k in ("t", "o", "h", "l", "c"))
            if None in (t, o, h, l, c):
                continue
            conv.append([float(t), float(o), float(h), float(l), float(c), 0.0])
        conv.sort(key=lambda x: x[T])
        if conv:
            out[coin] = conv
    return out


def splits(bars: list[list[float]]) -> tuple[float, float, float, float]:
    """(t0, train_end, val_end, t1) on this timeframe's BTC span."""
    t0, t1 = bars[0][T], bars[-1][T]
    return t0, t0 + 0.5 * (t1 - t0), t0 + 0.75 * (t1 - t0), t1


def funding(hours: float) -> float:
    return FUND_PER_H * hours if hours >= 8 else 0.0


# ── indicator helpers (all strictly lookahead-safe) ──────────────────────────

def ema(closes: list[float], n: int) -> list[float]:
    a = 2.0 / (n + 1)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(out[-1] + a * (c - out[-1]))
    return out


def rets_of(bars) -> list[float | None]:
    out: list[float | None] = [None]
    for i in range(1, len(bars)):
        p = bars[i - 1][C]
        out.append(bars[i][C] / p - 1.0 if p > 0 else None)
    return out


def rolling_sigma(rets, window: int) -> list[float | None]:
    """sigma[i] = pstdev of rets[i-window..i-1] (bar i excluded — no lookahead)."""
    out: list[float | None] = [None] * len(rets)
    for i in range(window + 1, len(rets)):
        seg = [r for r in rets[i - window:i] if r is not None]
        if len(seg) >= window // 2:
            out[i] = statistics.pstdev(seg)
    return out


# ── trade construction: next-open fills, contiguity checked ──────────────────

def fill_trade(bars, i_sig: int, hold: int, step_ms: int) -> tuple | None:
    """Signal at close of bar i_sig -> entry open[i_sig+1], exit open[i_sig+1+hold].
    Returns (t_entry, t_exit, gross_ret, hold_hours) or None."""
    j = i_sig + 1
    k = j + hold
    if k >= len(bars):
        return None
    if bars[j][T] - bars[i_sig][T] != step_ms:   # gap between signal and fill
        return None
    e, x = bars[j][O], bars[k][O]
    if e <= 0 or x <= 0:
        return None
    hrs = (bars[k][T] - bars[j][T]) / HOUR_MS
    return bars[j][T], bars[k][T], x / e - 1.0, hrs


def net(gross: float, hrs: float) -> float:
    return gross - COST_RT - funding(hrs)


def window_of(t: float, sp) -> str | None:
    t0, tr, va, t1 = sp
    if t0 <= t <= tr:
        return "train"
    if tr < t <= va:
        return "validate"
    if va < t <= t1:
        return "test"
    return None


def assign(trades, sp) -> dict[str, list]:
    """Trade belongs to the window containing its entry; must COMPLETE inside it."""
    out = {"train": [], "validate": [], "test": []}
    ends = {"train": sp[1], "validate": sp[2], "test": sp[3]}
    for tr in trades:
        w = window_of(tr["t_in"], sp)
        if w and tr["t_out"] <= ends[w]:
            out[w].append(tr)
    return out


def stats(trades) -> dict:
    if not trades:
        return {"n": 0}
    ws = [tr.get("w", 1.0) for tr in trades]
    rs = [tr["r"] for tr in trades]
    sw = sum(ws)
    m = sum(w * r for w, r in zip(ws, rs)) / sw
    return {"n": len(rs), "mean_pct": round(m * 100, 4),
            "median_pct": round(statistics.median(rs) * 100, 4),
            "win": round(sum(1 for r in rs if r > 0) / len(rs), 3),
            "sum_pct": round(sum(w * r for w, r in zip(ws, rs)) * 100, 2)}


def wmean(trades) -> float:
    sw = sum(tr.get("w", 1.0) for tr in trades)
    return sum(tr.get("w", 1.0) * tr["r"] for tr in trades) / sw


# ── null machinery: same coin, random time, same hold, same weights ──────────

def mc_pvalue(oos_trades, sampler) -> float:
    """sampler(trade) -> a null trade dict (same coin, random OOS time) or None.
    2000 resamples of the full OOS set; p = P(null mean >= observed mean)."""
    obs = wmean(oos_trades)
    hits = draws = 0
    while draws < N_NULL:
        sample = []
        for tr in oos_trades:
            for _ in range(30):
                nt = sampler(tr)
                if nt is not None:
                    sample.append(nt)
                    break
        draws += 1
        if len(sample) < max(5, len(oos_trades) // 2):
            continue
        if wmean(sample) >= obs:
            hits += 1
    return hits / max(draws, 1)


def per_coin_sampler(bars_by_coin, sp, step_ms, sigma_by_coin=None,
                     sigma_target=None):
    """Null for per-coin families: random signal bar in OOS, same hold."""
    t0, tr_end, va_end, t1 = sp

    def sample(tr):
        bars = bars_by_coin.get(tr["coin"])
        if not bars or len(bars) < 10:
            return None
        i = random.randrange(1, len(bars) - tr["hold"] - 2)
        ft = fill_trade(bars, i, tr["hold"], step_ms)
        if ft is None:
            return None
        t_in, t_out, gross, hrs = ft
        if not (tr_end < t_in <= t1 and t_out <= t1):
            return None
        w = 1.0
        if sigma_by_coin is not None:
            sig = sigma_by_coin[tr["coin"]][i]
            if sig is None or sig <= 0:
                return None
            w = min(max(sigma_target / sig, 0.25), 2.0)
        return {"coin": tr["coin"], "r": net(gross, hrs), "w": w}
    return sample


# ── family runners ───────────────────────────────────────────────────────────

def run_grid(all_trades_by_cell: dict, sp_by_cell, sampler_by_cell) -> dict:
    """Generic: select best train cell (n>=30), freeze, score val/test, null p."""
    res = {"cells_train": {}}
    best, best_ev = None, None
    for cell, trades in all_trades_by_cell.items():
        byw = assign(trades, sp_by_cell[cell])
        st = stats(byw["train"])
        res["cells_train"][cell] = st
        if st["n"] >= MIN_TRAIN_N and (best_ev is None or st["mean_pct"] > best_ev):
            best, best_ev = cell, st["mean_pct"]
    if best is None:
        res["verdict"] = "UNDERSAMPLED"
        return res
    sp = sp_by_cell[best]
    byw = assign(all_trades_by_cell[best], sp)
    res["frozen_cell"] = best
    res["train"] = stats(byw["train"])
    res["validate"] = stats(byw["validate"])
    res["test"] = stats(byw["test"])
    oos = byw["validate"] + byw["test"]
    res["oos"] = stats(oos)
    if res["validate"]["n"] < MIN_OOS_N or res["test"]["n"] < MIN_OOS_N:
        res["verdict"] = "UNDERSAMPLED_OOS"
        return res
    p = mc_pvalue(oos, sampler_by_cell[best])
    res["p_null_oos"] = round(p, 4)
    v_m = res["validate"]["mean_pct"]
    t_m = res["test"]["mean_pct"]
    o_m = res["oos"]["mean_pct"]
    if v_m > 0 and t_m > 0 and p < ALPHA:
        res["verdict"] = "VALIDATED"
    elif (v_m > 0 and t_m > 0) or (o_m > 0 and min(v_m, t_m) > -0.05):
        res["verdict"] = "MARGINAL"
    else:
        res["verdict"] = "REFUTED"
    return res


def f1_vol_scaled_cooldown(hourly, daily) -> dict:
    """Momentum L-bar ret >= theta, hold 24h, cooldown 24 bars, 1/vol sizing."""
    out = {}
    for tf, bars_by_coin, Ls, hold, cool, sig_win, step in (
            ("hourly", hourly, (24, 72, 168), 24, 24, 168, HOUR_MS),
            ("daily", {c: b for c, b in daily.items()
                       if len(b) >= MIN_DAILY_BARS}, (5, 10, 20), 5, 5, 30, DAY_MS)):
        sp = splits(bars_by_coin["BTC"])
        sigma_by_coin = {c: rolling_sigma(rets_of(b), sig_win)
                         for c, b in bars_by_coin.items()}
        # frozen constant: train-window median sigma across coins
        pool = [s for c, b in bars_by_coin.items()
                for i, s in enumerate(sigma_by_coin[c])
                if s and sp[0] <= b[i][T] <= sp[1]]
        sig_tgt = statistics.median(pool)
        cells, sps, samplers = {}, {}, {}
        for Lb in Ls:
            for th in (0.03, 0.05, 0.10):
                key = f"L={Lb},theta={th}"
                trades = []
                for coin, bars in bars_by_coin.items():
                    nxt = 0
                    for i in range(max(Lb, sig_win + 1), len(bars) - hold - 1):
                        if i < nxt:
                            continue
                        p0 = bars[i - Lb][C]
                        if p0 <= 0 or bars[i][C] / p0 - 1.0 < th:
                            continue
                        sig = sigma_by_coin[coin][i]
                        if not sig or sig <= 0:
                            continue
                        ft = fill_trade(bars, i, hold, step)
                        if ft is None:
                            continue
                        t_in, t_out, gross, hrs = ft
                        w = min(max(sig_tgt / sig, 0.25), 2.0)
                        trades.append({"coin": coin, "t_in": t_in, "t_out": t_out,
                                       "r": net(gross, hrs), "w": w, "hold": hold})
                        nxt = i + 1 + hold + cool
                cells[key] = trades
                sps[key] = sp
                samplers[key] = per_coin_sampler(bars_by_coin, sp, step,
                                                 sigma_by_coin, sig_tgt)
        if tf == "hourly":
            out.update(run_grid(cells, sps, samplers))
        else:
            # daily = unselected sensitivity of the hourly-frozen cell (mapped)
            fro = out.get("frozen_cell")
            if fro:
                lmap = {"24": "5", "72": "10", "168": "20"}
                lh = fro.split(",")[0].split("=")[1]
                dkey = f"L={lmap[lh]},theta={fro.split('=')[2]}"
                byw = assign(cells.get(dkey, []), sp)
                out["daily_sensitivity"] = {
                    "cell": dkey,
                    "train": stats(byw["train"]),
                    "validate": stats(byw["validate"]),
                    "test": stats(byw["test"])}
    return out


class AltPanel:
    """Precomputed alt-basket structures: per-alt day->bar dict + sorted ts
    (for O(log n) prior-history counts). Master calendar = BTC daily bars."""

    def __init__(self, daily):
        self.cal = daily["BTC"]
        self.alts = [c for c in daily if c != "BTC"]
        self.bar_by_day = {c: {int(b[T] // DAY_MS): b for b in daily[c]}
                           for c in self.alts}
        import bisect
        self._bisect = bisect.bisect_left
        self.ts_by = {c: [b[T] for b in daily[c]] for c in self.alts}

    def n_prior(self, coin: str, t: float) -> int:
        return self._bisect(self.ts_by[coin], t)


def build_dominance(daily):
    """Returns (panel, alt_ret, logR_inc). logR_inc[k] = log(1+btc_ret_k) -
    log(1+altEW_ret_k); None if <5 alts alive that day."""
    import math
    panel = AltPanel(daily)
    cal = panel.cal
    btc_rets = rets_of(cal)
    alt_ret, inc = [None] * len(cal), [None] * len(cal)
    for k in range(1, len(cal)):
        d = int(cal[k][T] // DAY_MS)
        rs = []
        for c in panel.alts:
            bb = panel.bar_by_day[c]
            b0, b1 = bb.get(d - 1), bb.get(d)
            if b0 and b1 and b0[C] > 0:
                rs.append(b1[C] / b0[C] - 1.0)
        if len(rs) >= 5 and btc_rets[k] is not None:
            alt_ret[k] = sum(rs) / len(rs)
            inc[k] = math.log1p(btc_rets[k]) - math.log1p(alt_ret[k])
    return panel, alt_ret, inc


def basket_trade(panel: AltPanel, k_sig: int, hold: int) -> dict | None:
    """Alt-basket long: signal close day k -> entry open day k+1, exit open
    day k+1+hold. Eligible alt: >=60 prior daily bars, entry+exit bars exist."""
    cal = panel.cal
    j, k2 = k_sig + 1, k_sig + 1 + hold
    if k2 >= len(cal) or cal[j][T] - cal[k_sig][T] != DAY_MS:
        return None
    d_in, d_out = int(cal[j][T] // DAY_MS), int(cal[k2][T] // DAY_MS)
    legs = []
    for c in panel.alts:
        bb = panel.bar_by_day[c]
        b_in, b_out = bb.get(d_in), bb.get(d_out)
        if not b_in or not b_out or b_in[O] <= 0 or b_out[O] <= 0:
            continue
        if panel.n_prior(c, cal[j][T]) < 60:
            continue
        legs.append(b_out[O] / b_in[O] - 1.0)
    if len(legs) < 5:
        return None
    hrs = 24.0 * hold
    return {"coin": "ALT_EW", "t_in": cal[j][T], "t_out": cal[k2][T],
            "r": net(sum(legs) / len(legs), hrs), "hold": hold, "n_legs": len(legs)}


def basket_sampler(panel: AltPanel, sp):
    tr_end, t1 = sp[1], sp[3]

    def sample(tr):
        k = random.randrange(1, len(panel.cal) - tr["hold"] - 2)
        bt = basket_trade(panel, k, tr["hold"])
        if bt is None or not (tr_end < bt["t_in"] <= t1 and bt["t_out"] <= t1):
            return None
        return bt
    return sample


def f2_dominance_timebox(daily) -> dict:
    panel, alt_ret, inc = build_dominance(daily)
    cal = panel.cal
    sp = splits(cal)
    cells, sps, samplers = {}, {}, {}
    for K in (2, 3, 5):
        for W in (3, 5, 10):
            key = f"K={K},W={W}"
            trades, busy_until = [], -1
            for k in range(K, len(cal) - W - 1):
                if k <= busy_until:
                    continue
                if all(inc[k - j] is not None and inc[k - j] < 0 for j in range(K)):
                    bt = basket_trade(panel, k, W)
                    if bt:
                        trades.append(bt)
                        busy_until = k + 1 + W
            cells[key] = trades
            sps[key] = sp
            samplers[key] = basket_sampler(panel, sp)
    return run_grid(cells, sps, samplers)


def f4_dominance_transition(daily) -> dict:
    panel, alt_ret, inc = build_dominance(daily)
    cal = panel.cal
    sp = splits(cal)
    # R level series (only where increments exist; carry forward through gaps)
    R, lvl = [None] * len(cal), 0.0
    for k in range(len(cal)):
        if inc[k] is not None:
            lvl += inc[k]
        R[k] = lvl if any(x is not None for x in inc[:k + 1]) else None
    cells, sps, samplers = {}, {}, {}
    for s, l in ((5, 20), (10, 30), (20, 60)):
        sma = {}
        for n in (s, l):
            arr = [None] * len(cal)
            for k in range(n, len(cal)):
                seg = [R[j] for j in range(k - n + 1, k + 1) if R[j] is not None]
                if len(seg) == n:
                    arr[k] = sum(seg) / n
            sma[n] = arr
        for Hd in (5, 10, 20):
            key = f"sma={s}/{l},H={Hd}"
            trades, busy_until = [], -1
            for k in range(l + 1, len(cal) - Hd - 1):
                if k <= busy_until:
                    continue
                a0, b0 = sma[s][k - 1], sma[l][k - 1]
                a1, b1 = sma[s][k], sma[l][k]
                if None in (a0, b0, a1, b1):
                    continue
                if a0 >= b0 and a1 < b1:      # dominance rising -> falling flip
                    bt = basket_trade(panel, k, Hd)
                    if bt:
                        trades.append(bt)
                        busy_until = k + 1 + Hd
            cells[key] = trades
            sps[key] = sp
            samplers[key] = basket_sampler(panel, sp)
    return run_grid(cells, sps, samplers)


def f3_ema_crossing(hourly, daily) -> dict:
    cells, sps, samplers = {}, {}, {}
    for tf, bars_by_coin, step in (("1h", hourly, HOUR_MS),
                                   ("1d", {c: b for c, b in daily.items()
                                           if len(b) >= MIN_DAILY_BARS}, DAY_MS)):
        sp = splits(bars_by_coin["BTC"])
        for f, s in ((8, 21), (12, 26), (20, 50)):
            key = f"{tf},ema={f}/{s}"
            trades = []
            for coin, bars in bars_by_coin.items():
                closes = [b[C] for b in bars]
                if len(closes) < 3 * s + 5:
                    continue
                ef, es = ema(closes, f), ema(closes, s)
                i = 3 * s
                while i < len(bars) - 2:
                    if ef[i - 1] <= es[i - 1] and ef[i] > es[i]:
                        # find exit: cross-down at bar j
                        j = i + 1
                        while j < len(bars) - 1 and not (ef[j - 1] >= es[j - 1]
                                                         and ef[j] < es[j]):
                            j += 1
                        if j >= len(bars) - 1:
                            break                     # open at data end: discard
                        hold = j - i
                        ft = fill_trade(bars, i, hold, step)
                        if ft is not None:
                            t_in, t_out, gross, hrs = ft
                            trades.append({"coin": coin, "t_in": t_in,
                                           "t_out": t_out, "r": net(gross, hrs),
                                           "hold": hold})
                        i = j + 1
                    else:
                        i += 1
            cells[key] = trades
            sps[key] = sp
            samplers[key] = per_coin_sampler(bars_by_coin, sp, step)
    return run_grid(cells, sps, samplers)


def f5_ema_soft_alignment(hourly, daily) -> dict:
    out = {}
    for tf, bars_by_coin, Hs, step in (
            ("hourly", hourly, (12, 24, 72), HOUR_MS),
            ("daily", {c: b for c, b in daily.items()
                       if len(b) >= MIN_DAILY_BARS}, (2, 5, 10), DAY_MS)):
        sp = splits(bars_by_coin["BTC"])
        cells, sps, samplers = {}, {}, {}
        emas = {}
        for coin, bars in bars_by_coin.items():
            closes = [b[C] for b in bars]
            if len(closes) >= 170:
                emas[coin] = (ema(closes, 8), ema(closes, 21), ema(closes, 55))
        for x in (0.005, 0.01, 0.02):
            for Hh in Hs:
                key = f"x={x},H={Hh}"
                trades = []
                for coin, bars in bars_by_coin.items():
                    if coin not in emas:
                        continue
                    e8, e21, e55 = emas[coin]
                    nxt = 0
                    for i in range(165, len(bars) - Hh - 1):
                        if i < nxt:
                            continue
                        c = bars[i][C]
                        if not (e8[i] > e21[i] > e55[i]) or e8[i] <= 0:
                            continue
                        if abs(c / e8[i] - 1.0) > x:
                            continue
                        ft = fill_trade(bars, i, Hh, step)
                        if ft is None:
                            continue
                        t_in, t_out, gross, hrs = ft
                        trades.append({"coin": coin, "t_in": t_in, "t_out": t_out,
                                       "r": net(gross, hrs), "hold": Hh})
                        nxt = i + 1 + Hh + Hh      # cooldown = H after exit
                cells[key] = trades
                sps[key] = sp
                samplers[key] = per_coin_sampler(bars_by_coin, sp, step)
        if tf == "hourly":
            out.update(run_grid(cells, sps, samplers))
        else:
            fro = out.get("frozen_cell")
            if fro:
                hmap = {"12": "2", "24": "5", "72": "10"}
                hh = fro.split("H=")[1]
                dkey = f"{fro.split(',')[0]},H={hmap[hh]}"
                byw = assign(cells.get(dkey, []), sp)
                out["daily_sensitivity"] = {
                    "cell": dkey, "train": stats(byw["train"]),
                    "validate": stats(byw["validate"]), "test": stats(byw["test"])}
    return out


def f6_burst_persistence(hourly) -> dict:
    sp = splits(hourly["BTC"])
    sig_by, medv_by, rets_by = {}, {}, {}
    for coin, bars in hourly.items():
        r = rets_of(bars)
        rets_by[coin] = r
        sig_by[coin] = rolling_sigma(r, 168)
        mv = [None] * len(bars)
        vols = [b[V] for b in bars]
        for i in range(169, len(bars)):
            mv[i] = statistics.median(vols[i - 168:i])
        medv_by[coin] = mv
    cells, sps, samplers = {}, {}, {}
    for Ns in (2, 3, 4):
        for Hh in (4, 12, 24):
            key = f"Nsig={Ns},H={Hh}"
            trades = []
            for coin, bars in hourly.items():
                r, sg, mv = rets_by[coin], sig_by[coin], medv_by[coin]
                nxt = 0
                for i in range(169, len(bars) - Hh - 3):
                    if i < nxt:
                        continue
                    if r[i] is None or sg[i] is None or sg[i] <= 0 or mv[i] is None:
                        continue
                    if r[i] <= 0 or r[i] < Ns * sg[i]:
                        continue
                    if mv[i] <= 0 or bars[i][V] < 3 * mv[i]:
                        continue
                    if bars[i + 1][T] - bars[i][T] != HOUR_MS:
                        continue
                    if bars[i + 1][C] < bars[i][C]:      # next bar must HOLD it
                        continue
                    ft = fill_trade(bars, i + 1, Hh, HOUR_MS)  # entry open i+2
                    if ft is None:
                        continue
                    t_in, t_out, gross, hrs = ft
                    trades.append({"coin": coin, "t_in": t_in, "t_out": t_out,
                                   "r": net(gross, hrs), "hold": Hh})
                    nxt = i + 2 + Hh + Hh
            cells[key] = trades
            sps[key] = sp
            samplers[key] = per_coin_sampler(hourly, sp, HOUR_MS)
    return run_grid(cells, sps, samplers)


def f7_vol_normalized_pressure(hourly) -> dict:
    sp = splits(hourly["BTC"])
    press = {}
    for coin, bars in hourly.items():
        p = []
        for b in bars:
            rng = b[H] - b[L]
            p.append(((b[C] - b[L]) - (b[H] - b[C])) / rng * b[V] if rng > 0 else 0.0)
        press[coin] = p
    cells, sps, samplers = {}, {}, {}
    for W in (6, 12, 24):
        S_by = {}
        for coin, p in press.items():
            S = [None] * len(p)
            run = sum(p[:W])
            S[W - 1] = run
            for i in range(W, len(p)):
                run += p[i] - p[i - W]
                S[i] = run
            S_by[coin] = S
        for Hh in (6, 12, 24):
            key = f"W={W},H={Hh}"
            trades = []
            for coin, bars in hourly.items():
                S = S_by[coin]
                nxt = 0
                for i in range(W - 1 + 169, len(bars) - Hh - 2):
                    if i < nxt:
                        continue
                    hist = [s for s in S[i - 168:i] if s is not None]
                    if len(hist) < 84:
                        continue
                    mu, sd = statistics.mean(hist), statistics.pstdev(hist)
                    if sd <= 0 or S[i] is None or (S[i] - mu) / sd < 2.0:
                        continue
                    ft = fill_trade(bars, i, Hh, HOUR_MS)
                    if ft is None:
                        continue
                    t_in, t_out, gross, hrs = ft
                    trades.append({"coin": coin, "t_in": t_in, "t_out": t_out,
                                   "r": net(gross, hrs), "hold": Hh})
                    nxt = i + 1 + Hh + Hh
            cells[key] = trades
            sps[key] = sp
            samplers[key] = per_coin_sampler(hourly, sp, HOUR_MS)
    return run_grid(cells, sps, samplers)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    hourly = load_hourly()
    daily = load_daily()
    print(f"hourly: {len(hourly)} coins; daily: {len(daily)} coins "
          f"({sum(1 for b in daily.values() if len(b) >= MIN_DAILY_BARS)} with "
          f">={MIN_DAILY_BARS} bars)")
    res = {"alpha_bonferroni_per_family": round(ALPHA, 5),
           "alpha_programwide_60cells": round(0.05 / 60, 6),
           "cost_model": "25bps RT + 1.25e-5/h funding for holds>=8h, LONG only",
           "seed": 20260712}
    for name, fn, args in (
            ("F1_vol_scaled_cooldown", f1_vol_scaled_cooldown, (hourly, daily)),
            ("F2_dominance_timebox", f2_dominance_timebox, (daily,)),
            ("F3_ema_oscillator_crossing", f3_ema_crossing, (hourly, daily)),
            ("F4_dominance_transition", f4_dominance_transition, (daily,)),
            ("F5_ema_soft_alignment", f5_ema_soft_alignment, (hourly, daily)),
            ("F6_burst_persistence", f6_burst_persistence, (hourly,)),
            ("F7_vol_normalized_pressure", f7_vol_normalized_pressure, (hourly,))):
        print(f"\n=== {name} ===", flush=True)
        r = fn(*args)
        res[name] = r
        print(f"  frozen: {r.get('frozen_cell')}  verdict: {r.get('verdict')}")
        for w in ("train", "validate", "test"):
            if w in r:
                print(f"  {w}: {r[w]}")
        if "p_null_oos" in r:
            print(f"  p_null_oos: {r['p_null_oos']}")
    json.dump(res, open(RESULTS, "w"), indent=1)
    print(f"\nwrote {RESULTS}")


if __name__ == "__main__":
    main()
