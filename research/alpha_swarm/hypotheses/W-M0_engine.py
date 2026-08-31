"""W-M0 — Lane M shared engine (mover capture, operator mandate 2026-07-11).

PRE-REGISTRATION (fixed before any result was looked at):

Data: the Lane-H extended 1h cache (hourly_ext.json, 40 liquid perps,
2025-12-13 .. 2026-07-09, ~5000 bars/coin, validated vs dataset.json).
Dollar volume per bar = v * close (v is base units).

Common rules, all hypotheses:
  * Decide on bar i close, FILL at bar i+1 open. Never peek.
  * Intra-bar pessimism: LOW before HIGH. Stops/floors are checked against the
    bar LOW before the bar HIGH may raise a trail peak; a trail arms/raises
    effective NEXT bar. Gap-through-stop exits at the bar OPEN.
  * Costs: report EV at 0/6/12/25/50 bps round-trip. VERDICT tier = 25 bps.
  * OOS: split each cell's trades by TIME at the median entry; both halves
    must be EV25 > 0.
  * MC null: same-coin random-time null. Pool = the SAME exit policy applied
    at strided random bars of the SAME coin meeting the SAME liquidity floor
    (and, for regime cells, the same BTC-20d regime). >= 2000 iters, escalated
    to 100k for any cell with p < 0.005 (Bonferroni needs the resolution).
    p = P(null mean >= observed mean), gross returns (costs cancel).
  * Regime: BTC trailing 480h (20d) close-to-close return sign at the signal
    bar. Views: all / btc_up / btc_dn. First 480h have no regime.
  * n >= 30 per cell, no exceptions.
  * Bonferroni: alpha = 0.05 / (# cells in the hypothesis' full grid,
    regime views included). W-M1: 8 bands x 2 floors x 13 exits x 3 views
    = 624 -> 8.01e-05. W-M3: 2 floors x 13 exits x 3 views = 78 -> 6.41e-04.
    W-M2: 7 exits x 3 views = 21 -> 2.38e-03.
  * Non-overlap: per coin x signal-family, signals < 24h apart are dropped
    (first wins). 48h holds can still overlap one follow-on signal; noted.
  * Survivorship: universe is TODAY's liquid set -> any +EV is an UPPER BOUND.

Exit grid (13 policies): hold {6,12,24,48}h x hard stop {5,8,15}%  (12)
  + KAITO tight-floor trail: arm at +2% peak gain, floor = entry*(1+0.90*peak
    gain) (retrace 0.10), disaster stop 15%, max hold 48h  (1).
"""
from __future__ import annotations

import bisect
import json
import statistics
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
SWARM = REPO / "research" / "alpha_swarm"
sys.path.insert(0, str(SWARM / "lib"))

# Lane-H cache (read-only, produced+validated by W-H0_fetch.py on 2026-07-09)
HOURLY_CACHE = Path(
    "/private/tmp/claude-501/-Users-julian-dev-Documents-code-pathia/"
    "4b037816-5b27-4d2d-a13e-a6ebd68a2340/scratchpad/hourly_ext.json")
# Lane-M scratchpad (results JSONs live here, findings in research/alpha_swarm)
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-julian-dev-Documents-code-pathia/"
    "f77b77de-96c2-4bf2-a574-1fd5aeebb7f2/scratchpad")

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
HOUR_MS = 3_600_000

HOLDS = [6, 12, 24, 48]
STOPS = [0.05, 0.08, 0.15]
TRAIL = ("trail", None)          # KAITO floor: arm .02, gb .10, hard 15%, max 48h
POLICIES = [(h, s) for h in HOLDS for s in STOPS] + [TRAIL]
TRAIL_ARM, TRAIL_GB, TRAIL_HARD, TRAIL_MAX = 0.02, 0.10, 0.15, 48
SLIPS = [0.0, 0.0006, 0.0012, 0.0025, 0.0050]
VERDICT_SLIP = 0.0025
POOL_STRIDE = 4
MC_BASE, MC_ESC = 2000, 100_000


