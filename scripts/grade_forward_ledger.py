#!/usr/bin/env python3
"""Grade a book's LIVE forward ledger — what it actually called, vs what happened.

WHY THIS EXISTS
.state/grading.json was 44 bytes. The books have been recording forward signals
for days and nothing was scoring them, so the only evidence for the live book
was its own backtest. A strategy that grades itself only in-sample is a strategy
with no evidence at all.

THE TRAP THIS AVOIDS, AND IT IS NOT SUBTLE
The books re-record a candidate EVERY cycle it still qualifies. xs_reversal's
ledger held 720 rows that were 11 decisions: xyz:BE appears 181 times and
xyz:HOOD 169. Grading rows instead of decisions gives a sample 65x too large and
a bootstrap p-value to match — the first pass of this script reported n=514 and
p=0.0000 off six real bets. One BET is one (coin, entry day); the repeats are
one decision observed many times, not many decisions.

    python scripts/grade_forward_ledger.py [book]
"""
import json, statistics as st, sys
from datetime import datetime, timezone
sys.path.insert(0, "research/regime_2026_09")
from C1_session_structure import load_panel
from H1_funding_carry import DAY_MS, SLIP_PCT, _f

BOOK = sys.argv[1] if len(sys.argv) > 1 else "xs_reversal"
panel = load_panel()
rows = [json.loads(l) for l in open(f".state/shadow_ledger/{BOOK}.jsonl") if l.strip()]

# One BET = one (coin, entry day). The book re-records a live candidate every
# cycle it still qualifies; those repeats are one decision, not 85.
first = {}
for r in rows:
    d = datetime.fromtimestamp(int(r["ts"])/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    first.setdefault((r["coin"], d), r)
print(f"  {len(rows)} ledger rows  ->  {len(first)} DISTINCT bets\n")

out = []
for (coin, day), r in sorted(first.items(), key=lambda kv: kv[1]["ts"]):
    s = panel.get(coin); px0 = float(r.get("entry_ref_px") or 0)
    if not s or px0 <= 0: continue
    tgt = int(r["ts"]) + float(r.get("horizon_days") or 1.0) * DAY_MS
    later = [u for u in sorted(s) if u >= tgt]
    if not later: 
        out.append((coin, day, None)); continue
    px1 = _f(s[later[0]], "px")
    out.append((coin, day, (-(px1/px0 - 1) * 100 - SLIP_PCT) if px1 else None))

print(f"  {'coin':<14}{'entry day':<12}{'24h short ret':>14}")
for coin, day, ret in out:
    print(f"  {coin:<14}{day:<12}{'  pending' if ret is None else f'{ret:>13.2f}%'}")
done = [r for _, _, r in out if r is not None]
if done:
    print(f"\n  n={len(done)} resolved   EV {st.mean(done):+.3f}%   "
          f"win {100*sum(1 for x in done if x>0)/len(done):.0f}%")
    print("  backtest said +1.408% pooled (n=2562, p<0.0001)")
