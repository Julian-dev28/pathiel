#!/usr/bin/env python3
"""Higher-timeframe (1h/4h/1d) native candle dataset for the 1.5x green-vol breakout test.
Broad crypto-perp universe (top ~120 by dayNtlVlm, HIP-3 excluded). Native bars, not resampled.
Saves one file per interval: htf_1h.json / htf_4h.json / htf_1d.json."""
import json, os, sys, time
from pathlib import Path
_REPO = Path("/Users/julian_dev/Documents/code/hermes-trader"); sys.path.insert(0, str(_REPO))
for _l in (_REPO/".env.local").read_text().splitlines():
    _l=_l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        k,_,v=_l.partition("="); os.environ.setdefault(k.strip(),v.strip())
from hermes_trader.client.universe import get_universe
from hermes_trader.client.hl_client import fetch_hl_candles
SCR = Path(__file__).resolve().parent
LOG = Path("/tmp/htf-build/progress.log"); LOG.parent.mkdir(parents=True, exist_ok=True)
def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True); LOG.open("a").write(line+"\n")
log("HTF BUILD start")
uni=[u for u in get_universe(force_refresh=True, include_hip3=False) if u.get("type")=="perp" and u.get("dex") is None]
uni.sort(key=lambda u:u.get("dayNtlVlm",0), reverse=True)
coins=[u["coin"] for u in uni[:120]]
log(f"universe: {len(coins)} coins (top by dayNtlVlm)")
INTERVALS=("1h","4h","1d")
out={iv:{"meta":{"coins":coins,"interval":iv},"candles":{}} for iv in INTERVALS}
for i,c in enumerate(coins,1):
    for iv in INTERVALS:
        try:
            cs=fetch_hl_candles(c,iv,5000)
            out[iv]["candles"][c]=[[x.t,x.o,x.h,x.l,x.c,x.v] for x in cs]
        except Exception as e:
            out[iv]["candles"][c]=[]; log(f"WARN {c} {iv}: {e}")
        time.sleep(0.08)
    if i%20==0: log(f"  {i}/{len(coins)} coins")
for iv in INTERVALS:
    p=SCR/f"htf_{iv}.json"; p.write_text(json.dumps(out[iv]))
    nb=sum(len(v) for v in out[iv]["candles"].values())
    log(f"  wrote {p.name} ({p.stat().st_size/1e6:.1f} MB), {nb} bars total")
log("HTF BUILD done")
