#!/usr/bin/env python3
"""W-E2 — RTH-open volatility transfer: does the overnight xyz drift revert (or
persist) when real underlying flow arrives at the US open?

Episode (dedup unit = one weekday overnight, plain 1-calendar-day gaps only —
weekends are W-E1's): trading days p -> d, (d-p).days == 1.
  ref  = xyz close at p's RTH close (20:00 EDT / 21:00 EST UTC).
  m    = overnight move = preopen close on d (13:00 UTC EDT) vs ref.
  fill = open of the next 1h bar (13:00 UTC EDT bar open) — 30min before the
         cash open; xyz trades 24/7 so the fill is real. A second fill variant
         at open+30m (14:00 bar open) checks robustness to the open auction.
  exit = d's RTH close (primary); open+2h close (fast variant).
Rule: |m| >= thr {0.5%,1%,2%} -> fade / momo. Basket per day = ONE episode.
Extra: residualize m on BTC's same-window move (PIT expanding beta, min 20 obs)
to split "crypto-tracked overnight drift" from idiosyncratic drift.
Nulls: random-sign per episode + same-side pool (mc_null). Stops swept on fade.
"""
from __future__ import annotations
import random, statistics, sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib
we = importlib.import_module("W-E_lib")
import alpha_lib
import mc_null

T = we.T


def overnight_obs(d: dict) -> list[dict]:
    # BTC overnight moves per day (for residualization)
    btc_idx = we.by_t(d["candles_1h"]["BTC"])
    obs = []
    for coin in d["coins"]:
        bars = d["candles_1h"].get(coin) or []
        if len(bars) < 24 * 10:
            continue
        idx = we.by_t(bars)
        t0 = date.fromtimestamp(bars[0][T] / 1000)
        t1 = date.fromtimestamp(bars[-1][T] / 1000)
        day = t0
        while day <= t1:
            if we.is_trading_day(day):
                p = we.prev_trading_day(day)
                if (day - p).days == 1 and p >= t0:
                    ref = we.last_rth_close(idx, p)
                    pre, t_close = we.preopen_close(idx, day)
                    if ref and pre:
                        m = (pre - ref) / ref
                        exit_close = we.rth_utc(day)[1]
                        r1 = we.hold_return(idx, t_close, exit_close, "long")
                        r_fast = we.hold_return(idx, t_close, t_close + 3 * we.HOUR, "long")
                        r_post = we.hold_return(idx, t_close + we.HOUR, exit_close, "long")
                        # BTC same overnight window
                        bref = we.last_rth_close(btc_idx, p)
                        bpre = we.close_at(btc_idx, t_close)
                        bm = (bpre - bref) / bref if (bref and bpre) else None
                        if r1 is not None:
                            obs.append({"coin": coin, "ep": day.isoformat(), "t": t_close,
                                        "m": m, "bm": bm, "r1": r1, "r_fast": r_fast,
                                        "r_post": r_post, "idx": idx, "fill_t": t_close,
                                        "exit1": exit_close,
                                        "is_index": coin in we.INDEX_NAMES})
            day += timedelta(days=1)
        del idx
    return obs


