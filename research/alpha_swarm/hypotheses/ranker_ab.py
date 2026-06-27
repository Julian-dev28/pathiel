"""ranker_ab — decision-grade head-to-head: pct_k vs z_ext vs raw-residual.

Builds a market-neutral L/S DAILY-return series for each ranker at MATCHED params,
using the EXACT live scorers imported from hermes_trader.agents.xs_momentum. The three
rankers score the SAME eligible universe each rebal day, so survivorship cancels in the
PAIRED (residual - pct_k) difference series — that paired diff is the headline.

Read-only. Cache-only (dataset.json). No live code touched, no network, no pytest.
"""
from __future__ import annotations
import sys, statistics, math
from pathlib import Path

SCRATCH = "/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad"
REPO = "/Users/julian_dev/Documents/code/hermes-trader"
sys.path.insert(0, SCRATCH)
sys.path.insert(0, REPO)

import alpha_lib as al
import mc_null
from hermes_trader.agents.xs_momentum import rank_universe  # EXACT live ranker

d = al.load_dataset()
COINS = [c for c in d["coins"] if al.candles(d, c, "1d")]
BENCH = "BTC"
assert BENCH in COINS

# ---- Build the daily panel on BTC's timestamp axis ----------------------------------
btc_bars = al.candles(d, BENCH, "1d")
master_t = [b[al.T] for b in btc_bars]              # 301 daily timestamps
tindex = {t: i for i, t in enumerate(master_t)}

# coin -> {t: bar}; bars converted to DICTS because the live candle_val() reads dict keys
# / object attrs, NOT list indices (raw dataset bars are [t,o,h,l,c,v] lists).
def _todict(b):
    return {"t": b[al.T], "o": b[al.O], "h": b[al.H], "l": b[al.L], "c": b[al.C], "v": b[al.V]}
bybar = {}
for c in COINS:
    bybar[c] = {b[al.T]: _todict(b) for b in al.candles(d, c, "1d")}

def bars_upto(coin, i):
    """List of the coin's bars for master dates [0..i], in order, skipping missing dates."""
    out = []
    for j in range(i + 1):
        b = bybar[coin].get(master_t[j])
        if b is not None:
            out.append(b)
    return out

def daily_ret(coin, i):
    """close[i]/close[i-1]-1 on master axis; None if either bar missing."""
    if i <= 0:
        return None
    b0 = bybar[coin].get(master_t[i - 1]); b1 = bybar[coin].get(master_t[i])
    if not b0 or not b1 or b0["c"] <= 0:
        return None
    return b1["c"] / b0["c"] - 1.0

N = len(master_t)

# ---- Backtest one ranker -> continuous daily book-return series ----------------------
RANKERS = ["pct_k", "z_ext", "raw"]          # "raw"+bench = residual
ZWIN, BWIN = 14, 30                          # live zext_window / beta_window

def build_book(i, lb, k, ranking):
    """Decide the book on bars up to and including master date i (i-close approximation,
    permitted for slow daily signals). Universe MATCHED across rankers: a coin is eligible
    only if it has enough history for ALL three scorers (max requirement) at date i."""
    req = max(ZWIN, lb + 1, BWIN + 1)
    cbc = {}
    for c in COINS:
        if c == BENCH:
            continue                          # don't trade the benchmark itself
        bars = bars_upto(c, i)
        if len(bars) >= req:
            cbc[c] = bars
    if len(cbc) < 2 * k:
        return None
    bench_bars = bars_upto(BENCH, i)
    book = rank_universe(cbc, lb, k, bench_bars=bench_bars, beta_window=BWIN,
                         ranking=ranking, zext_window=ZWIN)
    if not book.longs or not book.shorts:
        return None
    return book

def turnover(prev, cur):
    """Fraction of the 2k legs that changed (avg of long+short symmetric difference)."""
    if prev is None:
        return 1.0
    pl, cl = set(prev.longs), set(cur.longs)
    ps, cs = set(prev.shorts), set(cur.shorts)
    t_long = len(cl - pl) / max(1, len(cl))
    t_short = len(cs - ps) / max(1, len(cs))
    return 0.5 * (t_long + t_short)

