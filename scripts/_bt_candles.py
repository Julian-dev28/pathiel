"""Shared disk-cached candle fetch for the alpha-hunt backtests.

Fetch once, cache to disk, reuse across every edge_*.py — so HL isn't hammered (429-safe via
exponential backoff) and iteration is fast. Candles are stored as plain dicts (candle_val reads
either dicts or Candle objects).
"""
import json, os, time
from hermes_trader.client.hl_client import fetch_hl_candles

_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           ".bt_candle_cache.json")
_mem = {}
if os.path.exists(_CACHE_FILE):
    try: _mem = json.load(open(_CACHE_FILE))
    except Exception: _mem = {}


def _save():
    try:
        tmp = _CACHE_FILE + ".tmp"
        json.dump(_mem, open(tmp, "w")); os.replace(tmp, _CACHE_FILE)
    except Exception:
        pass


def get(coin, interval="1d", n=260, max_age_h=12):
    """Return candles as list[dict(t,o,h,l,c,v)]. Disk-cached; refetch only if older than
    max_age_h. Exponential backoff on 429/timeout; returns [] if it can't fetch."""
    key = f"{coin}:{interval}:{n}"
    hit = _mem.get(key)
    if hit and (time.time() * 1000 - hit.get("at", 0)) < max_age_h * 3600 * 1000:
        return hit["c"]
    for k in range(6):
        try:
            raw = fetch_hl_candles(coin, interval, n)
            cands = [{"t": int(getattr(b, "t", b["t"]) if not isinstance(b, dict) else b["t"]),
                      "o": float(b["o"] if isinstance(b, dict) else b.o),
                      "h": float(b["h"] if isinstance(b, dict) else b.h),
                      "l": float(b["l"] if isinstance(b, dict) else b.l),
                      "c": float(b["c"] if isinstance(b, dict) else b.c),
                      "v": float((b.get("v", 0) if isinstance(b, dict) else getattr(b, "v", 0)) or 0)}
                     for b in raw]
            if cands:
                _mem[key] = {"at": time.time() * 1000, "c": cands}
                _save()
            return cands
        except Exception as e:
            es = str(e)
            if "429" in es or "Too Many" in es or "timed out" in es or "reset" in es:
                time.sleep(min(60, 5 * (2 ** k)))
            else:
                return hit["c"] if hit else []
    return hit["c"] if hit else []
