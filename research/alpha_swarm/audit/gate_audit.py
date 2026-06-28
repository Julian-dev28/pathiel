#!/usr/bin/env python3
"""gate_audit.py — forensic: were our PRE-RESEARCH / entry skips RIGHT or did they kill alpha?
Read-only. Measures forward return after each skip for a LONG the bot was considering.
WRONG skip = coin then mooned (big up move missed). RIGHT skip = flat/down.
"""
import os, json, sys, collections, statistics, time

ROOT='/Users/julian_dev/Documents/code/hermes-trader'
LOG=os.path.expanduser('~/.hermes-trader-session-log.jsonl')
os.chdir(ROOT)
for line in open(os.path.join(ROOT,'.env.local')):
    line=line.strip()
    if not line or line.startswith('#') or '=' not in line: continue
    k,v=line.split('=',1); os.environ[k.strip()]=v.strip().strip('"').strip("'")
sys.path.insert(0,ROOT)
from hermes_trader.client.hl_client import fetch_hl_candles

DAY=86400000; HR=3600000
LAST_TS=1782609337982
WINDOW_START=LAST_TS-14*DAY   # last 14 days

# ---- 1. collect skip events ----
# (bucket, coin, ts) ; dedupe coin+bucket+hour
def pf_bucket(reason):
    if not reason: return None
    p=str(reason).split(' ')[0].split(':')[0]
    return p

# execute-detail buckets we treat as SIGNAL/ENTRY gates (longs)
EXEC_GATES={'runner_gate_blocked','trend_filter','signal_veto','insufficient_free_margin',
            'loss_cooldown','override_no_volume_confirm','sidestep_extension_blocked','reentry_cap'}

skips=[]  # dicts: bucket, coin, ts, side, score
with open(LOG) as f:
    for line in f:
        if '"entry_preflight"' not in line and '"execute"' not in line: continue
        try: d=json.loads(line)
        except: continue
        ev=d.get('event'); ts=d.get('ts',0)
        if ts < WINDOW_START: continue
        coin=d.get('coin')
        if not coin: continue
        if ev=='entry_preflight':
            b=pf_bucket(d.get('reason'))
            if not b: continue
            skips.append({'bucket':b,'coin':coin,'ts':ts,'side':'long','src':'preflight'})
        elif ev=='execute':
            if d.get('executed'): continue
            detail=d.get('detail') or ''
            b=str(detail).split(' (')[0].split(' ')[0].strip("[]'\"")
            if b not in EXEC_GATES: continue
            side=(d.get('side') or 'long')
            # only audit LONG considerations (bot considering a long); skip shorts
            if side!='long': continue
            skips.append({'bucket':b,'coin':coin,'ts':ts,'side':'long','src':'execute'})

print(f'raw long skips in 14d window: {len(skips)}', file=sys.stderr)