def run(lb, k, hold, ranking, slip_bps=0.0):
    """Return dict with daily net book-return series + the BTC daily series (aligned),
    plus per-rebal spread list. Book decided at rebal index r (close r), effective from
    day r+1; held `hold` days. Day d return uses the book whose decision index < d."""
    cost = slip_bps / 10000.0
    daily = []          # (day_index_i, book_net_ret)
    btc_daily = []
    cur_book = None
    prev_book = None
    last_rebal = -10**9
    rebal_spread = []   # per-rebal mean daily spread over its hold window (for EV table)
    # We iterate days; rebal happens at indices r = first_valid, first_valid+hold, ...
    first_valid = max(ZWIN, lb + 1, BWIN + 1)
    for i in range(first_valid, N):
        # Decide whether to rebalance using close of day i-1 (so book is active for day i).
        # Rebal cadence keyed off (i-1).
        decide_idx = i - 1
        if decide_idx - last_rebal >= hold:
            nb = build_book(decide_idx, lb, k, ranking)
            if nb is not None:
                prev_book = cur_book
                cur_book = nb
                last_rebal = decide_idx
        if cur_book is None:
            continue
        # day i book return
        lrs = [daily_ret(c, i) for c in cur_book.longs]
        srs = [daily_ret(c, i) for c in cur_book.shorts]
        lrs = [x for x in lrs if x is not None]
        srs = [x for x in srs if x is not None]
        if not lrs or not srs:
            continue
        Lt = statistics.mean(lrs)
        St = statistics.mean(srs)
        bret = 0.5 * (Lt - St)        # dollar-neutral, gross = 1
        # charge cost on the day the book just changed (i == last_rebal+1)
        if i - 1 == last_rebal and prev_book is not None:
            bret -= cost * turnover(prev_book, cur_book)
        elif i - 1 == last_rebal and prev_book is None:
            bret -= cost * 1.0
        daily.append((i, bret))
        br = daily_ret(BENCH, i)
        btc_daily.append(br if br is not None else 0.0)
    return {"daily": daily, "btc": btc_daily}

# ---- stats helpers ------------------------------------------------------------------
def sharpe(xs):
    if len(xs) < 3:
        return 0.0
    sd = statistics.pstdev(xs)
    return statistics.mean(xs) / sd if sd > 0 else 0.0

def ann_sharpe(xs):
    return sharpe(xs) * math.sqrt(365)

def tstat(xs):
    if len(xs) < 3:
        return 0.0
    sd = statistics.stdev(xs)
    return statistics.mean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0

def ols_beta(y, x):
    n = min(len(y), len(x))
    if n < 5:
        return 0.0
    y, x = y[-n:], x[-n:]
    mx, my = statistics.mean(x), statistics.mean(y)
    vx = sum((a - mx) ** 2 for a in x)
    if vx <= 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / vx

def down_beta(y, x):
    """beta on days where BTC (x) < 0."""
    pairs = [(b, a) for a, b in zip(x, y) if a < 0]
    if len(pairs) < 5:
        return 0.0
    yy = [p[0] for p in pairs]; xx = [p[1] for p in pairs]
    return ols_beta(yy, xx)

def halves(series):
    mid = len(series) // 2
    return series[:mid], series[mid:]

