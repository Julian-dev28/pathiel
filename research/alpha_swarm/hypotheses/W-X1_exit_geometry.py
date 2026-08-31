"""W-X1 — Lane X: exit-geometry / win-rate engineering on VALIDATED entry families.

PRE-REGISTERED SPEC (declared in full before the first run; every cell reported).

OBJECTIVE: for each validated entry family, find the exit geometry that MAXIMIZES
WIN RATE subject to EV@25bps > 0 (funding included on holds >= 8h). Report the
whole (win%, EV25) frontier honestly.

DATA: W-H0 extended 1h cache (~208d x 40 coins, bar-exact validated, 2025-12-13..
2026-07-09), loaded via W-H0_fetch.load_ext(). Daily bars are aggregated from 1h
bars by UTC day; a day is COMPLETE iff its 23:00 bar exists and >=20 bars present.
Signals decide on completed daily bars; fills at the NEXT 1h bar open (00:00 UTC,
must be exactly close_t + 1h — else episode dropped). funding.json (hourly rows)
supplies the funding term.

ENTRY FAMILIES (validated elsewhere — NOT re-derived, entries frozen):
  F1 extreme_fade ARMED (LONG):  coin 1d ret <= -12% (daily close/close), market
     20d skew < 0 (equal-weight market daily ret, population skew m3/m2^1.5,
     trailing 20 rets ending at signal day; >=10 coins/day). Baseline exit:
     stop 20%, horizon 3d (72 x 1h bars).                     [findings/W-B2.md]
  F1d deep tier: same but coin 1d ret <= -20% (subset, reported separately).
  F2 funding_spike_short (SHORT): trailing-24h mean funding F24 at day-open t,
     z >= 2 vs own trailing-30d F24 (>=15 hist days, >=18 settled rows/24h);
     enter at day t+1 open; episode dedup: z<1 resets, no re-entry while open.
     Baseline exit: stop 15%, horizon 5d (120 bars).          [findings/W-F2.md]
  F3 engulf_short (SHORT, secondary): daily bearish engulf (c<o, prev c>prev o,
     o>=pc, c<=po) on completed contiguous days; short next 1h open. Baseline:
     stop 20%, horizon 1d (24 bars).       [engulfing_reversal_xs.py, live spec]
  F4 crash_continue_short (SHORT, secondary): BTC 2d ret > 0 AND coin 2d ret
     <= -8%; short next 1h open. Baseline: stop 20%, horizon 10d (240 bars).
     BTC-up gate def pre-registered here as BTC 2d close ret > 0 (divergence
     read of "BTC-up + coin -8%/2d").         [engulf_crash_sweep.md: stop 20]
  Dedup (all families, geometry-independent so every cell scores the SAME
  episode set): per coin, no new episode until entry_t + horizon.

EXIT-GEOMETRY GRID (29 cells/family, identical across families; the baseline
stop/horizon NEVER changes — only the overlay differs):
  baseline                                   (1)
  partial50@B: close 50% at +B%, B in {1.5,2,3,4,6}; rest rides stop/horizon (5)
  fulltp@T:    close 100% at +T%, T in {2,3,4,6,8}                           (5)
  belock@B:    after +B% (B in {2,3,4}), stop moves to entry                 (3)
  trail@P/rR:  arm at peak >= +P% (P in {1.5,2,3}); exit when gain retraces
               to peak*(1-R), R in {10,15,20,25,35}%                        (15)
Bonferroni: m=29 cells/family — flagged when p*29 fails.

INTRA-BAR ASSUMPTION (pessimistic, per audit 2026-07-09): within each 1h bar the
event order is open -> ADVERSE extreme -> favorable extreme -> close (low before
high for longs; high before low for shorts). Consequences: a stop/floor and a TP
in the same bar resolves to the STOP; a lock/arm earned at the favorable extreme
protects only the extreme->close leg of that bar onward; favorable triggers fill
AT the trigger level (never better); adverse gaps at open fill at open (worse).

COSTS: net = weighted gross price ret + funding term - tier, tier in {12,25} bps
round-trip on full notional (partial exits keep total traded notional identical).
FUNDING (holds >= 8h): per exit leg w at t_x, funding = sum of settled hourly
rates over (entry, t_x]: SHORT collects +sum (missing coverage hours contribute
0 — conservative), LONG pays -(sum + missing_hours * max(coin mean rate,
1.25e-5)) — conservative. funding.json coverage is printed (~90d; F1/F3/F4
episodes outside it use the fallback).

SCORING per cell: n, win25 (net25>0 — THE win definition), win12, EV12, EV25,
avg win / avg loss (net25), mean episode max drawdown of the hourly mark-to-
market equity path, OOS EV25 first/second time half, p_pos = bootstrap
P(EV25<=0) (3000 iter), p_vs_base = paired sign-flip permutation p (two-sided)
of net25 delta vs baseline (3000 iter).
RECOMMENDATION RULE (pre-registered): among cells with win25 >= 0.65 AND
EV25 > 0 AND both OOS halves > 0, pick max EV25; tie-break higher win25. If no
cell clears, report the best achievable trade-off explicitly.
PORTFOLIO: episodes/month from each family's eligible-day span; blended win =
episode-weighted; monthly $EV at $20 and $60 per position.

Survivorship: universe is TODAY's liquid 40 — all positive EV is an UPPER BOUND.
Run: .venv/bin/python research/alpha_swarm/hypotheses/W-X1_exit_geometry.py
Self-test: ... --selftest  (hand-computed engine cases must pass first).
"""
from __future__ import annotations

