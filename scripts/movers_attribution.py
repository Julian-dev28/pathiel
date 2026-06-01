#!/usr/bin/env python3
"""Top-movers trade-attribution report (READ-ONLY — no orders, no writes).

Answers: of today's biggest movers (crypto + HIP-3), which ones did we trade,
and for the ones we didn't — WHERE in the funnel did they die?

Funnel: scan -> trigger(composite score) -> TA filter -> AI research -> risk gates -> execute.

Sources (all read-only):
  - logs/trading_loop.log         : the per-scan "crypto-movers:" / "HIP-3-movers:" lines (24h%)
  - ~/.hermes-trader-session-log.jsonl (or logs/*.jsonl): scan / ta_skip / research / execute events
  - .agent-memory.json            : trades (did we EVER trade it)

Usage: python3 scripts/movers_attribution.py [--hours 6]
"""
import json, os, re, sys, time, glob

HOURS = 6
if "--hours" in sys.argv:
    try: HOURS = float(sys.argv[sys.argv.index("--hours")+1])
    except Exception: pass
SINCE_MS = int((time.time() - HOURS*3600) * 1000)

def find_session_log():
    cands = [os.path.expanduser("~/.hermes-trader-session-log.jsonl")]
    cands += sorted(glob.glob("logs/*.jsonl"))
    cands += sorted(glob.glob("*.jsonl"))
    for c in cands:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            return c
    return None

def parse_movers(logpath, n_lines=4000):
    """Return (crypto_movers, hip3_movers) from the most recent scan lines:
    list of (coin, pct_str)."""
    cryp, hip3 = [], []
    if not os.path.exists(logpath):
        return cryp, hip3
    lines = open(logpath, errors="replace").read().splitlines()[-n_lines:]
    def grab(tag):
        out = []
        for ln in reversed(lines):
            if tag in ln:
                seg = ln.split(tag, 1)[1].strip()
                for m in re.finditer(r'([A-Za-z0-9:_]+)\s+([+-]?[0-9.]+%)', seg):
                    out.append((m.group(1), m.group(2)))
                if out:
                    return out
        return out
    return grab("crypto-movers:"), grab("HIP-3-movers:")

def load_events(slog):
    evs = []
    if not slog: return evs
    for ln in open(slog, errors="replace"):
        ln = ln.strip()
        if not ln: continue
        try: e = json.loads(ln)
        except Exception: continue
        evs.append(e)
    return evs

def ts_of(e):
    for k in ("ts","ts_ms","time","timestamp"):
        if k in e:
            v = e[k]
            try:
                v = float(v)
                return v if v > 1e12 else v*1000
            except Exception: pass
    return None

def attribute(coin, evs):
    """Walk events for this coin; return latest funnel facts."""
    scan_score = None; scan_trigs = None
    ta = None; ai = None; exe = None
    for e in evs:
        ev = e.get("event")
        if ev == "scan":
            for cs in e.get("coin_scores", []) or []:
                if cs.get("coin") == coin:
                    scan_score = cs.get("score"); scan_trigs = cs.get("triggers")
        elif e.get("coin") == coin:
            if ev == "ta_skip": ta = e.get("signal")
            elif ev == "research": ai = (e.get("verdict"), e.get("confidence"))
            elif ev == "execute": exe = (e.get("executed"), e.get("blocked_by") or e.get("detail"))
            elif ev == "ai_close": exe = ("CLOSE", e.get("detail"))
    # Verdict on where it died
    if exe and exe[0] is True: where = "TRADED ✓"
    elif exe and exe[0] not in (None, False): where = f"action={exe[0]}"
    elif exe and exe[0] is False: where = f"GATE-BLOCKED: {exe[1]}"
    elif ai is not None: where = f"AI {ai[0]} (conf {ai[1]})"
    elif ta is not None: where = f"TA {ta}"
    elif scan_score is not None: where = f"TRIGGERED no-trade (score {scan_score})"
    else: where = "no trigger (below threshold OR outside scan budget)"
    return scan_score, scan_trigs, ta, ai, exe, where

def traded_ever(coin, mem):
    tr = mem.get("trades") or []
    return sum(1 for t in tr if (t.get("coin")==coin))

def main():
    slog = find_session_log()
    evs_all = load_events(slog)
    # window filter (keep events with no ts too — better to over-include)
    evs = [e for e in evs_all if (ts_of(e) is None or ts_of(e) >= SINCE_MS)]
    cryp, hip3 = parse_movers("logs/trading_loop.log")
    try: mem = json.load(open(".agent-memory.json"))
    except Exception: mem = {}

    print(f"=== MOVERS ATTRIBUTION (last {HOURS}h) ===")
    print(f"session-log: {slog or 'NOT FOUND'}  ({len(evs_all)} events, {len(evs)} in window)")
    for label, movers in (("CRYPTO movers", cryp), ("HIP-3 movers", hip3)):
        print(f"\n--- {label} ---")
        if not movers:
            print("  (none parsed from trading_loop.log)")
            continue
        print(f"  {'coin':<14}{'24h%':>8}  {'ever':>4}  where-it-died / outcome")
        for coin, pct in movers:
            n = traded_ever(coin, mem)
            _,_,_,_,_, where = attribute(coin, evs)
            print(f"  {coin:<14}{pct:>8}  {n:>4}  {where}")
    print("\nlegend: 'ever'=lifetime trades on this coin in memory; "
          "where-it-died = furthest funnel stage reached in the window.")

if __name__ == "__main__":
    main()
