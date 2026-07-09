"""W-H2 — cross-sectional 1h dispersion spikes: momentum continuation or
convergence? Tested as a conditional overlay on an hourly xs-momentum book.

PRE-REGISTERED SPEC:
- disp[i] = cross-sectional pstdev of the 1h close-to-close returns at bar i
  across all alts with data (require >= 20 names). Known at close[i].
- SPIKE gate: disp[i] >= expanding P90 of all PAST disp values (burn-in 336
  bars, strictly-past percentile -> lookahead-safe). CALM = below P90.
- Book: rank alts by trailing 24h return (close[i]/close[i-24]-1); LONG top 8,
  SHORT bottom 8. Fill open[i+1], exit open[i+1+HOLD], HOLD in {6, 24}.
- Unit = per-event book spread: mean(long-leg fwd) - mean(short-leg fwd).
  Costs: each side is an alt leg -> net = gross - 2*tier; tiers {0,12,25}.
- Episodes deduped to HOLD-bar spacing in BOTH cells (independence).
- Funding: HOLD=24 >= 8h -> compute the spread's net funding from funding.json
  where coverage exists (2026-03-29..06-27); report its mean size. Longs pay
  positive funding, shorts collect.
- MC null (>=2000): pool = the same book built at EVERY bar (any regime);
  shuffle_label_p of the spike-cell mean vs the pool answers "does the spike
  gate add anything to always-on momentum timing?" Report spike & calm cells.
- Decision rule (declared): continuation overlay only if spike-cell net(2x12bps)
  > 0, OOS both halves positive, mc_p < 0.05; convergence claim (the A8 ghost)
  requires the mirrored condition on the negative side. Anything else = no overlay.
- Prior art: A8 dispersion_mean_reversion refuted DAILY convergence; this is
  hourly and scored as an overlay on momentum, not a standalone convergence book.
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
import funding_lib as fl  # noqa: E402

T, O, H, L, C, V = 0, 1, 2, 3, 4, 5
HOUR = 3_600_000
K = 8
BURN = 336
HOLDS = [6, 24]
P_GATE = 0.90


def main() -> None:
    cs = wh0.load_ext()
    fund = fl.load_funding()
    coins = [c for c in cs if c != "BTC" and len(cs[c]) > 400]
    btc = cs["BTC"]
    ts_all = [int(b[T]) for b in btc]

    rets: dict[str, dict[int, float]] = {c: dict(wh0.hourly_rets(cs[c])) for c in coins}
    idx: dict[str, dict[int, int]] = {c: wh0.bar_index(cs[c]) for c in coins}
    close: dict[str, dict[int, float]] = {
        c: {int(b[T]): b[C] for b in cs[c]} for c in coins}

    # dispersion series (known at close of bar t)
    disp: list[tuple[int, float]] = []
    for t in ts_all:
        xs = [rets[c][t] for c in coins if t in rets[c]]
        if len(xs) >= 20:
            disp.append((t, statistics.pstdev(xs)))

    # expanding strictly-past P90 gate
    spike_ts: set[int] = set()
    past: list[float] = []
    for i, (t, v) in enumerate(disp):
        if i >= BURN:
            s = sorted(past)
            thr = s[int(P_GATE * (len(s) - 1))]
            if v >= thr:
                spike_ts.add(t)
        past.append(v)
    eligible_ts = [t for i, (t, _) in enumerate(disp) if i >= BURN]
    print(f"eligible bars={len(eligible_ts)} spike bars={len(spike_ts)}")

    def book_at(t: int, hold: int):
        """Momentum book spread at bar t (per pre-registered spec)."""
        ranks = []
        for c in coins:
            p_now = close[c].get(t)
            p_24 = close[c].get(t - 24 * HOUR)
            if p_now and p_24:
                ranks.append((p_now / p_24 - 1.0, c))
        if len(ranks) < 2 * K + 4:
            return None
        ranks.sort()
        shorts = [c for _, c in ranks[:K]]
        longs = [c for _, c in ranks[-K:]]
        lf, sf = [], []
        f_adj = 0.0
        n_f = 0
        for side, names, acc in (("L", longs, lf), ("S", shorts, sf)):
            for c in names:
                fr = wh0.fwd_open_ret(cs[c], idx[c], t, hold)
                if fr is None:
                    continue
                acc.append(fr)
                if hold >= 8:
                    cf = fl.cum_funding(fund, c, t + HOUR, t + (1 + hold) * HOUR)
                    if cf:
                        n_f += 1
                        f_adj += -cf if side == "L" else +cf
        if len(lf) < K // 2 or len(sf) < K // 2:
            return None
        spread = statistics.mean(lf) - statistics.mean(sf)
        fund_per_book = f_adj / max(1, (len(lf) + len(sf))) * 2 if n_f else 0.0
        return spread, fund_per_book, n_f

    for hold in HOLDS:
        spike_ev = wh0.dedup_episodes(
            [{"t": t} for t in sorted(spike_ts)], hold * HOUR)
        calm_ev = wh0.dedup_episodes(
            [{"t": t} for t in sorted(set(eligible_ts) - spike_ts)], hold * HOUR)

        def run_cell(evs):
            out = []
            for e in evs:
                got = book_at(e["t"], hold)
                if got is not None:
                    out.append({"t": e["t"], "ret": got[0], "fund": got[1],
                                "nf": got[2]})
            return out

        spike_tr = run_cell(spike_ev)
        calm_tr = run_cell(calm_ev)
        pool = []
        for t in eligible_ts[::3]:
            got = book_at(t, hold)
            if got is not None:
                pool.append(got[0])

        def report(name, trs):
            if len(trs) < 15:
                print(f"  HOLD={hold} {name:<6} n={len(trs)} NOT-RIPE")
                return
            g = statistics.mean(x["ret"] for x in trs)
            fs = [x["fund"] for x in trs if x["nf"]]
            f_mean = statistics.mean(fs) if fs else 0.0
            net12 = g - 2 * 12 / 1e4 + f_mean
            net25 = g - 2 * 25 / 1e4 + f_mean
            h1, h2 = al.time_split(trs)
            e1 = statistics.mean(x["ret"] for x in h1) - 2 * 12 / 1e4 if h1 else float("nan")
            e2 = statistics.mean(x["ret"] for x in h2) - 2 * 12 / 1e4 if h2 else float("nan")
            mc = mc_null.shuffle_label_p([x["ret"] for x in trs], pool,
                                         n_iter=3000, seed=11)
            print(f"  HOLD={hold} {name:<6} n={len(trs):<3} gross={100*g:+.3f}% "
                  f"net12={100*net12:+.3f}% net25={100*net25:+.3f}% "
                  f"fund_adj={100*f_mean:+.4f}% (cover {len(fs)}) "
                  f"OOS12 {100*e1:+.3f}/{100*e2:+.3f} mc_p={mc['p_one_sided']} "
                  f"excess={100*(mc['excess'] or 0):+.3f}%")

        print(f"\n== HOLD {hold}h (book spread, cost=2x tier) ==")
        report("SPIKE", spike_tr)
        report("CALM", calm_tr)
        if len(spike_tr) >= 15 and len(calm_tr) >= 15:
            dif = (statistics.mean(x["ret"] for x in spike_tr)
                   - statistics.mean(x["ret"] for x in calm_tr))
            print(f"  spike-minus-calm gross diff = {100*dif:+.3f}%")


if __name__ == "__main__":
    main()
