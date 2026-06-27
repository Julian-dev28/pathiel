#!/usr/bin/env python3
"""Build the funding-rate dataset for the data-frontier swarm.

Hyperliquid exposes REAL historical hourly funding (rate + premium) via
fundingHistory — so funding-carry / funding-momentum / carry+trend can be
backtested NOW, no waiting on the live data_logger (which only snapshots OI).

Paginates fetch_funding_history (500 rows/call) back ~`DAYS` days for the same
liquid universe as the candle dataset, and writes funding.json alongside it:

    {meta, funding: {coin: [[time_ms, fundingRate, premium], ...]}}

Gitignored (data, not source). Align with dataset.json's 1h candles by timestamp.
Survivorship caveat applies (today's liquid set = upper bound), same as candles.
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))
_env = _REPO / ".env.local"
if _env.is_file():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, _, v = _line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from hermes_trader.client.hl_client import fetch_funding_history

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (Path(__file__).resolve().parent.parent / "funding.json")
LOG = Path("/tmp/alpha-funding/progress.log")
LOG.parent.mkdir(parents=True, exist_ok=True)
DAYS = int(os.environ.get("FUNDING_DAYS", "90"))
HOUR_MS = 3_600_000


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def _coins_from_candles() -> list:
    ds = Path(__file__).resolve().parent.parent / "dataset.json"
    if ds.is_file():
        try:
            return list(json.loads(ds.read_text()).get("meta", {}).get("coins", []))
        except Exception:
            pass
    # fallback: top liquid perps
    from hermes_trader.client.universe import get_universe
    uni = [u for u in get_universe(include_hip3=False) if u.get("type") == "perp" and u.get("dex") is None]
    uni.sort(key=lambda u: u.get("dayNtlVlm", 0), reverse=True)
    return [u["coin"] for u in uni[:40]]


def _paginate(coin: str, start_ms: int, end_ms: int) -> list:
    """Walk fetch_funding_history forward (500/call) until end_ms or no progress."""
    rows = []
    cur = start_ms
    seen_last = -1
    for _ in range(40):  # hard cap on calls per coin
        batch = fetch_funding_history(coin, cur, end_ms)
        if not batch:
            break
        for r in batch:
            try:
                rows.append([int(r["time"]), float(r["fundingRate"]), float(r.get("premium", 0.0))])
            except Exception:
                continue
        last_t = int(batch[-1]["time"])
        if last_t <= seen_last or last_t >= end_ms:
            break
        seen_last = last_t
        cur = last_t + 1
        time.sleep(0.12)
    # dedup + sort by time
    dd = {t: [t, fr, pr] for t, fr, pr in rows}
    return [dd[t] for t in sorted(dd)]


def main():
    coins = _coins_from_candles()
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - DAYS * 86_400_000
    log(f"FUNDING BUILD start — {len(coins)} coins, {DAYS}d")
    data = {"meta": {"built": now_ms, "days": DAYS, "coins": coins,
                     "survivorship": "PIT-current liquid set; positive results are UPPER BOUNDS"},
            "funding": {}}
    for i, coin in enumerate(coins, 1):
        try:
            rows = _paginate(coin, start_ms, now_ms)
        except Exception as e:
            log(f"  WARN {coin}: {e}")
            rows = []
        data["funding"][coin] = rows
        log(f"  {coin}: {len(rows)} hourly funding rows [{i}/{len(coins)} {100*i//len(coins)}%]")
    OUT.write_text(json.dumps(data))
    mb = OUT.stat().st_size / 1e6
    log(f"FUNDING BUILD done — {OUT} ({mb:.1f} MB)")


if __name__ == "__main__":
    main()
