"""W-B2 skew_arm_forward_spec — pin B13: the precise neg-market-skew threshold +
lookback W that maximizes the within-universe regime split (neg vs pos EV) on the
extreme_fade-long base, and whether it is robust to the skew window. -> shadow spec."""
import statistics
import laneB2_common as B
import alpha_lib, mc_null
px = B.px; coins = B.coins; N = B.N

FADE_THR = -0.12   # coin 1d ret < -12%
STOP = 0.20        # 20% stop
HOR = 3            # 3d

def fade_trades():
    """all extreme_fade-long entries, lookahead-safe (decide at i close, fill i+1 open)."""
    out = []
    for c in coins:
        for i in range(2, N - HOR - 2):
            r = px.dret(c, i)
            if r is None or r >= FADE_THR: continue
            eo = px.open(c, i + 1)
            if not eo: continue
            fwd = [px.bar(c, j) for j in range(i + 1, i + 1 + HOR + 1)]
            fwd = [b for b in fwd if b]
            if len(fwd) < 1: continue
            res = alpha_lib.sweep_stop(eo, "long", fwd, [STOP], HOR)
            out.append({"t": px.timeline[i + 1], "ret": res[STOP], "i": i})
    return out

# precompute skew at every bar index for several windows
def skew_at(i, W):
    rs = [B.market_ret(j) for j in range(i - W + 1, i + 1)]
    rs = [r for r in rs if r is not None]
    if len(rs) < max(5, W // 2): return None
    m = statistics.mean(rs); sd = statistics.pstdev(rs)
    if sd == 0: return 0.0
    return statistics.mean([(r - m) ** 3 for r in rs]) / (sd ** 3)

trades = fade_trades()
print("=" * 110)
print(f"W-B2 skew-arm spec on extreme_fade-long (ret<{FADE_THR}, {int(STOP*100)}% stop, {HOR}d). n_all={len(trades)}")
print("=" * 110)
all_s = alpha_lib.summarize(trades)
print(f"BASE all: EV12={all_s['slip12']['mean_ret_pct']:+.3f}% win={all_s['slip12']['win_rate']:.3f} "
      f"OOS {all_s['oos_12bps']['first_half_mean_pct']}/{all_s['oos_12bps']['second_half_mean_pct']}")

pool = [t["ret"] for t in trades]  # random-entry pool = any fade event (the base)

print(f"\n{'W':>3} {'thr':>6} | {'neg n':>6} {'neg EV12':>9} {'neg win':>8} {'neg OOS h1/h2':>16} | "
      f"{'pos n':>6} {'pos EV12':>9} | split | p(neg>pool)")
best = None
for W in (10, 15, 20, 30):
    # threshold options: hard 0, and the W-specific median (within-universe split)
    sk = {t["i"]: skew_at(t["i"], W) for t in trades}
    vals = sorted(v for v in sk.values() if v is not None)
    med = vals[len(vals)//2] if vals else 0.0
    for label, thr in (("0.0", 0.0), ("med", med)):
        neg = [t for t in trades if sk[t["i"]] is not None and sk[t["i"]] < thr]
        pos = [t for t in trades if sk[t["i"]] is not None and sk[t["i"]] >= thr]
        if len(neg) < 15 or len(pos) < 15: continue
        ns = alpha_lib.summarize(neg); ps = alpha_lib.summarize(pos)
        nev = ns['slip12']['mean_ret_pct']; pev = ps['slip12']['mean_ret_pct']
        noos = ns['oos_12bps']
        split = nev - pev
        mc = mc_null.shuffle_label_p([t["ret"] for t in neg], pool, n_iter=4000, seed=1)
        h1, h2 = noos['first_half_mean_pct'], noos['second_half_mean_pct']
        robust = (h1 is not None and h2 is not None and h1 > 0 and h2 > 0)
        print(f"{W:>3} {label:>6} | {len(neg):>6} {nev:>+9.3f} {ns['slip12']['win_rate']:>8.3f} "
              f"{str(h1)+'/'+str(h2):>16} | {len(pos):>6} {pev:>+9.3f} | {split:>+5.2f} | p={mc['p_one_sided']}")
        if robust and split > 0:
            score = split + (h2 or 0)
            if best is None or score > best[0]:
                best = (score, W, label, thr, nev, pev, split, h1, h2, mc['p_one_sided'])

print("\n" + "-" * 110)
if best:
    _, W, lab, thr, nev, pev, split, h1, h2, p = best
    print(f"BEST SPEC: W={W} threshold={lab}({thr:+.3f}) -> neg EV12 {nev:+.3f}% vs pos {pev:+.3f}% "
          f"(split {split:+.2f}), OOS h1/h2 {h1}/{h2}, MC p={p}")
else:
    print("No robust positive split found.")
