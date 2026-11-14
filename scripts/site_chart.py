#!/usr/bin/env python3
"""Draw the landing page's bracket chart (site/index.html) as inline SVG.

Seeded, so the same input gives the same chart every run; tests/test_site.py
checks the page carries exactly this output. The chart is an illustration of
how a long bracket is built: a pullback, a base, a push up to the entry, then
the target and stop zones to the right with no candles after the entry, so it
shows no outcome and no market data.

    python3 scripts/site_chart.py            # print the SVG body
    python3 scripts/site_chart.py --write    # replace it in site/index.html
"""
from __future__ import annotations

import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "site" / "index.html"
SVG_OPEN = '<svg viewBox="0 0 720 500"'


def chart() -> str:
    rng = random.Random(28)
    PLOT_R = 640          # price axis starts here
    TOP, BOT = 28, 468
    N, STEP, CW = 40, 13, 8
    X0 = 14
    # price path in arbitrary units
    closes, p = [], 60.0
    for i in range(N):
        if i < 14:   drift = -0.9
        elif i < 28: drift = 0.05
        else:        drift = 0.75
        p += drift + rng.uniform(-1.6, 1.6)
        closes.append(p)
    opens = [closes[0] + 0.8] + closes[:-1]
    highs = [max(o, c) + rng.uniform(0.3, 1.8) for o, c in zip(opens, closes)]
    lows = [min(o, c) - rng.uniform(0.3, 1.8) for o, c in zip(opens, closes)]
    entry = closes[-1]
    stop = min(lows[-6:]) - 0.6
    r = entry - stop
    tp = entry + 2.5 * r
    lo = min(min(lows), stop) - 2
    hi = max(max(highs), tp) + 2
    def y(v): return round(BOT - (v - lo) / (hi - lo) * (BOT - TOP), 1)
    out = []
    # grid
    for gy in range(TOP, BOT + 1, 44):
        out.append(f'<line class="g" x1="0" y1="{gy}" x2="{PLOT_R}" y2="{gy}"/>')
    for gx in range(0, PLOT_R + 1, 64):
        out.append(f'<line class="g" x1="{gx}" y1="{TOP}" x2="{gx}" y2="{BOT}"/>')
    # bracket zones from the last candle to the axis
    zx = X0 + (N - 1) * STEP + CW + 6
    out.append(f'<rect class="zone-tp" x="{zx}" y="{y(tp)}" width="{PLOT_R - zx}" height="{round(y(entry) - y(tp), 1)}"/>')
    out.append(f'<rect class="zone-sl" x="{zx}" y="{y(entry)}" width="{PLOT_R - zx}" height="{round(y(stop) - y(entry), 1)}"/>')
    # candles
    for i in range(N):
        x = X0 + i * STEP
        o, c, h, l = opens[i], closes[i], highs[i], lows[i]
        cls = "up" if c >= o else "dn"
        cx = x + CW / 2
        out.append(f'<g class="c {cls}" style="--i:{i}"><line x1="{cx}" y1="{y(h)}" x2="{cx}" y2="{y(l)}"/>'
                   f'<rect x="{x}" y="{min(y(o), y(c))}" width="{CW}" height="{max(1.0, round(abs(y(o) - y(c)), 1))}"/></g>')
    # level lines and right-axis tags
    for name, v, cls in (("TP", tp, "tp"), ("ENTRY", entry, "en"), ("SL", stop, "sl")):
        yy = y(v)
        out.append(f'<line class="lvl {cls}" x1="{zx if cls != "en" else 0}" y1="{yy}" x2="{PLOT_R}" y2="{yy}"/>')
        out.append(f'<g class="tag {cls}"><rect x="{PLOT_R + 4}" y="{yy - 10}" width="72" height="20" rx="3"/>'
                   f'<text x="{PLOT_R + 40}" y="{yy + 4}">{name}</text></g>')
    # R:R label in the target zone, last-price dot
    mid = y(entry + 1.25 * r)
    out.append(f'<text class="rr" x="{(zx + PLOT_R) / 2}" y="{mid + 4}">R:R 2.5</text>')
    out.append(f'<text class="rr sl" x="{(zx + PLOT_R) / 2}" y="{round((y(entry) + y(stop)) / 2 + 4, 1)}">1R</text>')
    out.append(f'<circle class="last" cx="{X0 + (N - 1) * STEP + CW / 2}" cy="{y(entry)}" r="3.5"/>')
    return "\n".join(out)


def splice(page: str, body: str) -> str:
    """The page with everything between the chart's <svg ...> tag and its
    </svg> replaced by body."""
    rx = re.compile(r'(' + re.escape(SVG_OPEN) + r'[^>]*>\n)(.*?)(\n\s*</svg>)', re.S)
    if not rx.search(page):
        raise SystemExit("site/index.html has no bracket chart <svg>")
    return rx.sub(lambda m: m.group(1) + body + m.group(3), page, count=1)


def main(argv: list) -> int:
    body = chart()
    if "--write" in argv:
        PAGE.write_text(splice(PAGE.read_text(encoding="utf-8"), body), encoding="utf-8")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
