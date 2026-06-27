"""W-B7 turbulence_upsize_spec — pin B15: is the high-vol-state XS-book EV concentration
real SHARPE lift or just vol-scaling restated? Diagnostic: book Sharpe (not EV) in
turbulent vs calm. Then turbulence-upsize multiplier vs flat vs inverse-vol sizing.
If only vol-scaling -> REFUTE the 'turbulence alpha' framing."""
import statistics
import laneB2_common as B
px = B.px; N = B.N
K, HOLD, M = 14, 7, 6
book = B.xs_book(K, HOLD, M)
for x in book:
    x["bv"] = px.vol("BTC", x["i"], 10)
bvs = sorted(x["bv"] for x in book if x["bv"] is not None)
thr = bvs[int(0.66 * len(bvs))]  # top-tercile BTC vol = turbulent

turb = [x for x in book if x["bv"] is not None and x["bv"] >= thr]
calm = [x for x in book if x["bv"] is not None and x["bv"] < thr]

def stats(grp):
    r = [x["ret"] for x in grp]
    return (statistics.mean(r), statistics.pstdev(r), B.sharpe(r), len(r))

print("=" * 100)
print("W-B7 TURBULENCE UPSIZE — EV concentration vs SHARPE concentration (BTC 10d vol, top tercile=turb)")
print("=" * 100)
mt, st, sht, nt = stats(turb); mc, sc, shc, nc = stats(calm)
print(f"  TURBULENT: n={nt} mean={100*mt:+.3f}% vol={100*st:.3f}% per-rebal-Sharpe={sht:+.3f}")
print(f"  CALM:      n={nc} mean={100*mc:+.3f}% vol={100*sc:.3f}% per-rebal-Sharpe={shc:+.3f}")
print(f"  -> EV ratio turb/calm = {mt/mc if mc else float('nan'):.2f}x | "
      f"VOL ratio = {st/sc if sc else float('nan'):.2f}x | SHARPE ratio = {sht/shc if shc else float('nan'):.2f}x")
print("  If EV ratio >> SHARPE ratio, the concentration is VOL, not alpha.")

# overlays
def overlay(kind, up=2.0):
    out = []
    # inverse-vol uses trailing book-return vol (causal), 6-rebal window
    for idx, x in enumerate(book):
        if kind == "flat":
            w = 1.0
        elif kind == "turb":
            w = up if (x["bv"] is not None and x["bv"] >= thr) else 1.0
        elif kind == "invvol":
            if idx < 6: continue
            past = [book[j]["ret"] for j in range(idx-6, idx)]
            rv = statistics.pstdev(past) + 1e-9
            tgt = statistics.pstdev([y["ret"] for y in book])
            w = min(tgt / rv, 3.0)
        out.append({**x, "ret": w * x["ret"]})
    return out

print("\nSizing overlays (annSharpe is the verdict metric):")
res = {}
for kind in ("flat", "turb", "invvol"):
    o = B.report(kind, overlay(kind), HOLD); res[kind] = o; B.pr(o)
flat = res["flat"]["ann_sharpe"]
print(f"\nLIFT vs flat (annSharpe): turb-upsize={res['turb']['ann_sharpe']-flat:+.3f}  "
      f"inverse-vol={res['invvol']['ann_sharpe']-flat:+.3f}")
print(f"turb-upsize maxDD {res['turb']['maxdd_pct']}% vs flat {flat and res['flat']['maxdd_pct']}%")