import bisect
import json
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))

import importlib

wh0 = importlib.import_module("W-H0_fetch")
import funding_lib as fl  # noqa: E402

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
HOUR = 3_600_000
DAY = 24 * HOUR
TIER12, TIER25 = 0.0012, 0.0025
DEFAULT_HOURLY_RATE = 1.25e-5  # HL baseline funding, conservative long-pay fallback

SCRATCH = Path(
    "/private/tmp/claude-501/-Users-julian-dev-Documents-code-pathia/"
    "f77b77de-96c2-4bf2-a574-1fd5aeebb7f2/scratchpad")

# ── pre-registered grid ──────────────────────────────────────────────────────
CELLS: list[tuple[str, dict]] = [("baseline", {})]
CELLS += [(f"partial50@{b}", {"partial": (b / 100, 0.5)}) for b in (1.5, 2, 3, 4, 6)]
CELLS += [(f"fulltp@{t}", {"tp": t / 100}) for t in (2, 3, 4, 6, 8)]
CELLS += [(f"belock@{b}", {"belock": b / 100}) for b in (2, 3, 4)]
CELLS += [(f"trail@{p}/r{r}", {"trail": (p / 100, r / 100)})
          for p in (1.5, 2, 3) for r in (10, 15, 20, 25, 35)]
N_CELLS = len(CELLS)  # 29

FAMILIES = {
    "F1_extreme_fade_armed": dict(side=+1, stop=0.20, hold_h=72),
    "F1d_extreme_fade_deep": dict(side=+1, stop=0.20, hold_h=72),
    "F2_funding_spike_short": dict(side=-1, stop=0.15, hold_h=120),
    "F3_engulf_short": dict(side=-1, stop=0.20, hold_h=24),
    "F4_crash_continue_short": dict(side=-1, stop=0.20, hold_h=240),
}