# dedupe coin+bucket+hour (keep earliest ts in that hour)
seen={}
for s in skips:
    key=(s['bucket'],s['coin'],s['ts']//HR)
    if key not in seen or s['ts']<seen[key]['ts']:
        seen[key]=s
events=list(seen.values())
print(f'deduped skips: {len(events)}', file=sys.stderr)
bybucket=collections.Counter(e['bucket'] for e in events)
print('by bucket:', dict(bybucket), file=sys.stderr)

# ---- 2. fetch forward candles per unique coin ----
coins=sorted(set(e['coin'] for e in events))
print(f'unique coins to fetch: {len(coins)}', file=sys.stderr)
CACHE_F='/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad/_candle_cache.json'
candle_cache={}
if os.path.exists(CACHE_F):
    candle_cache=json.load(open(CACHE_F))
    print(f'loaded {len(candle_cache)} cached coins', file=sys.stderr)
todo=[c for c in coins if c not in candle_cache]
for i,c in enumerate(todo):
    for attempt in range(4):
        try:
            cd=fetch_hl_candles(c,'1h',400)
            candle_cache[c]=[(k.t,k.o,k.h,k.l,k.c) for k in cd] if cd else []
            break
        except Exception as e:
            if attempt==3:
                candle_cache[c]=[]
                print(f'  FAIL {c}: {e}', file=sys.stderr)
            else: time.sleep(1.0*(attempt+1))
    if (i+1)%20==0:
        print(f'  fetched {i+1}/{len(todo)}', file=sys.stderr)
        json.dump(candle_cache, open(CACHE_F,'w'))
json.dump(candle_cache, open(CACHE_F,'w'))

# ---- 3. forward metrics ----
def fwd_metrics(coin, ts):
    cd=candle_cache.get(coin) or []
    # entry ref = close of first candle at/after ts (the price we'd have entered ~at)
    fut=[k for k in cd if k[0]>=ts]
    if not fut: return None
    entry=fut[0][4]  # close of first fwd candle
    if entry<=0: return None
    # windows
    def window(hrs):
        end=ts+hrs*HR
        seg=[k for k in fut if k[0]<=end]
        if not seg: return None
        hi=max(k[2] for k in seg); lo=min(k[3] for k in seg); last=seg[-1][4]
        return (hi/entry-1)*100,(lo/entry-1)*100,(last/entry-1)*100, len(seg)
    return {'entry':entry,'24h':window(24),'72h':window(72),
            'avail_h':(fut[-1][0]-ts)/HR}

for e in events:
    e['m']=fwd_metrics(e['coin'],e['ts'])

# ---- 4. aggregate per bucket ----
def pct(x): return f'{x:+.2f}%'
report=[]
MOON8=8; MOON15=15
for bucket in sorted(bybucket, key=lambda b:-bybucket[b]):
    es=[e for e in events if e['bucket']==bucket and e['m']]
    # require >=24h forward avail for the 24h stat; >=72h for 72h
    e24=[e for e in es if e['m']['24h'] and e['m']['avail_h']>=24]
    e72=[e for e in es if e['m']['72h'] and e['m']['avail_h']>=72]
    if not e24:
        report.append((bucket,len(es),0,None,None,None,None,None,None,'no fwd data'))
        continue
    up24=[e['m']['24h'][2] for e in e24]    # 24h return (last)
    maxup24=[e['m']['24h'][0] for e in e24]  # max-up
    up72=[e['m']['72h'][2] for e in e72] if e72 else []
    maxup72=[e['m']['72h'][0] for e in e72] if e72 else []
    moon8=sum(1 for x in maxup24 if x>=MOON8)/len(maxup24)*100
    moon15=sum(1 for x in maxup72 if x>=MOON15)/len(maxup72)*100 if maxup72 else None
    med24=statistics.median(up24)
    med72=statistics.median(up72) if up72 else None
    medmaxup24=statistics.median(maxup24)
    report.append((bucket,len(es),len(e24),moon8,moon15,med24,med72,medmaxup24,len(e72),''))

# ---- 4b. COIN-LEVEL dedup (neutralize one-mooner concentration) ----
# For each bucket, group skip events by coin; a coin counts ONCE; did it ever moon>8% in 24h
# across its skip(s); take that coin's best 24h maxUp.
coinlevel=[]
for bucket in sorted(bybucket, key=lambda b:-bybucket[b]):
    es=[e for e in events if e['bucket']==bucket and e['m'] and e['m']['24h'] and e['m']['avail_h']>=24]
    bycoin={}
    for e in es:
        mu=e['m']['24h'][0]; rt=e['m']['24h'][2]
        cur=bycoin.get(e['coin'])
        if cur is None or mu>cur[0]: bycoin[e['coin']]=(mu,rt)
    if not bycoin: continue
    nc=len(bycoin)
    mooned=sum(1 for mu,_ in bycoin.values() if mu>=8)
    medret=statistics.median([rt for _,rt in bycoin.values()])
    coinlevel.append((bucket,nc,mooned,mooned/nc*100,medret))

# ---- 5. print ----
print('\n=== GATE AUDIT — forward return after LONG skip (14d window) ===')
print(f'{"gate(bucket)":<32}{"n":>5}{"n24":>5}{"%moon>8(24h)":>13}{"%moon>15(72h)":>14}{"med24h":>9}{"med72h":>9}{"medMaxUp24":>11}')
for r in report:
    bucket,n,n24,m8,m15,md24,md72,mmu24,n72,note=r
    if note=='no fwd data':
        print(f'{bucket:<32}{n:>5}{0:>5}   (no forward data){"":>0} {note}')
        continue
    print(f'{bucket:<32}{n:>5}{n24:>5}{m8:>12.0f}%{(f"{m15:.0f}%" if m15 is not None else "n/a"):>14}'
          f'{md24:>+8.1f}%{(f"{md72:+.1f}%" if md72 is not None else "  n/a"):>9}{mmu24:>+10.1f}%')

# dump raw json for the md writeup
out={'window_days':14,'n_events':len(events),'by_bucket':dict(bybucket),'report':[]}
for r in report:
    out['report'].append(dict(zip(
        ['bucket','n','n24','moon8_24h','moon15_72h','med24h','med72h','med_maxup24h','n72','note'],r)))
# top movers we skipped (worst misses)
misses=[]
for e in events:
    if e['m'] and e['m']['24h'] and e['m']['avail_h']>=24:
        misses.append((e['bucket'],e['coin'],e['m']['24h'][0],e['m']['24h'][2]))
misses.sort(key=lambda x:-x[2])
out['top_missed']=misses[:25]
json.dump(out, open('/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad/gate_audit_result.json','w'), indent=1)
print('\n=== COIN-LEVEL (each coin counts once per bucket) ===')
print(f'{"gate(bucket)":<32}{"coins":>6}{"mooned":>7}{"%moon":>7}{"medRet24":>9}')
for bucket,nc,mooned,pctm,medret in coinlevel:
    print(f'{bucket:<32}{nc:>6}{mooned:>7}{pctm:>6.0f}%{medret:>+8.1f}%')
out['coinlevel']=[dict(zip(['bucket','coins','mooned','pct_moon','med_ret24'],r)) for r in coinlevel]
json.dump(out, open('/private/tmp/claude-501/-Users-julian-dev-Documents-code-hermes-trader/02a15a26-058b-42af-a0f8-8bc0ce9ab5f5/scratchpad/gate_audit_result.json','w'), indent=1)

# top missed deduped by coin (best per coin)
bestcoin={}
for b,c,mu,ret in misses:
    if c not in bestcoin or mu>bestcoin[c][1]: bestcoin[c]=(b,mu,ret)
mc=sorted(bestcoin.items(), key=lambda x:-x[1][1])
print('\nTOP 15 MISSED COINS (deduped, best 24h maxUp):')
for c,(b,mu,ret) in mc[:15]:
    print(f'  {c:<10} maxUp24 {mu:+.1f}%  ret24 {ret:+.1f}%  via {b}')
print('\nwrote gate_audit_result.json', file=sys.stderr)
