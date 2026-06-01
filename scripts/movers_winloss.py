#!/usr/bin/env python3
"""Top winners + losers (crypto & HIP-3), with our funnel attribution.

For each big mover: 24h %, whether WE traded it, and if not, WHERE in the funnel
it died (no-trigger / TA-rejected / AI-pass / gate-blocked). This is the
strategy-learning loop: see what ripped, see why we caught or missed it.
"""
import json, os, sys
from collections import defaultdict
from hermes_trader.client.universe import get_universe

def _pct24(m):
    mid = m.get("midPx", 0) or 0; prev = m.get("prevDayPx", 0) or 0
    return (mid - prev) / prev * 100 if prev > 0 else 0.0

def _funnel_outcome(coin, evs):
    """Walk recent session events for this coin -> furthest funnel stage."""
    scanned = traded = None; ta = ai = blocked = None
    for e in evs:
        if e.get("coin") != coin and e.get("event") != "scan":
            continue
        ev = e.get("event")
        if ev == "scan":
            for cs in e.get("coin_scores", []) or []:
                if cs.get("coin") == coin:
                    scanned = cs.get("score")
        elif ev == "ta_skip" and e.get("coin") == coin:
            ta = e.get("signal")
        elif ev == "research" and e.get("coin") == coin:
            ai = (e.get("verdict"), e.get("confidence"))
        elif ev == "execute" and e.get("coin") == coin:
            if e.get("executed"): traded = True
            else: blocked = (e.get("blocked_by") or e.get("regime_via"))
    if traded: return "TRADED ✓"
    if blocked: return f"GATE-BLOCKED ({str(blocked)[:40]})"
    if ai: return f"AI {ai[0]} (conf {ai[1]})"
    if ta: return f"TA {ta}"
    if scanned is not None: return f"scanned, no-trigger (score {scanned})"
    return "not scanned (outside budget / no fresh signal)"

def main():
    slog = os.path.expanduser("~/.hermes-trader-session-log.jsonl")
    evs = []
    for ln in open(slog, errors="replace"):
        try: evs.append(json.loads(ln).get("data", {}) or json.loads(ln))
        except Exception: pass
    # keep last ~3000 events for speed
    evs = evs[-3000:]
    u = get_universe(include_hip3=True)
    # Only real, liquid perps. Exclude @-prefixed spot pairs (type=="spot") and
    # illiquid names (<$1M 24h vol) whose prevDayPx yields garbage % moves.
    MIN_VOL = 1_000_000
    def real(m):
        return (m.get("type") == "perp" and not m.get("coin","").startswith("@")
                and m.get("midPx", 0) and m.get("prevDayPx", 0)
                and m.get("dayNtlVlm", 0) >= MIN_VOL)
    crypto = [m for m in u if real(m) and ":" not in m.get("coin","")]
    hip3   = [m for m in u if real(m) and ":" in m.get("coin","")]
    for label, pool in (("CRYPTO", crypto), ("HIP-3", hip3)):
        ranked = sorted(pool, key=_pct24, reverse=True)
        winners = ranked[:3]; losers = ranked[-3:][::-1]
        print(f"\n=== {label} TOP WINNERS ===")
        for m in winners:
            c=m["coin"]; print(f"  {c:<14} {_pct24(m):+6.1f}%  -> {_funnel_outcome(c, evs)}")
        print(f"=== {label} TOP LOSERS ===")
        for m in losers:
            c=m["coin"]; print(f"  {c:<14} {_pct24(m):+6.1f}%  -> {_funnel_outcome(c, evs)}")

if __name__ == "__main__":
    main()
