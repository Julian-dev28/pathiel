"""W-M4 — our-interaction audit: for every coin-day the loop's scanner flagged
as a mover (last 14 days), what did the bot DO, and what did the coin do NEXT?

PIT method:
  * Episodes: a coin's appearance in a "[scan] crypto-movers" / "HIP-3-movers"
    line, grouped: a new episode starts after >=24h absence from mover lines.
    t0 = first appearance (episode start), pct0 = its 24h-change reading then.
  * Bot actions for that coin in [t0, t0+24h]: pre-research gate skips,
    re-research throttles, "Researching ..." -> "Verdict: X, Confidence: Y"
    (+ optional "Trade result: {...}").
  * Outcome class (precedence): executed > verdict_long/short_blocked >
    ai_pass > dominant pre-research gate > throttle_only > never_touched.
  * Forward return: fetched 1h closes (one candleSnapshot per distinct mover
    coin, cached once — same authorization/pacing precedent as W-H0/W-Y0).
    p(t) = close of last fully-closed 1h bar at t;
    fwd24 = p(t0+24h)/p(t0)-1, fwd72 = p(t0+72h)/p(t0)-1. N/A when data absent
    (delisted coin or t0 too recent).

Output: scratchpad/W-M4_results.json + per-gate table on stdout.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import re
import statistics
import sys
import time
from bisect import bisect_right
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LOG = REPO / "logs" / "trading_loop.log"
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/"
    "f77b77de-96c2-4bf2-a574-1fd5aeebb7f2/scratchpad")
CACHE = SCRATCH / "W-M4_movers_1h.json"

WINDOW_DAYS = 14
HOUR_MS = 3_600_000

RE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ ")
RE_MOVERS = re.compile(r"\[scan\] (?:crypto|HIP-3)-movers: (.+)$")
RE_ITEM = re.compile(r"([A-Za-z0-9:_@]+) ([+-][\d.]+)%")
RE_GATE = re.compile(r"INFO:__main__:([A-Za-z0-9:_@]+): pre-research ([a-z_]+)")
RE_THROTTLE = re.compile(r"INFO:__main__:([A-Za-z0-9:_@]+): re-research throttle")
RE_RESEARCH = re.compile(r"INFO:__main__:Researching ([A-Za-z0-9:_@]+) \(trigger ([\d.\-]+)")
RE_VERDICT = re.compile(r"INFO:__main__:Verdict: (\w+), Confidence: ([\d.]+)")
RE_TRADE = re.compile(r"INFO:__main__:Trade result: (\{.*\})$")


def to_ms(s: str) -> int:
    """Log lines are local time -> epoch ms."""
    return int(dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
               .astimezone().timestamp() * 1000)


def parse_log():
    movers = []       # (t_ms, coin, pct)
    gates = []        # (t_ms, coin, gate)
    throttles = []    # (t_ms, coin)
    researches = []   # {t, coin, trigger, verdict, conf, executed, reason}
    pending = None
    for line in LOG.read_text(errors="replace").splitlines():
        m = RE_TS.match(line)
        if not m:
            continue
        t = to_ms(m.group(1))
        mm = RE_MOVERS.search(line)
        if mm:
            for coin, pct in RE_ITEM.findall(mm.group(1)):
                movers.append((t, coin, float(pct)))
            continue
        g = RE_GATE.search(line)
        if g:
            gates.append((t, g.group(1), g.group(2)))
            continue
        th = RE_THROTTLE.search(line)
        if th:
            throttles.append((t, th.group(1)))
            continue
        r = RE_RESEARCH.search(line)
        if r:
            pending = {"t": t, "coin": r.group(1), "trigger": float(r.group(2)),
                       "verdict": None, "conf": None, "executed": None,
                       "reason": None}
            researches.append(pending)
            continue
        v = RE_VERDICT.search(line)
        if v and pending is not None and pending["verdict"] is None:
            pending["verdict"], pending["conf"] = v.group(1), float(v.group(2))
            continue
        tr = RE_TRADE.search(line)
        if tr and pending is not None:
            try:
                d = ast.literal_eval(tr.group(1))
                pending["executed"] = bool(d.get("executed"))
                pending["reason"] = d.get("reason")
            except (ValueError, SyntaxError):
                pass
            pending = None
    return movers, gates, throttles, researches


def build_episodes(movers, t_min):
    by_coin: dict[str, list] = {}
    for t, coin, pct in movers:
        by_coin.setdefault(coin, []).append((t, pct))
    eps = []
    for coin, rows in by_coin.items():
        rows.sort()
        last = -10**18
        cur = None
        for t, pct in rows:
            if t - last >= 24 * HOUR_MS:
                if cur:
                    eps.append(cur)
                cur = {"coin": coin, "t0": t, "pct0": pct, "max_pct": pct}
            else:
                cur["max_pct"] = max(cur["max_pct"], pct)
            last = t
        if cur:
            eps.append(cur)
    return sorted([e for e in eps if e["t0"] >= t_min], key=lambda e: e["t0"])


def fetch_candles(coins: list[str]) -> dict:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [c for c in coins if c not in cache]
    if todo:
        sys.path.insert(0, str(REPO))
        from hermes_trader.client.hl_client import fetch_hl_candles
        for i, coin in enumerate(todo):
            try:
                cs = fetch_hl_candles(coin, "1h", 450)
                cache[coin] = [[c.t, c.c] for c in cs]
            except Exception as exc:                       # delisted etc.
                print(f"  {coin}: fetch failed ({exc})")
                cache[coin] = []
            if (i + 1) % 20 == 0:
                CACHE.write_text(json.dumps(cache))
                print(f"  fetched {i+1}/{len(todo)}", flush=True)
            time.sleep(0.3)
        CACHE.write_text(json.dumps(cache))
    return cache


def price_at(series, t_ms):
    """close of the last FULLY CLOSED 1h bar at t_ms; None if out of range."""
    if not series:
        return None
    ts = [r[0] for r in series]
    j = bisect_right(ts, t_ms - HOUR_MS) - 1     # bar open <= t-1h => closed
    if j < 0:
        return None
    if t_ms - ts[j] > 6 * HOUR_MS:               # stale hole
        return None
    return series[j][1]


def classify(ep, gates, throttles, researches):
    t0, t1, coin = ep["t0"], ep["t0"] + 24 * HOUR_MS, ep["coin"]
    g = [x for x in gates if x[1] == coin and t0 <= x[0] < t1]
    th = [x for x in throttles if x[1] == coin and t0 <= x[0] < t1]
    rs = [r for r in researches if r["coin"] == coin and t0 <= r["t"] < t1]
    ep["n_gate"], ep["n_throttle"], ep["n_research"] = len(g), len(th), len(rs)
    ep["verdicts"] = [r["verdict"] for r in rs]
    if any(r["executed"] for r in rs):
        ep["outcome"] = "EXECUTED"
    elif any(r["verdict"] in ("LONG", "SHORT") for r in rs):
        r0 = next(r for r in rs if r["verdict"] in ("LONG", "SHORT"))
        ep["outcome"] = f"verdict_{r0['verdict']}_blocked"
    elif any(r["verdict"] == "PASS" for r in rs):
        ep["outcome"] = "ai_pass"
    elif g:
        counts: dict[str, int] = {}
        for _, _, gate in g:
            counts[gate] = counts.get(gate, 0) + 1
        ep["outcome"] = "gate:" + max(counts, key=counts.get)
    elif th:
        ep["outcome"] = "throttle_only"
    else:
        ep["outcome"] = "never_touched"
    return ep


def main() -> None:
    movers, gates, throttles, researches = parse_log()
    now_ms = int(time.time() * 1000)
    t_min = now_ms - WINDOW_DAYS * 24 * HOUR_MS
    eps = build_episodes(movers, t_min)
    coins = sorted({e["coin"] for e in eps})
    print(f"{len(eps)} mover episodes across {len(coins)} coins "
          f"since {dt.datetime.fromtimestamp(t_min/1000)}")
    candles = fetch_candles(coins)

    for ep in eps:
        classify(ep, gates, throttles, researches)
        s = candles.get(ep["coin"]) or []
        p0 = price_at(s, ep["t0"])
        p24 = price_at(s, ep["t0"] + 24 * HOUR_MS)
        p72 = price_at(s, ep["t0"] + 72 * HOUR_MS)
        ep["fwd24"] = (p24 / p0 - 1) if (p0 and p24 and ep["t0"] + 24 * HOUR_MS
                                         <= now_ms) else None
        ep["fwd72"] = (p72 / p0 - 1) if (p0 and p72 and ep["t0"] + 72 * HOUR_MS
                                         <= now_ms) else None

    (SCRATCH / "W-M4_results.json").write_text(json.dumps(eps, default=str))

    # per-outcome table
    groups: dict[str, list] = {}
    for ep in eps:
        groups.setdefault(ep["outcome"], []).append(ep)
    print(f"\n{'outcome':38}{'n':>4}{'n24':>5}{'mean24':>9}{'med24':>9}"
          f"{'%pos24':>8}{'n72':>5}{'mean72':>9}")
    def _row(name, rows):
        f24 = [e["fwd24"] for e in rows if e["fwd24"] is not None]
        f72 = [e["fwd72"] for e in rows if e["fwd72"] is not None]
        m24 = f"{100*statistics.mean(f24):.2f}" if f24 else "-"
        md24 = f"{100*statistics.median(f24):.2f}" if f24 else "-"
        pp = f"{100*sum(1 for x in f24 if x>0)/len(f24):.0f}" if f24 else "-"
        m72 = f"{100*statistics.mean(f72):.2f}" if f72 else "-"
        print(f"{name:38}{len(rows):>4}{len(f24):>5}{m24:>9}{md24:>9}"
              f"{pp:>8}{len(f72):>5}{m72:>9}")
    for name in sorted(groups, key=lambda k: -len(groups[k])):
        _row(name, groups[name])
    _row("ALL", eps)

    # the ones that ripped: top forward winners and what we did
    ranked = sorted((e for e in eps if e["fwd24"] is not None),
                    key=lambda e: -e["fwd24"])
    print("\ntop-12 forward winners (fwd24) and what the bot did:")
    for e in ranked[:12]:
        print(f"  {dt.datetime.fromtimestamp(e['t0']/1000):%m-%d %H:%M} "
              f"{e['coin']:14} seen@{e['pct0']:+.1f}% fwd24={100*e['fwd24']:+.1f}% "
              f"fwd72={('%+.1f%%' % (100*e['fwd72'])) if e['fwd72'] is not None else 'n/a':>8} "
              f"-> {e['outcome']} (research={e['n_research']}, gates={e['n_gate']})")


if __name__ == "__main__":
    main()
