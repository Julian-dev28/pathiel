#!/usr/bin/env python3
"""W-E1 — weekend structure on xyz tokenized equities: gap-fade vs price discovery.

The xyz perps trade all weekend while the underlying is closed. Two mutually
exclusive stories:
  (a) PRICE DISCOVERY — the weekend xyz move anticipates Monday's real-flow move
      (corr(weekend move, reopen-session return) > 0, momentum tradeable);
  (b) GAP-FADE — weekend moves are thin-liquidity overshoot that real flow
      reverts at the reopen (corr < 0, fade tradeable).

Episode (dedup unit = one closure span, i.e. one weekend / long weekend):
  f = last trading day before a >=2-calendar-day closure, n = next trading day.
  ref    = xyz close printing at f's RTH close (20:00 EDT / 21:00 EST, UTC).
  m      = weekend move = preopen close on n (13:00 UTC EDT / 14:00 EST) vs ref.
  fill   = open of the very next 1h bar (lookahead-safe i+1 open fill).
  exit   = n's RTH close (primary) / n+1's RTH close (continuation check).
Rule swept: |m| >= thr in {0.5%,1%,2%}, side = fade (-sign(m)) and momo (+sign).
Basket across triggered names per episode -> ONE trade per weekend.
Nulls: (1) random-sign per episode (does the direction conditioning inform?),
       (2) same-side random-weekend pool via mc_null (tape-drift check).
Stop sweep {8,15,20,25,40}% per SWARM-RULES (mostly inert at equity vol - reported).
Costs: alpha_lib tiers 0/6/12/25/50bps round-trip. xyz funding over a ~7h hold is
<2bp and inside the 25bps tier.
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

T, C = we.T, we.C


def collect_episodes(d: dict) -> list[dict]:
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
                n = we.next_trading_day(day)
                if (n - day).days >= 2 and n <= t1:   # closure span (weekend+)
                    ref = we.last_rth_close(idx, day)
                    pre, t_close = we.preopen_close(idx, n)
                    if ref and pre:
                        m = (pre - ref) / ref
                        fill_t = t_close                    # next bar OPEN == t_close print
                        exit1 = we.rth_utc(n)[1]
                        r1 = we.hold_return(idx, fill_t, exit1, "long")
                        n2 = we.next_trading_day(n)
                        r2 = None
                        if (n2 - n).days == 1:
                            r2 = we.hold_return(idx, fill_t, we.rth_utc(n2)[1], "long")
                        if r1 is not None:
                            obs.append({"coin": coin, "ep": day.isoformat(),
                                        "t": fill_t, "m": m, "r1": r1, "r2": r2,
                                        "idx": idx, "fill_t": fill_t, "exit1": exit1,
                                        "is_index": coin in we.INDEX_NAMES})
            day += timedelta(days=1)
        del idx
    return obs


def corr(xs, ys):
    if len(xs) < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) * sx * sy)


def random_sign_null(rets: list[float], n_iter=5000, seed=0) -> float:
    """P(mean of random-signed |exposures| >= observed mean). Strips tape drift."""
    rng = random.Random(seed)
    obs = statistics.mean(rets)
    ge = 0
    for _ in range(n_iter):
        m = statistics.mean(r * (1 if rng.random() < 0.5 else -1) for r in rets)
        if m >= obs:
            ge += 1
    return ge / n_iter


def main():
    d = we.load()
    obs = collect_episodes(d)
    eps = sorted({o["ep"] for o in obs})
    print(f"observations: {len(obs)} name-weekends across {len(eps)} closure spans, "
          f"{len({o['coin'] for o in obs})} names")

    # ── structure: does the weekend move predict the reopen session? ──
    for label, rows in (("ALL names", obs),
                        ("indices only", [o for o in obs if o["is_index"]]),
                        ("single names", [o for o in obs if not o["is_index"]])):
        c1 = corr([o["m"] for o in rows], [o["r1"] for o in rows])
        rows2 = [o for o in rows if o["r2"] is not None]
        c2 = corr([o["m"] for o in rows2], [o["r2"] for o in rows2])
        print(f"corr(weekend move, reopen ret) {label}: day1 {c1 if c1 is None else round(c1,3)}"
              f"  day1+2 {c2 if c2 is None else round(c2,3)}  (n={len(rows)})")

    # per-episode basket corr (the honest, dedup unit)
    from collections import defaultdict
    g = defaultdict(list)
    for o in obs:
        g[o["ep"]].append(o)
    bm = [statistics.mean(x["m"] for x in g[e]) for e in sorted(g)]
    br = [statistics.mean(x["r1"] for x in g[e]) for e in sorted(g)]
    print(f"corr per-weekend BASKET: {round(corr(bm, br), 3)}  (n={len(bm)} weekends)")

    # ── tradeable rule sweep ──
    for side_mode in ("fade", "momo"):
        for thr in (0.005, 0.01, 0.02):
            trades = []
            for o in obs:
                if abs(o["m"]) < thr:
                    continue
                sgn = -1.0 if side_mode == "fade" else 1.0
                dirn = sgn * (1 if o["m"] > 0 else -1)
                trades.append({"t": o["t"], "ep": o["ep"], "coin": o["coin"],
                               "ret": dirn * o["r1"], "side": "long" if dirn > 0 else "short",
                               "o": o})
            basket = we.basket_by_key(trades)
            if not basket:
                continue
            s = alpha_lib.summarize(basket)
            p_sign = random_sign_null([b["ret"] for b in basket])
            # same-side pool null on the name-level trades (tape check)
            shorts = [t["ret"] for t in trades if t["side"] == "short"]
            pool_short = [-o["r1"] for o in obs]
            p_pool = (mc_null.shuffle_label_p(shorts, pool_short)["p_one_sided"]
                      if len(shorts) >= 5 else None)
            print(f"\n{side_mode.upper()} |m|>={thr*100:.1f}%  episodes={s['n']} "
                  f"(name-trades={len(trades)})  p_sign={p_sign:.3f}  p_shortpool={p_pool}")
            print(we.fmt_summary(s))
            # stop sweep on the fade at the primary threshold
            if side_mode == "fade" and thr == 0.01 and trades:
                for sp in (0.08, 0.15, 0.20, 0.25, 0.40):
                    rs = []
                    for t_ in trades:
                        o = t_["o"]
                        r = we.hold_return(o["idx"], o["fill_t"], o["exit1"],
                                           t_["side"], stop_pct=sp)
                        if r is not None:
                            rs.append(r)
                    print(f"  stop {int(sp*100)}%: mean {100*statistics.mean(rs):+.3f}% "
                          f"(n={len(rs)})")

    # ── leg decomposition: indices vs single names (W-A2-style, not selection) ──
    print("\n── decomposition: FADE |m|>=1% by instrument class ──")
    for label, rows in (("indices", [o for o in obs if o["is_index"]]),
                        ("singles", [o for o in obs if not o["is_index"]])):
        trades = []
        for o in rows:
            if abs(o["m"]) < 0.01:
                continue
            dirn = -1 if o["m"] > 0 else 1
            trades.append({"t": o["t"], "ep": o["ep"],
                           "ret": dirn * o["r1"]})
        basket = we.basket_by_key(trades)
        if len(basket) < 15:
            print(f"{label}: only {len(basket)} episodes — below n=15, no verdict")
            continue
        s = alpha_lib.summarize(basket)
        p_sign = random_sign_null([b["ret"] for b in basket])
        print(f"\n{label}  episodes={s['n']} (name-trades={len(trades)})  p_sign={p_sign:.4f}")
        print(we.fmt_summary(s))

    # ── weekend realized-vol structure (context stat) ──
    for coin in ("xyz:SP500", "xyz:XYZ100", "xyz:NVDA", "xyz:MSTR"):
        bars = d["candles_1h"].get(coin) or []
        idx = we.by_t(bars)
        buckets = {"rth": [], "closed_wk": [], "weekend": []}
        for b in bars[1:]:
            t = int(b[T])
            prev = idx.get(t - we.HOUR)
            if not prev or not float(prev[C]):
                continue
            r = (float(b[C]) - float(prev[C])) / float(prev[C])
            day = date.fromtimestamp(t / 1000)
            if not we.is_trading_day(day):
                buckets["weekend"].append(r)
            else:
                o_, c_ = we.rth_utc(day)
                (buckets["rth"] if o_ <= t < c_ else buckets["closed_wk"]).append(r)
        out = {k: round(statistics.pstdev(v) * 100, 3) if len(v) > 30 else None
               for k, v in buckets.items()}
        print(f"hourly ret stdev% {coin}: RTH {out['rth']}  closed-weekday "
              f"{out['closed_wk']}  weekend {out['weekend']}")


if __name__ == "__main__":
    main()