# ==== DRIVER =========================================================================
if __name__ == "__main__":
    LBS = [7, 10, 14, 20]      # 7 = live lb; 10/14/20 = swept
    KS = [4, 6, 8]
    HOLDS = [5, 7, 10]         # 10 = live hold
    LIVE = (7, 8, 10)          # live lb/k/hold

    print(f"N master daily bars = {N}; coins(ex-BTC) = {len(COINS)-1}; "
          f"common span first eligible idx depends on params\n")

    # ---- 1) Headline sweep: per-ranker ann.Sharpe (net 12bps) + paired diff vs pct_k --
    print("=== SWEEP: ann.Sharpe net@12bps | mean daily bps net@12 | n_days ===")
    hdr = f"{'lb':>3} {'k':>2} {'H':>3} | " + " | ".join(f"{r:>22}" for r in RANKERS)
    print(hdr)
    sweep_rows = []
    for lb in LBS:
        for k in KS:
            for hold in HOLDS:
                cells = []
                series_by_r = {}
                for r in RANKERS:
                    res = run(lb, k, hold, r, slip_bps=12.0)
                    xs = [b for _, b in res["daily"]]
                    series_by_r[r] = res
                    cells.append(f"Sh{ann_sharpe(xs):+5.2f} {1e4*statistics.mean(xs) if xs else 0:+6.1f}bp n{len(xs):>3}")
                row = f"{lb:>3} {k:>2} {hold:>3} | " + " | ".join(f"{c:>22}" for c in cells)
                print(row)
                sweep_rows.append((lb, k, hold, series_by_r))

    # ---- 2) PAIRED DIFFERENCE (residual - pct_k) headline at LIVE + key configs -------
    print("\n=== PAIRED DIFF (residual minus pct_k), aligned daily, net@12bps ===")
    print(f"{'lb':>3} {'k':>2} {'H':>3} | {'mean_bp':>8} {'t':>6} {'mc_p':>7} {'n':>4} | "
          f"{'pctk_Sh':>7} {'resid_Sh':>8} {'zext_Sh':>7}")
    paired_records = []
    focus = [(7,8,10),(10,8,10),(14,8,10),(14,8,7),(14,8,5),(20,8,10),(14,6,10),(14,4,10)]
    for (lb, k, hold) in focus:
        r_pctk = run(lb, k, hold, "pct_k", slip_bps=12.0)
        r_resid = run(lb, k, hold, "raw", slip_bps=12.0)
        r_zext = run(lb, k, hold, "z_ext", slip_bps=12.0)
        # align by day index
        pk = dict(r_pctk["daily"]); rs = dict(r_resid["daily"]); zx = dict(r_zext["daily"])
        common = sorted(set(pk) & set(rs))
        diff = [rs[i] - pk[i] for i in common]
        pk_xs = [pk[i] for i in common]; rs_xs = [rs[i] for i in common]
        zx_common = sorted(set(zx) & set(pk))
        zx_xs = [zx[i] for i in zx_common]
        if len(diff) < 5:
            continue
        mean_bp = 1e4 * statistics.mean(diff)
        t = tstat(diff)
        # MC block-bootstrap p on the DIFFERENCE series: null = mean of random contiguous
        # blocks of the diff series itself (autocorr-preserving) >= observed mean.
        mc = mc_null.block_bootstrap_p(diff, k=len(diff), observed_mean=statistics.mean(diff),
                                       block_len=5, n_iter=10000, seed=0)
        tag = " *LIVE*" if (lb,k,hold)==LIVE else ""
        print(f"{lb:>3} {k:>2} {hold:>3} | {mean_bp:+8.2f} {t:+6.2f} {mc['p_one_sided']:>7.4f} "
              f"{len(diff):>4} | {ann_sharpe(pk_xs):+7.2f} {ann_sharpe(rs_xs):+8.2f} "
              f"{ann_sharpe(zx_xs):+7.2f}{tag}")
        paired_records.append((lb,k,hold,mean_bp,t,mc['p_one_sided'],len(diff),
                               ann_sharpe(pk_xs),ann_sharpe(rs_xs),ann_sharpe(zx_xs)))

    # ---- 3) BETA table + OOS halves at LIVE config (lb7 k8 H10) and lb14 k8 H10 -------
    print("\n=== BETA + OOS halves (net@12bps) ===")
    for (lb,k,hold) in [(7,8,10),(14,8,10)]:
        print(f"\n-- config lb={lb} k={k} H={hold} --")
        print(f"{'ranker':>10} | {'full_Sh':>7} {'h1_Sh':>6} {'h2_Sh':>6} | "
              f"{'mean_bp':>8} {'meanbp25':>9} | {'beta_full':>9} {'beta_down':>9}")
        for r in RANKERS:
            res12 = run(lb, k, hold, r, slip_bps=12.0)
            res25 = run(lb, k, hold, r, slip_bps=25.0)
            xs = [b for _,b in res12["daily"]]
            xs25 = [b for _,b in res25["daily"]]
            btc = res12["btc"]
            h1, h2 = halves(xs)
            label = "residual" if r=="raw" else r
            print(f"{label:>10} | {ann_sharpe(xs):+7.2f} {ann_sharpe(h1):+6.2f} {ann_sharpe(h2):+6.2f} | "
                  f"{1e4*statistics.mean(xs):+8.2f} {1e4*statistics.mean(xs25):+9.2f} | "
                  f"{ols_beta(xs,btc):+9.3f} {down_beta(xs,btc):+9.3f}")