def pol_name(p) -> str:
    return "trail(.02/.10)" if p[0] == "trail" else f"h{p[0]}_s{int(p[1]*100)}"


# ── data ─────────────────────────────────────────────────────────────────────

class Coin:
    __slots__ = ("name", "t", "o", "h", "l", "c", "dv", "n", "runid",
                 "r24", "dv24", "idx")

    def __init__(self, name: str, bars: list[list[float]]):
        self.name = name
        self.t = [int(b[T]) for b in bars]
        self.o = [b[O] for b in bars]
        self.h = [b[H] for b in bars]
        self.l = [b[L] for b in bars]
        self.c = [b[C] for b in bars]
        self.dv = [b[V] * b[C] for b in bars]
        self.n = len(bars)
        self.idx = {tt: i for i, tt in enumerate(self.t)}
        # runid: increments at every time gap -> O(1) window-contiguity checks
        rid, self.runid = 0, [0] * self.n
        for i in range(1, self.n):
            if self.t[i] - self.t[i - 1] != HOUR_MS:
                rid += 1
            self.runid[i] = rid
        # r24 / dv24 at close of bar i (need contiguous 24h window ending at i)
        self.r24 = [None] * self.n
        self.dv24 = [None] * self.n
        run = 0.0
        for i in range(self.n):
            run += self.dv[i]
            if i >= 24:
                run -= self.dv[i - 24]
                if self.runid[i] == self.runid[i - 24] and self.c[i - 24] > 0:
                    self.r24[i] = self.c[i] / self.c[i - 24] - 1.0
                    self.dv24[i] = run

    def contiguous(self, i0: int, i1: int) -> bool:
        return 0 <= i0 and i1 < self.n and self.runid[i0] == self.runid[i1]


def load_coins() -> dict[str, Coin]:
    raw = json.loads(HOURLY_CACHE.read_text())["candles"]
    return {name: Coin(name, bars) for name, bars in raw.items() if bars}


# ── BTC regime (20d trailing return sign, known at bar close) ────────────────

class Regime:
    def __init__(self, btc: Coin, window_h: int = 480):
        self.ts: list[int] = []
        self.sign: list[int] = []
        for i in range(window_h, btc.n):
            if btc.contiguous(i - window_h, i):
                r = btc.c[i] / btc.c[i - window_h] - 1.0
                self.ts.append(btc.t[i])
                self.sign.append(1 if r > 0 else -1)

    def at(self, t_ms: int) -> int | None:
        """Regime from the latest BTC bar with close time <= t_ms (no lookahead:
        the signal bar's own close is <= its own timestamp's close)."""
        j = bisect.bisect_right(self.ts, t_ms) - 1
        if j < 0 or t_ms - self.ts[j] > 3 * HOUR_MS:
            return None
        return self.sign[j]


# ── exit simulation (lookahead-safe, LOW-before-HIGH pessimistic) ────────────

def walk_stops(co: Coin, i_sig: int, max_h: int = 48) -> dict | None:
    """Fill at open[i_sig+1]; for each stop width return (bars_to_hit, exit_px);
    also horizon closes. Requires a contiguous window; else None."""
    e0 = i_sig + 1
    last = i_sig + max_h
    if last >= co.n or not co.contiguous(i_sig, last):
        return None
    entry = co.o[e0]
    if entry <= 0:
        return None
    hits: dict[float, tuple[int, float]] = {}
    live = set(STOPS)
    for j in range(e0, last + 1):
        for s in sorted(live, reverse=True):
            px = entry * (1 - s)
            if j > e0 and co.o[j] <= px:          # gapped through the stop
                hits[s] = (j - e0, co.o[j]); live.discard(s)
            elif co.l[j] <= px:
                hits[s] = (j - e0, px); live.discard(s)
        if not live:
            break
    horizon_close = {hh: co.c[i_sig + hh] for hh in HOLDS}
    return {"entry": entry, "hits": hits, "hclose": horizon_close}


