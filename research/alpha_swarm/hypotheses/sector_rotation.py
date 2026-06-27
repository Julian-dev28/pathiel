"""C14 sector_rotation — hand-tagged sectors: sector-momentum + intra-sector relative value.

Daily. (1) SECTOR MOMENTUM: rank sectors by avg trailing-L return, long top-sector basket /
short bottom-sector basket. (2) INTRA-SECTOR momentum & RV: within each sector long top-half /
short bottom-half by trailing-L return (momentum) and the reverse (RV). Non-overlapping
rebalances, lookahead-safe (score@i, enter i+1 open, exit i+1+H close). Control: an
all-universe XS-momentum book (same L/H) — sector structure must ADD over it.
"""
from __future__ import annotations
import statistics
import alpha_lib as A

SECTORS = {
    "L1": ["BTC", "ETH", "SOL", "SUI", "NEAR", "AVAX", "ADA", "BNB", "LTC", "XMR", "ZEC",
           "HYPE", "XPL", "IP", "MON", "XRP"],
    "MEME": ["DOGE", "kPEPE", "FARTCOIN", "WIF", "PUMP", "TRUMP", "VVV"],
    "DEFI": ["AAVE", "UNI", "JUP", "ENA", "MORPHO", "DYDX", "LIT", "INJ", "ONDO"],
    "AI": ["TAO", "FET", "WLD", "KAITO"],
    "INFRA": ["LINK", "JTO", "ZRO"],
}


def build_panel(d):
    coins = [c for c in d["coins"] if len(A.candles(d, c, "1d")) >= 60]
    cds = {c: A.candles(d, c, "1d") for c in coins}
    n = min(len(cds[c]) for c in coins)
    sec = {s: [c for c in cl if c in cds] for s, cl in SECTORS.items()}
    return coins, cds, n, sec


def ret_L(cd, i, L):
    return A.pct(cd[i - L][A.C], cd[i][A.C])


def fwd(cd, i, H):
    return A.pct(cd[i + 1][A.O], cd[i + 1 + H][A.C])


def sector_momentum(coins, cds, n, sec, L, H):
    trades = []
    i = L + 1
    while i < n - H - 2:
        srank = []
        for s, cl in sec.items():
            if len(cl) < 2:
                continue
            srank.append((statistics.mean(ret_L(cds[c], i, L) for c in cl), s))
        srank.sort()
        worst, best = srank[0][1], srank[-1][1]
        lr = statistics.mean(fwd(cds[c], i, H) for c in sec[best])
        sr = statistics.mean(fwd(cds[c], i, H) for c in sec[worst])
        trades.append({"t": cds[coins[0]][i + 1][A.T], "ret": lr - sr})
        i += H
    return trades


def intra_sector(coins, cds, n, sec, L, H, mode):
    """mode 'mom': long top-half / short bottom-half within each sector; 'rv': reverse."""
    trades = []
    i = L + 1
    while i < n - H - 2:
        longs, shorts = [], []
        for s, cl in sec.items():
            if len(cl) < 4:
                continue
            r = sorted(((ret_L(cds[c], i, L), c) for c in cl))
            half = len(r) // 2
            bot = [c for _, c in r[:half]]
            top = [c for _, c in r[-half:]]
            if mode == "mom":
                longs += top; shorts += bot
            else:
                longs += bot; shorts += top
        if not longs or not shorts:
            i += H; continue
        lr = statistics.mean(fwd(cds[c], i, H) for c in longs)
        sr = statistics.mean(fwd(cds[c], i, H) for c in shorts)
        trades.append({"t": cds[coins[0]][i + 1][A.T], "ret": lr - sr})
        i += H
    return trades


def all_universe_mom(coins, cds, n, L, H, topn=8):
    trades = []
    i = L + 1
    while i < n - H - 2:
        r = sorted(((ret_L(cds[c], i, L), c) for c in coins))
        shorts = [c for _, c in r[:topn]]
        longs = [c for _, c in r[-topn:]]
        lr = statistics.mean(fwd(cds[c], i, H) for c in longs)
        sr = statistics.mean(fwd(cds[c], i, H) for c in shorts)
        trades.append({"t": cds[coins[0]][i + 1][A.T], "ret": lr - sr})
        i += H
    return trades


def show(name, trades):
    s = A.summarize(trades)
    print(f"{name:<28} n={s['n']:<4} ev12={s['slip12']['mean_ret_pct']:<7} "
          f"ev25={s['slip25']['mean_ret_pct']:<7} h1={s['oos_12bps']['first_half_mean_pct']} "
          f"h2={s['oos_12bps']['second_half_mean_pct']}")


def run():
    d = A.load_dataset()
    coins, cds, n, sec = build_panel(d)
    print("sector sizes:", {s: len(cl) for s, cl in sec.items()}, "| aligned bars", n, "\n")
    for L in (10, 20):
        for H in (3, 5):
            print(f"--- L={L} H={H} ---")
            show("SECTOR-momentum (best-worst)", sector_momentum(coins, cds, n, sec, L, H))
            show("INTRA-sector momentum", intra_sector(coins, cds, n, sec, L, H, "mom"))
            show("INTRA-sector RV (reverse)", intra_sector(coins, cds, n, sec, L, H, "rv"))
            show("ALL-universe momentum (ref)", all_universe_mom(coins, cds, n, L, H))
            print()


if __name__ == "__main__":
    run()
