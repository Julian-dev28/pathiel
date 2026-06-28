import json, statistics as st
from collections import defaultdict

ev = json.load(open('audit_events.json'))
cache = json.load(open('candle_cache.json'))  # coin -> [[t,o,h,l,c,v],...]

H = 3600000

def fwd(coin, ts, hours):
    """ref price at event, fwd max-up/down and end-return over `hours`."""
    cs = cache.get(coin) or []
    if not cs:
        return None
    # ref = close of most recent candle with t<=ts; fallback open of first after
    ref = None
    after = []
    for t, o, h, l, c, v in cs:
        if t <= ts:
            ref = c
        else:
            after.append((t, o, h, l, c, v))
    if ref is None or ref <= 0:
        # use open of first candle after ts
        if not after:
            return None
        ref = after[0][1]
    win = [r for r in after if r[0] <= ts + hours * H]
    if not win:
        return None
    maxup = max(r[2] for r in win) / ref - 1
    maxdn = min(r[3] for r in win) / ref - 1
    endret = win[-1][4] / ref - 1
    return {'ref': ref, 'maxup': maxup, 'maxdn': maxdn, 'ret': endret, 'n': len(win)}

def summ(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return {
        'n': len(vals),
        'mean': sum(vals)/len(vals),
        'median': st.median(vals),
    }

# ---- A) research verdicts ----
research = ev['research']
by_v = defaultdict(list)
for r in research:
    f72 = fwd(r['coin'], r['ts'], 72)
    f24 = fwd(r['coin'], r['ts'], 24)
    if f72 is None:
        continue
    rec = {'coin': r['coin'], 'verdict': r['verdict'], 'conf': r['confidence'] or 0,
           'maxup72': f72['maxup'], 'maxdn72': f72['maxdn'], 'ret72': f72['ret'],
           'ret24': f24['ret'] if f24 else None, 'maxup24': f24['maxup'] if f24 else None}
    by_v[r['verdict']].append(rec)

print('=== A) RESEARCH VERDICT FORWARD RETURNS (1h candles, ref=event price) ===')
for v in ('PASS', 'LONG', 'SHORT'):
    rows = by_v[v]
    if not rows:
        continue
    n = len(rows)
    print(f'\n-- {v}  (n={n}) --')
    for k in ('ret24', 'ret72', 'maxup72', 'maxdn72'):
        s = summ(rows, k)
        if s:
            print(f'  {k:8s} mean={s["mean"]*100:+6.2f}%  median={s["median"]*100:+6.2f}%')
    # thresholds
    moon8 = sum(1 for r in rows if r['maxup72'] >= 0.08)
    moon15 = sum(1 for r in rows if r['maxup72'] >= 0.15)
    up72 = sum(1 for r in rows if r['ret72'] > 0)
    print(f'  maxup72>=8%: {moon8}/{n} = {moon8/n*100:.1f}%   >=15%: {moon15}/{n} = {moon15/n*100:.1f}%')
    print(f'  ret72>0 (ended up): {up72}/{n} = {up72/n*100:.1f}%')

# AI directional hit-rate
long_rows = by_v['LONG']; short_rows = by_v['SHORT']; pass_rows = by_v['PASS']
long_hit = sum(1 for r in long_rows if r['ret72'] > 0) / len(long_rows) if long_rows else 0
short_hit = sum(1 for r in short_rows if r['ret72'] < 0) / len(short_rows) if short_rows else 0
print('\n=== AI DIRECTIONAL HIT-RATE (72h end-return) ===')
print(f'  LONG correct (ended up):   {long_hit*100:.1f}%  (n={len(long_rows)})')
print(f'  SHORT correct (ended down):{short_hit*100:.1f}%  (n={len(short_rows)})')

# PASS opportunity cost: how often PASS'd coins ran, and avg forward move
pn = len(pass_rows)
pass_mean_up = sum(r['maxup72'] for r in pass_rows)/pn
pass_mean_ret = sum(r['ret72'] for r in pass_rows)/pn
moon8 = [r for r in pass_rows if r['maxup72'] >= 0.08]
moon15 = [r for r in pass_rows if r['maxup72'] >= 0.15]
print('\n=== PASS OPPORTUNITY COST (these were TA-confirmed, AI vetoed the long) ===')
print(f'  PASS count: {pn}')
print(f'  avg fwd max-up 72h: {pass_mean_up*100:+.2f}%   avg end-ret 72h: {pass_mean_ret*100:+.2f}%')
print(f'  PASS-then-mooned >=8%:  {len(moon8)}/{pn} = {len(moon8)/pn*100:.1f}%  (avg maxup {sum(r["maxup72"] for r in moon8)/len(moon8)*100:.1f}%)' if moon8 else '  none>=8%')
print(f'  PASS-then-mooned >=15%: {len(moon15)}/{pn} = {len(moon15)/pn*100:.1f}%')

# Compare PASS vs LONG forward maxup -- if PASS coins ran as much as LONG, AI added no skill
print('\n=== PASS vs LONG forward maxup72 (is AI separating winners?) ===')
print(f'  LONG mean maxup72: {sum(r["maxup72"] for r in long_rows)/len(long_rows)*100:+.2f}%')
print(f'  PASS mean maxup72: {pass_mean_up*100:+.2f}%')
print(f'  LONG mean ret72:   {sum(r["ret72"] for r in long_rows)/len(long_rows)*100:+.2f}%')
print(f'  PASS mean ret72:   {pass_mean_ret*100:+.2f}%')

# top PASS misses
miss = sorted(pass_rows, key=lambda r: -r['maxup72'])[:15]
print('\n=== TOP 15 PASS MISSES (biggest fwd max-up after AI PASS) ===')
for m in miss:
    print(f'  {m["coin"]:16s} maxup72 {m["maxup72"]*100:+6.1f}%  ret72 {m["ret72"]*100:+6.1f}%  maxdn72 {m["maxdn72"]*100:+6.1f}%')

# ---- B) TA skips ----
print('\n\n=== B) TA-SKIP FORWARD RETURNS ===')
skips = ev['skips']
srows = []
for s in skips:
    f72 = fwd(s['coin'], s['ts'], 72)
    f24 = fwd(s['coin'], s['ts'], 24)
    if f72 is None:
        continue
    srows.append({'coin': s['coin'], 'sig': s['signal'], 'maxup72': f72['maxup'],
                  'maxdn72': f72['maxdn'], 'ret72': f72['ret']})
by_sig = defaultdict(list)
for r in srows:
    by_sig[r['sig']].append(r)
for sig, rows in sorted(by_sig.items(), key=lambda x: -len(x[1])):
    n = len(rows)
    mu = sum(r['maxup72'] for r in rows)/n
    rt = sum(r['ret72'] for r in rows)/n
    m8 = sum(1 for r in rows if r['maxup72'] >= 0.08)
    m15 = sum(1 for r in rows if r['maxup72'] >= 0.15)
    quiet = sum(1 for r in rows if r['maxup72'] < 0.08)
    print(f'\n-- {sig} (n={n}) --')
    print(f'  avg maxup72 {mu*100:+.2f}%  avg ret72 {rt*100:+.2f}%')
    print(f'  ran >=8%: {m8}/{n}={m8/n*100:.1f}%  >=15%: {m15}/{n}={m15/n*100:.1f}%  stayed quiet(<8%): {quiet}/{n}={quiet/n*100:.1f}%')

alln = len(srows)
allm8 = sum(1 for r in srows if r['maxup72'] >= 0.08)
print(f'\n  ALL TA-skips: ran>=8% {allm8}/{alln}={allm8/alln*100:.1f}%  -> skip-correct(stayed quiet) {(alln-allm8)/alln*100:.1f}%')

# top skip misses
smiss = sorted(srows, key=lambda r: -r['maxup72'])[:10]
print('\n=== TOP 10 TA-SKIP MISSES ===')
for m in smiss:
    print(f'  {m["coin"]:16s} {m["sig"]:24s} maxup72 {m["maxup72"]*100:+6.1f}%  ret72 {m["ret72"]*100:+6.1f}%')

# save processed for md
json.dump({'by_v': {k: v for k, v in by_v.items()}, 'srows': srows}, open('audit_processed.json', 'w'))