def walk_trail(co: Coin, i_sig: int) -> float | None:
    """KAITO tight floor: arm +2% peak, give back 10% of peak gain, 15% hard
    stop, 48h max. LOW checked before HIGH updates the peak; arming and floor
    raises take effect the NEXT bar."""
    e0 = i_sig + 1
    last = i_sig + TRAIL_MAX
    if last >= co.n or not co.contiguous(i_sig, last):
        return None
    entry = co.o[e0]
    if entry <= 0:
        return None
    hard = entry * (1 - TRAIL_HARD)
    peak, armed = entry, False
    floor = None
    for j in range(e0, last + 1):
        if armed and floor is not None:
            if j > e0 and co.o[j] <= floor:
                return co.o[j] / entry - 1.0
            if co.l[j] <= floor:
                return floor / entry - 1.0
        if j > e0 and co.o[j] <= hard:
            return co.o[j] / entry - 1.0
        if co.l[j] <= hard:
            return hard / entry - 1.0
        if co.h[j] > peak:                          # HIGH after LOW
            peak = co.h[j]
            gain = peak / entry - 1.0
            if gain >= TRAIL_ARM:
                armed = True
            if armed:
                floor = entry * (1 + gain * (1 - TRAIL_GB))
    return co.c[last] / entry - 1.0


def policy_rets(co: Coin, i_sig: int) -> dict | None:
    """Gross return per policy for a signal at bar i_sig. None if window bad."""
    ws = walk_stops(co, i_sig)
    if ws is None:
        return None
    tr = walk_trail(co, i_sig)
    if tr is None:
        return None
    out = {}
    entry = ws["entry"]
    for hh in HOLDS:
        for s in STOPS:
            hit = ws["hits"].get(s)
            if hit is not None and hit[0] <= hh:
                out[(hh, s)] = hit[1] / entry - 1.0
            else:
                out[(hh, s)] = ws["hclose"][hh] / entry - 1.0
    out[TRAIL] = tr
    return out


# ── null pools: same coin, random strided times, same floor+regime ──────────

def build_pools(coins: dict[str, Coin], reg: Regime,
                floors=(5e6, 2e7)) -> dict:
    """pools[coin][policy] = list of (t, gross_ret, dv24, regime_sign).
    Eligibility: valid r24/dv24 (contiguous trailing 24h) and dv24 >= min(floors)
    — the caller filters by the exact floor/regime at draw time."""
    lo = min(floors)
    pools: dict[str, dict] = {}
    for name, co in coins.items():
        per: dict[tuple, list] = {p: [] for p in POLICIES}
        for i in range(24, co.n - 1, POOL_STRIDE):
            if co.dv24[i] is None or co.dv24[i] < lo:
                continue
            pr = policy_rets(co, i)
            if pr is None:
                continue
            rg = reg.at(co.t[i])
            for p in POLICIES:
                per[p].append((co.t[i], pr[p], co.dv24[i], rg))
        pools[name] = per
    return pools


def pool_view(pools, coin, policy, floor, regime) -> np.ndarray:
    rows = pools[coin][policy]
    return np.array([r for (t, r, dv, rg) in rows
                     if dv >= floor and (regime is None or rg == regime)])


# ── MC null + cell summary ───────────────────────────────────────────────────

