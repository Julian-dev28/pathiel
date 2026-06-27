"""C15 sympathy_followthrough — sector leader big move -> do laggards follow next bar?

1h. Leader per sector = highest dayNtlVlm coin. Event: leader 1h return |r|>G at bar t.
Treatment = sector laggards entered in the leader's direction at t+1 open, held H.
KEY CONTROL: same-side, same-time OUT-OF-SECTOR coins (isolates SECTOR sympathy from the
market-wide beta move that drives most big leader bars). Report paired (laggard - outsector)
per event, OOS halves + slippage. Also raw laggard EV vs random same-side (mc_null).
"""
from __future__ import annotations
import statistics
import alpha_lib as A
import mc_null

SECTORS = {
    "L1": ["BTC", "ETH", "SOL", "SUI", "NEAR", "AVAX", "ADA", "BNB", "LTC", "XMR", "ZEC",
           "HYPE", "XPL", "IP", "MON", "XRP"],
    "MEME": ["DOGE", "kPEPE", "FARTCOIN", "WIF", "PUMP", "TRUMP", "VVV"],
    "DEFI": ["AAVE", "UNI", "JUP", "ENA", "MORPHO", "DYDX", "LIT", "INJ", "ONDO"],
    "AI": ["TAO", "FET", "WLD", "KAITO"],
    "INFRA": ["LINK", "JTO", "ZRO"],
}


def leaders(d):
    out = {}
    for s, cl in SECTORS.items():
        best, bestv = None, -1
        for c in cl:
            v = d.get("universe", {}).get(c, {}).get("dayNtlVlm", 0) or 0
            if v > bestv and A.candles(d, c, "1h"):
                best, bestv = c, v
        out[s] = best
    return out


def run():
    d = A.load_dataset()
    lead = leaders(d)
    print("leaders:", lead)
    # align 1h by timestamp
    series = {c: A.candles(d, c, "1h") for c in d["coins"] if len(A.candles(d, c, "1h")) >= 200}
    idx = {c: {bar[A.T]: k for k, bar in enumerate(cd)} for c, cd in series.items()}
    common = set.intersection(*[set(m.keys()) for m in idx.values()])
    times = sorted(common)
    sec_of = {c: s for s, cl in SECTORS.items() for c in cl}

    for G in (0.01, 0.02, 0.03):
        for H in (1, 3, 6):
            paired = []   # laggard - outsector, per event
            lag_raw = []  # raw laggard signed returns (for mc_null)
            for s, cl in SECTORS.items():
                L = lead[s]
                if L not in series:
                    continue
                lcd, lidx = series[L], idx[L]
                laggards = [c for c in cl if c != L and c in series]
                outs = [c for c in series if sec_of.get(c) != s]
                for ti in range(1, len(times) - H - 2):
                    t = times[ti]
                    k = lidx[t]
                    if k < 1 or k + 1 + H >= len(lcd):
                        continue
                    r = A.pct(lcd[k - 1][A.C], lcd[k][A.C])
                    if abs(r) < G:
                        continue
                    side = "long" if r > 0 else "short"
                    sign = 1 if side == "long" else -1
                    def fwd(c):
                        cd, m = series[c], idx[c]
                        kk = m.get(t)
                        if kk is None or kk + 1 + H >= len(cd):
                            return None
                        return sign * A.pct(cd[kk + 1][A.O], cd[kk + 1 + H][A.C])
                    lag = [fwd(c) for c in laggards]
                    lag = [x for x in lag if x is not None]
                    osr = [fwd(c) for c in outs]
                    osr = [x for x in osr if x is not None]
                    if not lag or not osr:
                        continue
                    paired.append({"t": t, "ret": statistics.mean(lag) - statistics.mean(osr)})
                    lag_raw.extend({"t": t, "ret": x} for x in lag)
            if len(paired) < 30:
                continue
            sp = A.summarize(paired)
            print(f"\nG={G} H={H}  PAIRED(laggard - outsector) n={sp['n']} "
                  f"ev12={sp['slip12']['mean_ret_pct']} ev25={sp['slip25']['mean_ret_pct']} "
                  f"h1={sp['oos_12bps']['first_half_mean_pct']} h2={sp['oos_12bps']['second_half_mean_pct']}")
            sr = A.summarize(lag_raw)
            print(f"           RAW laggard signed   n={sr['n']} ev12={sr['slip12']['mean_ret_pct']} "
                  f"h1={sr['oos_12bps']['first_half_mean_pct']} h2={sr['oos_12bps']['second_half_mean_pct']}")


if __name__ == "__main__":
    run()
