#!/usr/bin/env python
"""W-Y2: PIT counterfactual of the min_history_bars=60 floor.

Parses logs/trading_loop.log for `history_floor_preflight` blocks:
  "xyz:ZHIPU: pre-research history_floor_preflight (18d < 60d history) — skip AI research"
Dedup to (coin, UTC day) episodes; for each episode take the FIRST block
timestamp of that day, price at the next 1h bar's open (earliest actionable),
and compute forward 24h / 72h returns from the cached 1h candles.

This is what the blocked candidates DID — the floor's realized cost (if we
would have longed) / saving (if the moves were down). Direction the bot would
actually have taken is unknowable (AI research was skipped), so both sides
are reported. Costs: 50 bps round trip shown alongside raw.

Data: W-Y_cache_blocked_1h.json (W-Y0_fetch.py). Log is read-only.
"""
import json
import os
import re
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = str(Path(__file__).resolve().parents[3] / "logs" / "trading_loop.log")
HOURLY = json.load(open(os.path.join(HERE, "W-Y_cache_blocked_1h.json")))

PAT = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*?(xyz:[A-Z0-9]+): "
    r"pre-research history_floor_preflight \((\d+)d < \d+d history\)")

RT_COST = 0.0050  # 25 bps/side


def main():
    episodes = {}
    with open(LOG) as f:
        for line in f:
            m = PAT.match(line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            # log timestamps are machine-LOCAL; convert to UTC via local tz
            ts = ts.astimezone(timezone.utc)
            coin, age = m.group(2), int(m.group(3))
            key = (coin, ts.date().isoformat())
            if key not in episodes:
                episodes[key] = (ts, age)

    print(f"block episodes (coin, day): {len(episodes)}  "
          f"coins: {len({c for c, _ in episodes})}")

    rows_out = []
    for (coin, day), (ts, age) in sorted(episodes.items(), key=lambda kv: kv[1][0]):
        bars = HOURLY.get(coin, [])
        t_ms = ts.timestamp() * 1000
        entry_bar = next((b for b in bars if b[0] >= t_ms), None)
        if entry_bar is None:
            rows_out.append((coin, day, age, None, None, None))
            continue
        p0 = entry_bar[1]

        def fwd(hours):
            tgt = entry_bar[0] + hours * 3600_000
            cands = [b for b in bars if b[0] <= tgt]
            if not cands or cands[-1][0] < tgt - 3600_000 * 2:
                return None  # forward window not yet complete
            # need the window to actually extend `hours` ahead
            if bars[-1][0] < tgt:
                return None
            return cands[-1][4] / p0 - 1

        rows_out.append((coin, day, age, p0, fwd(24), fwd(72)))

    def agg(idx, label):
        vals = [r[idx] for r in rows_out if r[idx] is not None]
        n = len(vals)
        if not n:
            print(f"{label}: n=0")
            return
        mean = st.mean(vals)
        med = st.median(vals)
        pos = sum(1 for v in vals if v > 0) / n
        long_net = st.mean(v - RT_COST for v in vals)
        short_net = st.mean(-v - RT_COST for v in vals)
        print(f"{label}: n={n} mean={mean*100:+.2f}% median={med*100:+.2f}% "
              f"up-rate={pos*100:.0f}% | if-LONG-all net={long_net*100:+.2f}%/ep "
              f"| if-SHORT-all net={short_net*100:+.2f}%/ep")
        best = sorted(vals)[-3:]
        worst = sorted(vals)[:3]
        print(f"   best3={[f'{v*100:+.1f}%' for v in best]} "
              f"worst3={[f'{v*100:+.1f}%' for v in worst]}")

    agg(4, "fwd 24h")
    agg(5, "fwd 72h")

    print("\nper-episode detail (fwd24 / fwd72):")
    for coin, day, age, p0, f24, f72 in rows_out:
        f = lambda v: f"{v*100:+6.1f}%" if v is not None else "   n/a"
        print(f"  {day} {coin:<14} age={age:>2}d  {f(f24)}  {f(f72)}")

    out = os.path.join(HERE, "W-Y2_counterfactual.json")
    with open(out, "w") as fjson:
        json.dump([{"coin": c, "day": d, "age": a, "fwd24": x, "fwd72": y}
                   for c, d, a, _, x, y in rows_out], fjson, indent=1)
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