def corr(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (len(xs) * sx * sy)


def random_sign_null(rets, n_iter=5000, seed=0):
    rng = random.Random(seed)
    obs_m = statistics.mean(rets)
    ge = sum(1 for _ in range(n_iter)
             if statistics.mean(r * (1 if rng.random() < 0.5 else -1) for r in rets) >= obs_m)
    return ge / n_iter


def residualize(obs: list[dict]) -> None:
    """PIT expanding per-name beta of m on bm; writes o['m_res'] (None till 20 obs)."""
    from collections import defaultdict
    hist = defaultdict(list)
    for o in sorted(obs, key=lambda x: x["t"]):
        o["m_res"] = None
        if o["bm"] is None:
            continue
        h = hist[o["coin"]]
        if len(h) >= 20:
            mx = statistics.mean(x for x, _ in h)
            my = statistics.mean(y for _, y in h)
            varx = sum((x - mx) ** 2 for x, _ in h)
            beta = (sum((x - mx) * (y - my) for x, y in h) / varx) if varx else 0.0
            o["m_res"] = o["m"] - beta * o["bm"]
        h.append((o["bm"], o["m"]))


def run_rule(obs, key, side_mode, thr, label):
    trades = []
    for o in obs:
        v = o.get(key)
        if v is None or abs(v) < thr:
            continue
        sgn = -1.0 if side_mode == "fade" else 1.0
        dirn = sgn * (1 if v > 0 else -1)
        trades.append({"t": o["t"], "ep": o["ep"], "ret": dirn * o["r1"],
                       "side": "long" if dirn > 0 else "short", "o": o})
    basket = we.basket_by_key(trades)
    if len(basket) < 15:
        print(f"\n{label}: only {len(basket)} episodes — below n=15, no verdict")
        return
    s = alpha_lib.summarize(basket)
    p_sign = random_sign_null([b["ret"] for b in basket])
    shorts = [t["ret"] for t in trades if t["side"] == "short"]
    pool_short = [-o["r1"] for o in obs]
    p_pool = (mc_null.shuffle_label_p(shorts, pool_short)["p_one_sided"]
              if len(shorts) >= 5 else None)
    print(f"\n{label}  episodes={s['n']} (name-trades={len(trades)})  "
          f"p_sign={p_sign:.4f}  p_shortpool={p_pool}")
    print(we.fmt_summary(s))
    return trades


def main():
    d = we.load()
    obs = overnight_obs(d)
    residualize(obs)
    eps = sorted({o["ep"] for o in obs})
    print(f"observations: {len(obs)} name-nights across {len(eps)} weekday overnights")

    print("\n── structure: corr(overnight move, next-window return) ──")
    for label, rows in (("ALL", obs), ("indices", [o for o in obs if o["is_index"]]),
                        ("singles", [o for o in obs if not o["is_index"]])):
        print(f"{label:8s} full-session {corr([o['m'] for o in rows], [o['r1'] for o in rows]):+.3f}"
              f"  first3h {corr([o['m'] for o in rows], [o['r_fast'] for o in rows]):+.3f}"
              f"  post-open->close {corr([o['m'] for o in rows], [o['r_post'] for o in rows]):+.3f}"
              f"  (n={len(rows)})")
    # per-day basket corr
    from collections import defaultdict
    g = defaultdict(list)
    for o in obs:
        g[o["ep"]].append(o)
    bm = [statistics.mean(x["m"] for x in g[e]) for e in sorted(g)]
    br = [statistics.mean(x["r1"] for x in g[e]) for e in sorted(g)]
    print(f"per-DAY basket corr: {corr(bm, br):+.3f}  (n={len(bm)} days)")

    print("\n── raw overnight move rules ──")
    for side_mode in ("fade", "momo"):
        for thr in (0.005, 0.01, 0.02):
            tr = run_rule(obs, "m", side_mode, thr,
                          f"{side_mode.upper()} |overnight|>={thr*100:.1f}%")
            if side_mode == "fade" and thr == 0.01 and tr:
                for sp in (0.08, 0.15, 0.20, 0.25, 0.40):
                    rs = [we.hold_return(t_["o"]["idx"], t_["o"]["fill_t"],
                                         t_["o"]["exit1"], t_["side"], stop_pct=sp)
                          for t_ in tr]
                    rs = [r for r in rs if r is not None]
                    print(f"  stop {int(sp*100)}%: mean {100*statistics.mean(rs):+.3f}% (n={len(rs)})")

    print("\n── BTC-residualized overnight move rules (idio drift only) ──")
    for side_mode in ("fade", "momo"):
        for thr in (0.005, 0.01, 0.02):
            run_rule(obs, "m_res", side_mode, thr,
                     f"{side_mode.upper()}-RESID |m_res|>={thr*100:.1f}%")

    print("\n── post-open fill variant (fill at open+30m bar) ──")
    for thr in (0.01, 0.02):
        trades = []
        for o in obs:
            if abs(o["m"]) < thr or o["r_post"] is None:
                continue
            dirn = -1 if o["m"] > 0 else 1
            trades.append({"t": o["t"], "ep": o["ep"], "ret": dirn * o["r_post"]})
        basket = we.basket_by_key(trades)
        if len(basket) >= 15:
            s = alpha_lib.summarize(basket)
            print(f"\nFADE post-open fill |m|>={thr*100:.1f}%  episodes={s['n']}")
            print(we.fmt_summary(s))


if __name__ == "__main__":
    main()
