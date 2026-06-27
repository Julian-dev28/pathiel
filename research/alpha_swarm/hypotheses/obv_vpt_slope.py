"""C13 obv_vpt_slope — OBV/VPT flow ranking as a cross-sectional factor.

Daily cross-sectional. Score per coin = net-flow-fraction = sum(sign(ret)*vol)/sum(vol)
over last L days (volume-weighted accumulation, comparable across coins, in [-1,1]).
Long top-n / short bottom-n, hold H, non-overlapping rebalances. Market-neutral so the null
is ~0; report OOS both-halves + slippage. CRITICAL control: compare to a price-MOMENTUM book
(same L/n/H) since OBV-slope correlates with price momentum -> does flow add anything?
"""
from __future__ import annotations
import statistics
import alpha_lib as A


def build_panel(d):
    coins = [c for c in d["coins"] if len(A.candles(d, c, "1d")) >= 60]
    cds = {c: A.candles(d, c, "1d") for c in coins}
    n = min(len(cds[c]) for c in coins)
    return coins, cds, n


def obv_score(cd, i, L):
    num = den = 0.0
    for k in range(i - L + 1, i + 1):
        r = cd[k][A.C] - cd[k - 1][A.C]
        s = 1 if r > 0 else (-1 if r < 0 else 0)
        num += s * cd[k][A.V]
        den += cd[k][A.V]
    return num / den if den else 0.0


def mom_score(cd, i, L):
    return A.pct(cd[i - L][A.C], cd[i][A.C])


def run_book(coins, cds, n, score_fn, L, topn, H):
    """returns list of per-rebalance spread returns {t, ret}."""
    trades = []
    i = L + 1
    while i < n - H - 2:
        scored = []
        for c in coins:
            cd = cds[c]
            scored.append((score_fn(cd, i, L), c))
        scored.sort()
        shorts = [c for _, c in scored[:topn]]
        longs = [c for _, c in scored[-topn:]]
        def fwd(c):
            cd = cds[c]
            return A.pct(cd[i + 1][A.O], cd[i + 1 + H][A.C])
        lr = statistics.mean(fwd(c) for c in longs)
        sr = statistics.mean(fwd(c) for c in shorts)
        trades.append({"t": cds[coins[0]][i + 1][A.T], "ret": lr - sr})
        i += H
    return trades


def show(name, trades):
    s = A.summarize(trades)
    print(f"{name:<26} n={s['n']:<4} ev12={s['slip12']['mean_ret_pct']:<7} "
          f"ev25={s['slip25']['mean_ret_pct']:<7} ev50={s['slip50']['mean_ret_pct']:<7} "
          f"h1={s['oos_12bps']['first_half_mean_pct']} h2={s['oos_12bps']['second_half_mean_pct']}")
    return s


def run():
    d = A.load_dataset()
    coins, cds, n = build_panel(d)
    print(f"panel: {len(coins)} coins, {n} aligned daily bars\n")
    for L in (10, 20):
        for topn in (6, 8):
            for H in (3, 5):
                obv = run_book(coins, cds, n, obv_score, L, topn, H)
                mom = run_book(coins, cds, n, mom_score, L, topn, H)
                print(f"--- L={L} topn={topn} H={H} ---")
                show("OBV-flow long/short", obv)
                show("PRICE-mom long/short", mom)
                # residual: OBV minus mom spread per rebalance (does flow add?)
                diff = [{"t": a["t"], "ret": a["ret"] - b["ret"]} for a, b in zip(obv, mom)]
                show("OBV minus MOM (added)", diff)
                print()


if __name__ == "__main__":
    run()
