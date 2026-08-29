#!/usr/bin/env python
"""W-Y3 addendum: (1) MC nulls for the best-LOOKING age-bucket cells (the
15-25d and 40-60d mirages) so the finding can kill them explicitly;
(2) grade the live young_mover_short ledger rows (forward hint) from the 1h
cache at the live 1d/6% geometry. No network."""
import json
import os
import random
import statistics as st
from pathlib import Path

import importlib.util as _il
HERE = os.path.dirname(os.path.abspath(__file__))
spec = _il.spec_from_file_location("geo", os.path.join(HERE, "W-Y3_geometry.py"))
geo = _il.module_from_spec(spec)
geo.main = lambda: None
spec.loader.exec_module(geo)

EPS, H1 = geo.EPS, geo.H1
rng = random.Random(20260722)

eq = [e for e in EPS if ":" in e["coin"]]

print("=== age-bucket cells vs same-coin nulls ===")
for lo, hi, h, s in [(15, 25, 3, 0.15), (15, 25, 2, 0.15), (15, 25, 5, 0.20),
                     (40, 60, 1, 0.06), (40, 60, 3, 0.15), (2, 15, 1, 0.06)]:
    sub = [e for e in eq if lo <= e["age"] < hi]
    tr = geo.run_cell_A(sub, h, s)
    if not tr:
        continue
    real = st.mean(v for _, v, _ in tr)
    p, null_mean = geo.mc_null_A(tr, h, s, rng)
    print(f"  age {lo}-{hi} h={h}d s={int(s*100)}%: n={len(tr):>2} "
          f"real={real*100:+.2f}% null={null_mean*100:+.2f}% "
          f"excess={(real-null_mean)*100:+.2f}% mc_p={p:.4f}")

print("\n=== live ledger forward grade (1d/6%, from 1h cache) ===")
LEDGER = str(Path(__file__).resolve().parents[3] / ".state" / "shadow_ledger" / "young_mover_short.jsonl")
rows = [json.loads(l) for l in open(LEDGER)]
res, unres = [], 0
for r in rows:
    coin, t0, px = r["coin"], int(r["signal_bar_t"]), float(r["entry_ref_px"])
    bars = H1.get(coin, [])
    idx = next((i for i, b in enumerate(bars) if b[0] >= t0), None)
    if idx is None or px <= 0:
        unres += 1
        continue
    stop_px = px * 1.06
    t_end = t0 + 24 * geo.HOUR
    exit_px, stopped, complete = None, False, False
    for j in range(idx, len(bars)):
        t, o, hgh = bars[j][0], bars[j][1], bars[j][2]
        if t >= t_end:
            complete = True
            break
        if o >= stop_px:
            exit_px, stopped, complete = o, True, True
            break
        if hgh >= stop_px:
            exit_px, stopped, complete = stop_px, True, True
            break
        exit_px = bars[j][4]
    if not complete or exit_px is None:
        unres += 1
        continue
    net = 1 - exit_px / px - 0.0050 + geo.funding_sum(coin, t0, t_end)
    res.append((coin, r["day"] if "day" in r else "", r["meta"].get("listing_days"),
                r["meta"].get("shadow"), net, stopped))
print(f"rows={len(rows)} resolved={len(res)} unresolved={unres}")
if res:
    vals = [v for *_, v, _ in res]
    live_only = [v for c, _, d, sh, v, _ in res if sh is False]
    print(f"ALL resolved: n={len(vals)} mean={st.mean(vals)*100:+.2f}% "
          f"win={sum(1 for v in vals if v>0)/len(vals)*100:.0f}% "
          f"stopped={sum(1 for *_, sp in res if sp)}/{len(res)}")
    if live_only:
        print(f"live-arm (shadow=false): n={len(live_only)} "
              f"mean={st.mean(live_only)*100:+.2f}%")
    for c, _, d, sh, v, sp in res:
        print(f"  {c:<14} age={d}d shadow={sh} net={v*100:+6.2f}%{' STOPPED' if sp else ''}")
