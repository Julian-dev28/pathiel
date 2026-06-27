"""W-B1 survivor_stack — combine Wave-1 survivors into ONE overlay on the live
XS-momentum book: skew-regime arm (B13) + turbulence-upsize (B15 HMM proxy) +
ADX>25 gate (B6). Does the STACK beat the best single overlay and the un-overlaid
book on OOS Sharpe? Watch overfitting (3 gates, one sample)."""
import statistics
import laneB2_common as B

K, HOLD, M = 14, 7, 6
base = B.xs_book(K, HOLD, M)                       # un-overlaid
base_adx = B.xs_book(K, HOLD, M, adx_min=25)       # ADX-gated legs (B6)

# regime context computed at decision bar i for each rebal
def neg_skew(x):  # B13 arm: negative aggregate skew regime
    return x["skew"] is not None and x["skew"] < 0
# turbulence: causal high-vol BTC state proxy for B15 (top-tercile trailing vol)
vols = sorted(x["btcvol"] for x in base if x["btcvol"] is not None)
turb_thr = vols[int(0.66 * len(vols))] if vols else None
def turbulent(x):
    return x["btcvol"] is not None and turb_thr is not None and x["btcvol"] >= turb_thr

def overlay(series, *, use_skew=False, use_turb=False, up=2.0):
    """Multiplicative sizing overlay. skew arm: full size in neg-skew else 0.5.
    turb upsize: x`up` in turbulent state else 1x. Returns sized series."""
    out = []
    for x in series:
        w = 1.0
        if use_skew:
            w *= 1.0 if neg_skew(x) else 0.5
        if use_turb:
            w *= up if turbulent(x) else 1.0
        out.append({**x, "ret": w * x["ret"]})
    return out

print("=" * 110)
print("W-B1 SURVIVOR STACK — overlays on XS-momentum book (k=14,H=7,m=6). LIFT = annSharpe vs un-overlaid")
print("=" * 110)
variants = {
    "un-overlaid (base)": base,
    "ADX>25 legs only":   base_adx,
    "skew-arm only":      overlay(base, use_skew=True),
    "turb-upsize only":   overlay(base, use_turb=True),
    "skew+turb":          overlay(base, use_skew=True, use_turb=True),
    "ADX+skew":           overlay(base_adx, use_skew=True),
    "ADX+turb":           overlay(base_adx, use_turb=True),
    "STACK (ADX+skew+turb)": overlay(base_adx, use_skew=True, use_turb=True),
}
reps = {}
for name, s in variants.items():
    o = B.report(name, s, HOLD); reps[name] = o; B.pr(o)

base_sh = reps["un-overlaid (base)"]["ann_sharpe"]
print(f"\nbase annSharpe = {base_sh:+.3f}")
print("LIFT vs base (annSharpe):")
best_single, best_single_v = None, -9
for name in ("ADX>25 legs only", "skew-arm only", "turb-upsize only"):
    lift = reps[name]["ann_sharpe"] - base_sh
    print(f"   {name:<22} lift={lift:+.3f}")
    if reps[name]["ann_sharpe"] > best_single_v:
        best_single_v, best_single = reps[name]["ann_sharpe"], name
stack = reps["STACK (ADX+skew+turb)"]
print(f"\nbest single overlay = {best_single} (annSh {best_single_v:+.3f})")
print(f"STACK annSh {stack['ann_sharpe']:+.3f}  lift-vs-base {stack['ann_sharpe']-base_sh:+.3f}  "
      f"lift-vs-best-single {stack['ann_sharpe']-best_single_v:+.3f}")
print(f"STACK OOS both halves: h1_sh={stack['h1_sh']} h2_sh={stack['h2_sh']} (both>0 required)")
print(f"STACK maxDD {stack['maxdd_pct']}% vs base {reps['un-overlaid (base)']['maxdd_pct']}%")