# ── exit engine (pessimistic intra-bar) ──────────────────────────────────────
def simulate(bars: list, i0: int, side: int, stop: float, hold_h: int,
             cfg: dict) -> dict | None:
    """Enter at bars[i0] open. Returns weighted gross price ret, exit legs
    [(t_ms, weight, gain)], hourly equity path max drawdown."""
    if i0 + hold_h > len(bars):
        return None
    E = bars[i0][O]
    if not E or E <= 0:
        return None

    def g(p: float) -> float:
        return side * (p / E - 1.0)

    tp = cfg.get("tp")
    partial = cfg.get("partial")          # (B, frac)
    belock = cfg.get("belock")            # B
    trail = cfg.get("trail")              # (P, R)
    stop_g = -stop
    open_w, banked = 1.0, 0.0
    legs: list[tuple[int, float, float]] = []
    peak, armed, be_active, partial_done = 0.0, False, False, False
    eq, run_max, maxdd = 0.0, 0.0, 0.0

    def prot_level() -> float:
        lv = 0.0 if be_active else stop_g
        if trail and armed:
            lv = max(lv, peak * (1.0 - trail[1]))
        return lv

    def bank(t_ms: int, w: float, gain: float) -> None:
        nonlocal open_w, banked
        banked += w * gain
        open_w -= w
        legs.append((t_ms, w, gain))

    end = i0 + hold_h
    for i in range(i0, end):
        b = bars[i]
        t_x = int(b[T]) + HOUR  # fills inside bar i settle by bar close time
        o = b[O]
        fav = b[H] if side > 0 else b[L]
        adv = b[L] if side > 0 else b[H]
        g_o, g_fav, g_adv, g_c = g(o), g(fav), g(adv), g(b[C])

        # 1) OPEN — gaps (adverse checked first)
        if g_o <= prot_level():
            bank(t_x, open_w, g_o)
            break
        if partial and not partial_done and g_o >= partial[0]:
            bank(t_x, open_w * partial[1], partial[0])  # limit fills AT level
            partial_done = True
        if tp is not None and g_o >= tp:
            bank(t_x, open_w, tp)
            break
        # 2) ADVERSE extreme — protection state entering the bar
        if g_adv <= prot_level():
            lv = prot_level()
            bank(t_x, open_w, lv)
            break
        # 3) FAVORABLE extreme — banks, locks, arms
        if partial and not partial_done and g_fav >= partial[0]:
            bank(t_x, open_w * partial[1], partial[0])
            partial_done = True
        if tp is not None and g_fav >= tp:
            bank(t_x, open_w, tp)
            break
        if belock is not None and g_fav >= belock:
            be_active = True
        if trail:
            peak = max(peak, g_fav)
            if peak >= trail[0]:
                armed = True
        # 4) favorable-extreme -> close leg: newly raised protection can trigger
        if g_c <= prot_level():
            bank(t_x, open_w, prot_level())
            break
        # equity mark at close
        eq = banked + open_w * g_c
        run_max = max(run_max, eq)
        maxdd = max(maxdd, run_max - eq)

    if open_w > 1e-12:  # horizon exit at close of last bar
        bank(int(bars[end - 1][T]) + HOUR, open_w, g(bars[end - 1][C]))
    eq = banked
    maxdd = max(maxdd, run_max - eq)
    return {"gross": banked, "legs": legs, "maxdd": maxdd,
            "entry_t": int(bars[i0][T])}


# ── funding term ─────────────────────────────────────────────────────────────
class Funding:
    def __init__(self) -> None:
        f = fl.load_funding()
        self.ft: dict[str, list[int]] = {}
        self.fp: dict[str, list[float]] = {}
        self.mean_rate: dict[str, float] = {}
        lo, hi = None, None
        for c in f["coins"]:
            rs = fl.rows(f, c)
            if not rs:
                continue
            self.ft[c] = [int(r[0]) for r in rs]
            ps = [0.0]
            for r in rs:
                ps.append(ps[-1] + r[1])
            self.fp[c] = ps
            self.mean_rate[c] = (ps[-1] / len(rs)) if rs else DEFAULT_HOURLY_RATE
            lo = min(lo, rs[0][0]) if lo else rs[0][0]
            hi = max(hi, rs[-1][0]) if hi else rs[-1][0]
        self.lo, self.hi = lo, hi

    def cum_and_count(self, c: str, t0: int, t1: int) -> tuple[float, int]:
        ts = self.ft.get(c)
        if not ts:
            return 0.0, 0
        i0 = bisect.bisect_right(ts, t0)
        i1 = bisect.bisect_right(ts, t1)
        return self.fp[c][i1] - self.fp[c][i0], i1 - i0

    def term(self, c: str, side: int, entry_t: int,
             legs: list[tuple[int, float, float]]) -> float:
        """Signed funding contribution to the episode return (per unit notional)."""
        out = 0.0
        for t_x, w, _gain in legs:
            hours = (t_x - entry_t) / HOUR
            if hours < 8:
                continue
            s, n = self.cum_and_count(c, entry_t, t_x)
            missing = max(0.0, hours - n)
            if side < 0:
                out += w * s                       # short collects; gaps = 0
            else:
                est = max(self.mean_rate.get(c, DEFAULT_HOURLY_RATE),
                          DEFAULT_HOURLY_RATE)
                out -= w * (s + missing * est)     # long pays; gaps at est rate
        return out