def mc_pvalue(trades, pools, policy, floor, regime, iters=MC_BASE,
              seed=0) -> float | None:
    """trades: list of (coin, t, gross_ret). Null = for each trade draw a random
    same-coin pool return (same floor/regime), mean across trades, iters times."""
    if not trades:
        return None
    rng = np.random.default_rng(seed)
    by_coin: dict[str, int] = {}
    for c, _, _ in trades:
        by_coin[c] = by_coin.get(c, 0) + 1
    null_sum = np.zeros(iters)
    n = len(trades)
    global_pv = None
    for c, k in by_coin.items():
        pv = pool_view(pools, c, policy, floor, regime)
        if len(pv) < 10:                    # too thin to represent the null
            pv = pool_view(pools, c, policy, floor, None)
        if len(pv) < 10:                    # recent listing: cross-coin null
            if global_pv is None:
                global_pv = np.concatenate([
                    pool_view(pools, cc, policy, floor, regime)
                    for cc in pools] or [np.array([])])
            pv = global_pv
        if len(pv) < 10:
            return None
        idx = rng.integers(0, len(pv), size=(iters, k))
        null_sum += pv[idx].sum(axis=1)
    obs = statistics.mean(r for _, _, r in trades)
    null_means = null_sum / n
    ge = int((null_means >= obs).sum())
    return (ge + 1) / (iters + 1)


def cell_summary(trades, pools, policy, floor, regime, bonf_alpha,
                 seed=0) -> dict:
    """Full verdict for one cell. trades = [(coin, t_ms, gross_ret)]."""
    n = len(trades)
    out = {"n": n}
    if n == 0:
        out["verdict"] = "NO TRADES"
        return out
    rets = [r for _, _, r in trades]
    for sl in SLIPS:
        net = [r - sl for r in rets]
        out[f"ev{round(sl*10000)}"] = round(100 * statistics.mean(net), 4)
    out["win25"] = round(sum(1 for r in rets if r - VERDICT_SLIP > 0) / n, 3)
    ts = sorted(t for _, t, _ in trades)
    mid = ts[n // 2]
    h1 = [r for _, t, r in trades if t <= mid]
    h2 = [r for _, t, r in trades if t > mid]
    out["oos25_h1"] = round(100 * (statistics.mean(h1) - VERDICT_SLIP), 4) if h1 else None
    out["oos25_h2"] = round(100 * (statistics.mean(h2) - VERDICT_SLIP), 4) if h2 else None
    p = mc_pvalue(trades, pools, policy, floor, regime, MC_BASE, seed)
    if p is not None and p < 0.005:
        p = mc_pvalue(trades, pools, policy, floor, regime, MC_ESC, seed + 1)
    out["mc_p"] = p
    ev25 = out["ev25"]
    gates = {
        "ev25_pos": ev25 > 0,
        "oos_both": (out["oos25_h1"] or -1) > 0 and (out["oos25_h2"] or -1) > 0,
        "mc_sig": p is not None and p < 0.05,
        "bonferroni": p is not None and p < bonf_alpha,
        "n_ok": n >= 30,
    }
    out["gates"] = gates
    out["wire_eligible"] = all(gates.values())
    return out


def dedup_24h(sig_idx: list[int], co: Coin) -> list[int]:
    """Per-coin signal dedup: drop signals < 24h after the previous kept one."""
    out, last_t = [], -10**18
    for i in sig_idx:
        if co.t[i] - last_t >= 24 * HOUR_MS:
            out.append(i)
            last_t = co.t[i]
    return out


if __name__ == "__main__":
    coins = load_coins()
    btc = coins["BTC"]
    reg = Regime(btc)
    up = sum(1 for s in reg.sign if s > 0)
    print(f"{len(coins)} coins; BTC bars {btc.n}; regime bars {len(reg.ts)} "
          f"({up} up / {len(reg.ts)-up} down)")
    import datetime as dt
    print("span:", dt.datetime.fromtimestamp(btc.t[0]/1000, dt.UTC),
          "->", dt.datetime.fromtimestamp(btc.t[-1]/1000, dt.UTC))
    # smoke: KAITO trail on an arbitrary bar
    k = coins["KAITO"]
    pr = policy_rets(k, 3000)
    print("KAITO@3000 sample policy rets:",
          {pol_name(p): round(100*r, 2) for p, r in list(pr.items())[:4]},
          "trail:", round(100*pr[TRAIL], 2))
