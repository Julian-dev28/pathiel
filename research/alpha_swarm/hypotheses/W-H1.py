"""W-H1 — BTC->ALT residual catch-up after 2-sigma BTC 1h shocks (Lane H).

PRE-REGISTERED SPEC (declared before running):
- Data: extended 1h cache (W-H0, ~208d, 40 coins), lookahead-safe throughout.
- Shock: |BTC 1h close-to-close ret at bar i| > 2 * sigma_168, sigma = pstdev of
  the 168 hourly BTC rets strictly BEFORE bar i. Signal known at close[i].
- Residual response per alt j at bar i: resid_j = altret_j[i] - beta_j * btcret[i],
  beta_j = rolling 240h OLS beta on rets strictly before i.
- LAGGARDS = the 8 alts that most under-reacted in the shock's direction:
  up-shock -> 8 lowest resid (LONG alt, hedge SHORT beta_j * BTC);
  down-shock -> 8 highest resid (SHORT alt, hedge LONG beta_j * BTC).
- Fill open[i+1], exit open[i+1+H], H in {1,3,6}. Episode dedup: 6h spacing.
- Pair return = sign * (alt_fwd - beta_j * btc_fwd). Costs per pair =
  tier bps on the alt leg + tier/2 bps on the BTC hedge leg (BTC is deepest);
  reported at tiers {0,12,25} -> net = gross - 1.5*tier.
- Unit of significance = per-EVENT basket mean (8 legs are cross-correlated).
- Cells: ALL / up-shock / down-shock / liquidity-half / beta-tercile. n>=15
  events per cell or NOT-RIPE.
- MC null (>=2000 iter): identical rule applied at every NON-shock bar forms the
  pool of basket returns; shuffle_label_p tests whether the 2-sigma conditioning
  beats size-matched random draws from that pool.
- OOS: first/second time half at 12bps-tier equivalent (net incl. hedge cost).
- Hold <= 6h -> no funding accrual applied (HL funding is hourly ~0.00125%/h;
  <=6h drag is < 0.01%, noted as a caveat, not modeled).
- Prior art honored: btc_leadlag.md refuted NEXT-BAR directional/xs follow;
  this tests hedged RESIDUAL convergence at 1-6h, a different claim.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))

import importlib
wh0 = importlib.import_module("W-H0_fetch")
import mc_null  # noqa: E402
import alpha_lib as al  # noqa: E402

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
HOUR = 3_600_000
SIGMA_W = 168
BETA_W = 240
K = 8
HOLDS = [1, 3, 6]
DEDUP_MS = 6 * HOUR
TIERS = [0, 12, 25]


def main() -> None:
    cs = wh0.load_ext()
    d = al.load_dataset()
    vol = {c: d["universe"].get(c, {}).get("dayNtlVlm", 0.0) for c in cs}
    coins = [c for c in cs if c != "BTC" and len(cs[c]) > BETA_W + 50]
    med_vol = statistics.median([vol[c] for c in coins])

    btc = cs["BTC"]
    bidx = wh0.bar_index(btc)
    btc_rets_l = wh0.hourly_rets(btc)
    btc_rets = dict(btc_rets_l)
    sig = wh0.rolling_sigma(btc_rets_l, SIGMA_W)
    ts_sorted = [t for t, _ in btc_rets_l]

    alt_rets: dict[str, dict[int, float]] = {}
    alt_idx: dict[str, dict[int, int]] = {}
    betas: dict[str, dict[int, float]] = {}
    for c in coins:
        alt_rets[c] = dict(wh0.hourly_rets(cs[c]))
        alt_idx[c] = wh0.bar_index(cs[c])
        betas[c] = wh0.rolling_beta(alt_rets[c], btc_rets, ts_sorted, BETA_W)

    def basket_at(t: int, direction: int, hold: int):
        """Apply the pre-registered rule at bar-open-timestamp t (shock bar).
        direction=+1 up-shock, -1 down-shock. Returns (basket_ret, legs) or None."""
        br = btc_rets.get(t)
        if br is None:
            return None
        btc_fwd = wh0.fwd_open_ret(btc, bidx, t, hold)
        if btc_fwd is None:
            return None
        cand = []
        for c in coins:
            ar = alt_rets[c].get(t)
            b = betas[c].get(t)
            if ar is None or b is None:
                continue
            cand.append((ar - b * br, c, b))
        if len(cand) < 2 * K:
            return None
        cand.sort()
        picks = cand[:K] if direction > 0 else cand[-K:]
        legs = []
        for resid, c, b in picks:
            alt_fwd = wh0.fwd_open_ret(cs[c], alt_idx[c], t, hold)
            if alt_fwd is None:
                continue
            pair = direction * (alt_fwd - b * btc_fwd)
            legs.append({"coin": c, "beta": b, "resid": resid, "pair": pair,
                         "liq_hi": vol[c] >= med_vol})
        if len(legs) < K // 2:
            return None
        return statistics.mean(l["pair"] for l in legs), legs

    # ── events (deduped) + non-shock pool bars ──────────────────────────────
    shock_bars, pool_bars = [], []
    for t in ts_sorted:
        s = sig.get(t)
        r = btc_rets.get(t)
        if s is None or r is None or s <= 0:
            continue
        if abs(r) > 2 * s:
            shock_bars.append({"t": t, "dir": 1 if r > 0 else -1})
        else:
            pool_bars.append({"t": t, "dir": 1 if r > 0 else -1})
    events = wh0.dedup_episodes(shock_bars, DEDUP_MS)
    print(f"shock bars={len(shock_bars)} deduped events={len(events)} "
          f"(up={sum(1 for e in events if e['dir'] > 0)}, "
          f"dn={sum(1 for e in events if e['dir'] < 0)}), pool bars={len(pool_bars)}")

    for hold in HOLDS:
        trades = []   # per-event basket
        legs_all = []
        for e in events:
            got = basket_at(e["t"], e["dir"], hold)
            if got is None:
                continue
            bk, legs = got
            trades.append({"t": e["t"], "dir": e["dir"], "ret": bk})
            for l in legs:
                l2 = dict(l); l2["t"] = e["t"]; l2["dir"] = e["dir"]
                legs_all.append(l2)
        pool = []
        for p in pool_bars[::2]:  # every 2nd non-shock bar: plenty + faster
            got = basket_at(p["t"], p["dir"], hold)
            if got is not None:
                pool.append(got[0])

        def cell(name, ts):
            if len(ts) < 15:
                print(f"  H={hold} {name:<14} n={len(ts):<3} NOT-RIPE")
                return
            rets = [x["ret"] for x in ts]
            gross = statistics.mean(rets)
            net12 = gross - 1.5 * 12 / 1e4
            net25 = gross - 1.5 * 25 / 1e4
            h1, h2 = al.time_split(ts)
            e1 = statistics.mean([x["ret"] for x in h1]) - 1.5 * 12 / 1e4 if h1 else float("nan")
            e2 = statistics.mean([x["ret"] for x in h2]) - 1.5 * 12 / 1e4 if h2 else float("nan")
            mc = mc_null.shuffle_label_p(rets, pool, n_iter=3000, seed=7)
            print(f"  H={hold} {name:<14} n={len(ts):<3} gross={100*gross:+.3f}% "
                  f"net12={100*net12:+.3f}% net25={100*net25:+.3f}% "
                  f"OOS12 {100*e1:+.3f}/{100*e2:+.3f} mc_p={mc['p_one_sided']} "
                  f"excess={100*(mc['excess'] or 0):+.3f}%")

        print(f"\n== HOLD {hold}h  (per-event basket, hedge-inclusive costs) ==")
        cell("ALL", trades)
        cell("up-shock", [x for x in trades if x["dir"] > 0])
        cell("down-shock", [x for x in trades if x["dir"] < 0])
        # cohort views (per-leg averaged inside each event to keep event units)
        for nm, pred in [("liq-high", lambda l: l["liq_hi"]),
                         ("liq-low", lambda l: not l["liq_hi"])]:
            per_ev: dict[int, list[float]] = {}
            for l in legs_all:
                if pred(l):
                    per_ev.setdefault(l["t"], []).append(l["pair"])
            ts = [{"t": t, "ret": statistics.mean(v)} for t, v in per_ev.items()
                  if len(v) >= 2]
            cell(nm, sorted(ts, key=lambda x: x["t"]))
        # beta terciles across legs (per event mean of in-tercile legs)
        bvals = sorted(l["beta"] for l in legs_all)
        if bvals:
            q1 = bvals[len(bvals) // 3]; q2 = bvals[2 * len(bvals) // 3]
            for nm, lo, hi in [("beta-low", -9, q1), ("beta-mid", q1, q2),
                               ("beta-high", q2, 9)]:
                per_ev = {}
                for l in legs_all:
                    if lo <= l["beta"] < hi:
                        per_ev.setdefault(l["t"], []).append(l["pair"])
                ts = [{"t": t, "ret": statistics.mean(v)} for t, v in per_ev.items()
                      if len(v) >= 2]
                cell(nm, sorted(ts, key=lambda x: x["t"]))


if __name__ == "__main__":
    main()