# ── daily aggregation from 1h ────────────────────────────────────────────────
def build_days(bars: list) -> list[dict]:
    """UTC-day rows: {d, o, h, l, c, close_t, complete}. Sorted."""
    by: dict[int, list] = {}
    for b in bars:
        by.setdefault(int(b[T]) // DAY * DAY, []).append(b)
    out = []
    for d in sorted(by):
        bs = sorted(by[d], key=lambda x: x[T])
        has_close = any(int(b[T]) % DAY == 23 * HOUR for b in bs)
        out.append({"d": d, "o": bs[0][O], "h": max(b[H] for b in bs),
                    "l": min(b[L] for b in bs), "c": bs[-1][C],
                    "close_t": int(bs[-1][T]),
                    "complete": has_close and len(bs) >= 20})
    return out


def skew_pop(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    m = statistics.mean(xs)
    m2 = sum((x - m) ** 2 for x in xs) / n
    m3 = sum((x - m) ** 3 for x in xs) / n
    if m2 <= 0:
        return None
    return m3 / m2 ** 1.5


# ── episode collection ───────────────────────────────────────────────────────
def collect_episodes(cs: dict, fund: Funding) -> dict[str, list[dict]]:
    idx = {c: wh0.bar_index(cs[c]) for c in cs}
    days = {c: build_days(cs[c]) for c in cs}
    dmap = {c: {r["d"]: r for r in days[c]} for c in cs}

    def ret_nd(c: str, d: int, n: int) -> float | None:
        r0, r1 = dmap[c].get(d - n * DAY), dmap[c].get(d)
        if not r0 or not r1 or not (r0["complete"] and r1["complete"]):
            return None
        # all intermediate days must exist + be complete (no hole-spanning rets)
        for k in range(1, n):
            rk = dmap[c].get(d - k * DAY)
            if not rk or not rk["complete"]:
                return None
        return r1["c"] / r0["c"] - 1.0 if r0["c"] > 0 else None

    all_days = sorted({r["d"] for c in cs for r in days[c]})

    # market equal-weight daily ret + trailing-20 skew (known at day close)
    mret: dict[int, float] = {}
    for d in all_days:
        rs = [ret_nd(c, d, 1) for c in cs]
        rs = [r for r in rs if r is not None]
        if len(rs) >= 10:
            mret[d] = statistics.mean(rs)
    skew20: dict[int, float] = {}
    md = sorted(mret)
    for i in range(19, len(md)):
        window = md[i - 19:i + 1]
        if window[-1] - window[0] == 19 * DAY:  # contiguous 20 days
            s = skew_pop([mret[x] for x in window])
            if s is not None:
                skew20[md[i]] = s

    def entry_bar(c: str, d_signal: int) -> int | None:
        """First 1h bar of the day after d_signal; must be exactly close_t+1h."""
        row = dmap[c].get(d_signal)
        if not row or not row["complete"]:
            return None
        t_e = d_signal + DAY
        i = idx[c].get(t_e)
        if i is None or row["close_t"] + HOUR != t_e:
            return None
        return i

    eps: dict[str, list[dict]] = {k: [] for k in FAMILIES}
    # F1 / F1d extreme_fade armed
    for c in cs:
        last_free = 0
        for d in sorted(dmap[c]):
            r1 = ret_nd(c, d, 1)
            sk = skew20.get(d)
            if r1 is None or sk is None or r1 > -0.12 or sk >= 0:
                continue
            i0 = entry_bar(c, d)
            if i0 is None or d + DAY < last_free:
                continue
            last_free = d + DAY + FAMILIES["F1_extreme_fade_armed"]["hold_h"] * HOUR
            e = {"coin": c, "i0": i0, "d": d, "ret1d": r1}
            eps["F1_extreme_fade_armed"].append(e)
            if r1 <= -0.20:
                eps["F1d_extreme_fade_deep"].append(dict(e))

    # F2 funding_spike_short (z on F24 vs own 30d; entry day t+1 open)
    grid = [d for d in all_days]
    f24: dict[str, dict[int, float]] = {c: {} for c in cs}
    for c in cs:
        for d in grid:
            s, n = fund.cum_and_count(c, d - DAY, d)
            if n >= 18:
                f24[c][d] = s / n
    for c in cs:
        in_ep, last_exit = False, 0
        for d in grid:
            cur = f24[c].get(d)
            if cur is None:
                continue
            hist = [f24[c].get(d - k * DAY) for k in range(1, 31)]
            hist = [h for h in hist if h is not None]
            if len(hist) < 15:
                continue
            z = (cur - statistics.mean(hist)) / (statistics.pstdev(hist) + 1e-12)
            if z < 1.0:
                in_ep = False
            if z >= 2.0:
                if in_ep or d < last_exit:
                    continue
                in_ep = True
                i0 = entry_bar(c, d)  # entry = open of day d+1 (W-F2 spec)
                if i0 is None:
                    continue
                last_exit = d + DAY + FAMILIES["F2_funding_spike_short"]["hold_h"] * HOUR
                eps["F2_funding_spike_short"].append(
                    {"coin": c, "i0": i0, "d": d, "z": round(z, 2)})

    # F3 engulf_short
    for c in cs:
        last_free = 0
        ds = sorted(dmap[c])
        for d in ds:
            p, r = dmap[c].get(d - DAY), dmap[c].get(d)
            if not p or not r or not (p["complete"] and r["complete"]):
                continue
            if not (r["c"] < r["o"] and p["c"] > p["o"]
                    and r["o"] >= p["c"] and r["c"] <= p["o"]):
                continue
            i0 = entry_bar(c, d)
            if i0 is None or d + DAY < last_free:
                continue
            last_free = d + DAY + FAMILIES["F3_engulf_short"]["hold_h"] * HOUR
            eps["F3_engulf_short"].append({"coin": c, "i0": i0, "d": d})

    # F4 crash_continue_short (BTC 2d ret > 0, coin 2d ret <= -8%)
    for c in cs:
        if c == "BTC":
            continue
        last_free = 0
        for d in sorted(dmap[c]):
            r2 = ret_nd(c, d, 2)
            b2 = ret_nd("BTC", d, 2)
            if r2 is None or b2 is None or r2 > -0.08 or b2 <= 0:
                continue
            i0 = entry_bar(c, d)
            if i0 is None or d + DAY < last_free:
                continue
            last_free = d + DAY + FAMILIES["F4_crash_continue_short"]["hold_h"] * HOUR
            eps["F4_crash_continue_short"].append({"coin": c, "i0": i0, "d": d})

    return eps


# ── stats helpers ────────────────────────────────────────────────────────────
def boot_p_pos(rets: list[float], n_iter: int = 3000, seed: int = 11) -> float:
    rng = random.Random(seed)
    k = len(rets)
    le = 0
    for _ in range(n_iter):
        m = statistics.mean(rets[rng.randrange(k)] for _ in range(k))
        if m <= 0:
            le += 1
    return (le + 1) / (n_iter + 1)


def signflip_p(deltas: list[float], n_iter: int = 3000, seed: int = 7) -> float:
    if not deltas:
        return 1.0
    obs = abs(statistics.mean(deltas))
    rng = random.Random(seed)
    ge = 0
    for _ in range(n_iter):
        m = abs(statistics.mean(d if rng.random() < 0.5 else -d for d in deltas))
        if m >= obs:
            ge += 1
    return (ge + 1) / (n_iter + 1)


def time_split(rows: list[dict]) -> tuple[list, list]:
    ts = sorted(r["t"] for r in rows)
    mid = ts[len(ts) // 2]
    return [r for r in rows if r["t"] <= mid], [r for r in rows if r["t"] > mid]


# ── scoring ──────────────────────────────────────────────────────────────────
def run_family(name: str, spec: dict, episodes: list[dict], cs: dict,
               fund: Funding) -> list[dict]:
    rows_out = []
    base_nets: list[float] | None = None
    for cell_name, cfg in CELLS:
        rows = []
        for e in episodes:
            sim = simulate(cs[e["coin"]], e["i0"], spec["side"], spec["stop"],
                           spec["hold_h"], cfg)
            if sim is None:
                continue
            fterm = fund.term(e["coin"], spec["side"], sim["entry_t"], sim["legs"])
            rows.append({"t": sim["entry_t"], "coin": e["coin"],
                         "net12": sim["gross"] + fterm - TIER12,
                         "net25": sim["gross"] + fterm - TIER25,
                         "maxdd": sim["maxdd"]})
        n = len(rows)
        if n == 0:
            rows_out.append({"cell": cell_name, "n": 0})
            continue
        nets25 = [r["net25"] for r in rows]
        nets12 = [r["net12"] for r in rows]
        wins = [r for r in rows if r["net25"] > 0]
        losses = [r for r in rows if r["net25"] <= 0]
        h1, h2 = time_split(rows)
        if cell_name == "baseline":
            base_nets = nets25
        deltas = ([a - b for a, b in zip(nets25, base_nets)]
                  if base_nets and len(base_nets) == n else [])
        rec = {
            "cell": cell_name, "n": n,
            "win25": sum(1 for x in nets25 if x > 0) / n,
            "win12": sum(1 for x in nets12 if x > 0) / n,
            "ev12": statistics.mean(nets12),
            "ev25": statistics.mean(nets25),
            "avg_win": statistics.mean([r["net25"] for r in wins]) if wins else 0.0,
            "avg_loss": statistics.mean([r["net25"] for r in losses]) if losses else 0.0,
            "mean_maxdd": statistics.mean([r["maxdd"] for r in rows]),
            "oos1": statistics.mean([r["net25"] for r in h1]) if h1 else None,
            "oos2": statistics.mean([r["net25"] for r in h2]) if h2 else None,
            "p_pos": boot_p_pos(nets25),
            "p_vs_base": (signflip_p(deltas) if cell_name != "baseline" else None),
        }
        rows_out.append(rec)
    return rows_out


def fmt_row(r: dict) -> str:
    if r.get("n", 0) == 0:
        return f"  {r['cell']:<16} n=0"
    pvb = f"{r['p_vs_base']:.4f}" if r["p_vs_base"] is not None else "  --  "
    return (f"  {r['cell']:<16} n={r['n']:<3} win25={100*r['win25']:5.1f}% "
            f"EV12={100*r['ev12']:+6.2f}% EV25={100*r['ev25']:+6.2f}% "
            f"aW={100*r['avg_win']:+5.2f} aL={100*r['avg_loss']:+5.2f} "
            f"mDD={100*r['mean_maxdd']:5.2f} "
            f"OOS25 {100*r['oos1']:+5.2f}/{100*r['oos2']:+5.2f} "
            f"p+={r['p_pos']:.4f} pΔ={pvb}")


def pareto(rows: list[dict]) -> list[dict]:
    ok = [r for r in rows if r.get("n", 0) > 0]
    front = []
    for r in ok:
        if not any(o["win25"] >= r["win25"] and o["ev25"] >= r["ev25"]
                   and (o["win25"] > r["win25"] or o["ev25"] > r["ev25"])
                   for o in ok):
            front.append(r)
    return sorted(front, key=lambda x: -x["win25"])


def recommend(rows: list[dict]) -> dict | None:
    ok = [r for r in rows if r.get("n", 0) >= 15 and r["win25"] >= 0.65
          and r["ev25"] > 0 and (r["oos1"] or 0) > 0 and (r["oos2"] or 0) > 0]
    if not ok:
        return None
    return sorted(ok, key=lambda x: (-x["ev25"], -x["win25"]))[0]


# ── engine self-test (hand-computed) ─────────────────────────────────────────
def selftest() -> None:
    def bars_of(rows):  # (o,h,l,c) hourly from t=0
        return [[i * HOUR, o, h, l, c, 0.0] for i, (o, h, l, c) in enumerate(rows)]

    A = lambda x, y: abs(x - y) < 1e-12 or (_ for _ in ()).throw(
        AssertionError(f"{x} != {y}"))
    # 1) pessimistic: stop and TP in same bar -> stop first (long)
    b = bars_of([(100, 103, 96.9, 100)])
    r = simulate(b, 0, +1, 0.03, 1, {"tp": 0.02})
    A(r["gross"], -0.03)
    # 2) full TP fills AT level
    b = bars_of([(100, 103, 99, 102)])
    r = simulate(b, 0, +1, 0.20, 1, {"tp": 0.02})
    A(r["gross"], 0.02)
    # 3) partial50@2: half banked at +2, rest exits at horizon close (+2)
    r = simulate(b, 0, +1, 0.20, 1, {"partial": (0.02, 0.5)})
    A(r["gross"], 0.5 * 0.02 + 0.5 * 0.02)
    # 4) trail arm1.5 r20: peak 3% -> floor 2.4%; close 2% < floor -> exit 2.4%
    r = simulate(b, 0, +1, 0.20, 1, {"trail": (0.015, 0.20)})
    A(r["gross"], 0.03 * 0.8)
    # 5) belock@2 armed at high cannot save same-bar LOW, but catches h->c leg
    b = bars_of([(100, 103, 98, 99)])
    r = simulate(b, 0, +1, 0.20, 1, {"belock": 0.02})
    A(r["gross"], 0.0)
    # 6) adverse gap at a LATER bar's open fills at open (worse than stop)
    b = bars_of([(100, 100.5, 99.5, 100), (90, 95, 89, 94)])
    r = simulate(b, 0, +1, 0.05, 2, {})
    A(r["gross"], -0.10)
    # 7) short mirror: adverse = HIGH first
    b = bars_of([(100, 103.1, 97, 100)])
    r = simulate(b, 0, -1, 0.03, 1, {"tp": 0.02})
    A(r["gross"], -0.03)
    # 8) trail floor from PRIOR bar's peak protects next bar's adverse leg
    b = bars_of([(100, 103, 100, 102.5), (102.5, 102.6, 100, 101)])
    r = simulate(b, 0, +1, 0.20, 2, {"trail": (0.015, 0.20)})
    A(r["gross"], 0.024)
    # 9) baseline horizon exit at last close
    b = bars_of([(100, 101, 99, 100.5), (100.5, 102, 100, 101)])
    r = simulate(b, 0, +1, 0.20, 2, {})
    A(r["gross"], 0.01)
    print("W-X1 engine self-test: 9/9 PASS")


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    cs = wh0.load_ext()
    fund = Funding()
    import datetime as dt
    span_lo = min(b[0][T] for b in cs.values())
    span_hi = max(b[-1][T] for b in cs.values())
    print(f"cache: {len(cs)} coins, "
          f"{dt.datetime.fromtimestamp(span_lo/1000, dt.UTC):%Y-%m-%d} .. "
          f"{dt.datetime.fromtimestamp(span_hi/1000, dt.UTC):%Y-%m-%d}")
    print(f"funding coverage: "
          f"{dt.datetime.fromtimestamp(fund.lo/1000, dt.UTC):%Y-%m-%d} .. "
          f"{dt.datetime.fromtimestamp(fund.hi/1000, dt.UTC):%Y-%m-%d}")

    eps = collect_episodes(cs, fund)
    results: dict[str, list[dict]] = {}
    spans: dict[str, float] = {}
    for fam, spec in FAMILIES.items():
        rows = run_family(fam, spec, eps[fam], cs, fund)
        results[fam] = rows
        es = eps[fam]
        if es:
            ts = [cs[e["coin"]][e["i0"]][T] for e in es]
            spans[fam] = (max(ts) - min(ts)) / DAY + spec["hold_h"] / 24
        print(f"\n===== {fam} (side={spec['side']:+d}, stop={spec['stop']:.0%}, "
              f"hold={spec['hold_h']}h) — n={len(es)} episodes =====")
        for r in rows:
            print(fmt_row(r))
        pf = pareto(rows)
        print("  -- Pareto (win25, EV25):",
              "; ".join(f"{r['cell']} ({100*r['win25']:.0f}%, "
                        f"{100*r['ev25']:+.2f}%)" for r in pf))
        rec = recommend(rows)
        if rec:
            print(f"  -- RECOMMENDED: {rec['cell']} win25={100*rec['win25']:.1f}% "
                  f"EV25={100*rec['ev25']:+.2f}% "
                  f"OOS {100*rec['oos1']:+.2f}/{100*rec['oos2']:+.2f} "
                  f"p+={rec['p_pos']:.4f} (Bonferroni m={N_CELLS}: "
                  f"p*29={min(1, rec['p_pos']*N_CELLS):.3f})")
        else:
            print("  -- NO cell clears win>=65% & EV25>0 & OOS-both-halves>0")

    (SCRATCH / "W-X1_results.json").write_text(json.dumps(
        {"families": results, "spans_days": spans,
         "n_eps": {k: len(v) for k, v in eps.items()}}, indent=1, default=str))
    print(f"\nfull results -> {SCRATCH / 'W-X1_results.json'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        selftest()
        main()
