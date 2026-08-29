#!/usr/bin/env python
"""W-Y4 step 1: extract the young history-floor-blocked cohort from the live log.

Parses logs/trading_loop.log for `history_floor_preflight` blocks (the exact
population the LIVE young_mover_short book trades) and dedupes to (coin, UTC
day) episodes keyed on the FIRST block timestamp of the day — same convention
as W-Y2 and as the -2.71%/next-day retrospective that motivated the book.

Unlike W-Y2's regex this one also catches BARE (crypto) coins (CASHCAT), which
the live book records at zero capital. Log timestamps are machine-LOCAL and
converted to UTC via the local tz.

Output: W-Y4_episodes.json
  [{coin, day, block_ts_ms, age_days, is_xyz}]  sorted by block_ts_ms
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = str(Path(__file__).resolve().parents[3] / "logs" / "trading_loop.log")
OUT = os.path.join(HERE, "W-Y4_episodes.json")

PAT = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO:[^:]+:"
    r"((?:[a-z0-9]+:)?[A-Za-z0-9._-]+): "
    r"pre-research history_floor_preflight \((\d+)d < \d+d history\)")


def main():
    episodes = {}
    n_lines = 0
    with open(LOG, errors="replace") as f:
        for line in f:
            m = PAT.match(line)
            if not m:
                continue
            n_lines += 1
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            ts = ts.astimezone(timezone.utc)  # log is machine-local
            coin, age = m.group(2), int(m.group(3))
            key = (coin, ts.date().isoformat())
            if key not in episodes or ts < episodes[key][0]:
                episodes[key] = (ts, age)

    rows = [{"coin": c, "day": d,
             "block_ts_ms": int(ts.timestamp() * 1000),
             "age_days": age, "is_xyz": ":" in c}
            for (c, d), (ts, age) in episodes.items()]
    rows.sort(key=lambda r: r["block_ts_ms"])

    coins = sorted({r["coin"] for r in rows})
    xyz = [r for r in rows if r["is_xyz"]]
    print(f"block lines matched: {n_lines}")
    print(f"episodes (coin, UTC day): {len(rows)}  coins: {len(coins)}")
    print(f"  xyz episodes: {len(xyz)} across {len({r['coin'] for r in xyz})} coins")
    print(f"  bare (crypto) episodes: {len(rows) - len(xyz)}")
    print(f"  date range: {rows[0]['day']} .. {rows[-1]['day']}")
    print("coins:", ", ".join(coins))
    with open(OUT, "w") as f:
        json.dump(rows, f, indent=1)
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
