"""Everything one review reads, as one bundle.

A bundle is the account's public Hyperliquid reads (fills, orders,
portfolio, ledger, funding: the same five replies loss_audit.load_inputs
fetches), the 5m and 1h candles of each coin, and the desks' theses. Two
ways to get one:

  load_live(work_dir, theses_dirs)   fetch it now (public /info, read only;
                                     theses read only from their dirs)
  load_bundle(dir)                   read a saved one (`--offline`, tests)

`save_bundle` writes a trimmed copy (orders, candles and thesis fields cut
to what the review reads) so a run can be replayed byte for byte later.

Bundle layout:

    meta.json                    {"as_of_ms": ..., "coins": [...]}
    fills.json orders.json portfolio.json ledger.json funding.json
    candles/<coin>_5m.json       [[t, o, h, l, c], ...]
    candles/<coin>_1h.json
    theses/<desk>/<id>.json      the journal's thesis JSON, trimmed
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from typing import Any, Dict, List, Sequence

from pathiel.models.types import Candle

from services.strategies.backtest import data as D
from services.strategies.evals import loss_audit as LA

RAW_KEYS = ("fills", "orders", "portfolio", "ledger", "funding")
DESKS = ("oil", "metals")
BARS_5M = 5000          # about 17 days of 5m bars: the venue's cap per request
BARS_1H = 1800
THESIS_KEEP = ("id", "desk_id", "created_at", "expires_at", "horizon_days")
CALL_KEEP = ("side", "instrument", "entry_px", "stop_px", "targets", "confidence", "invalidation")
GRADE_KEEP = ("direction_hit", "ret_pct", "graded_at")


def _coin_file(coin: str, interval: str) -> str:
    return urllib.parse.quote(coin, safe="") + f"_{interval}.json"


def _bars_to_rows(bars: Sequence[Candle]) -> List[List[float]]:
    return [[b.t, b.o, b.h, b.l, b.c] for b in bars]


def _rows_to_bars(rows: Sequence[Sequence[float]]) -> List[Candle]:
    return [Candle(t=int(r[0]), o=r[1], h=r[2], l=r[3], c=r[4], v=0.0) for r in rows]


def read_theses_raw(theses_dir: str) -> List[Dict[str, Any]]:
    """Every thesis JSON under `theses_dir`, oldest file name first. READ
    ONLY. A missing dir is an empty list."""
    if not theses_dir or not os.path.isdir(theses_dir):
        return []
    out = []
    for name in sorted(os.listdir(theses_dir)):
        if name.endswith(".json"):
            try:
                with open(os.path.join(theses_dir, name)) as fh:
                    raw = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(raw, dict):
                out.append(raw)
    return out


def trim_thesis(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: raw.get(k) for k in THESIS_KEEP if k in raw}
    out["call"] = {k: (raw.get("call") or {}).get(k) for k in CALL_KEEP}
    g = raw.get("grade")
    out["grade"] = {k: g.get(k) for k in GRADE_KEEP} if isinstance(g, dict) else None
    return out


def load_live(work_dir: str, theses_dirs: Dict[str, str]) -> Dict[str, Any]:
    """Fetch every input now. Public reads only; a fresh `work_dir` per run
    (the loss audit's caches never expire, so reusing one would replay old
    fills)."""
    raw_dir, cache_dir = os.path.join(work_dir, "raw"), os.path.join(work_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    raw = LA.load_inputs(raw_dir, refresh=True)
    bundle: Dict[str, Any] = {k: raw[k] for k in RAW_KEYS}
    bundle["bars5"] = {c: D.fetch_candles_cached(c, "5m", BARS_5M, cache_dir) for c in LA.COINS}
    bundle["bars1h"] = {c: D.fetch_candles_cached(c, "1h", BARS_1H, cache_dir) for c in LA.COINS}
    bundle["theses_dirs"] = {d: theses_dirs.get(d, "") for d in DESKS}
    bundle["theses_raw"] = {d: read_theses_raw(bundle["theses_dirs"][d]) for d in DESKS}
    bundle["as_of_ms"] = int(time.time() * 1000)
    return bundle


def load_bundle(path: str) -> Dict[str, Any]:
    with open(os.path.join(path, "meta.json")) as fh:
        meta = json.load(fh)
    bundle: Dict[str, Any] = {"as_of_ms": int(meta["as_of_ms"])}
    for k in RAW_KEYS:
        p = os.path.join(path, f"{k}.json")
        with open(p) as fh:
            bundle[k] = json.load(fh)
    coins = meta.get("coins") or list(LA.COINS)
    for key, interval in (("bars5", "5m"), ("bars1h", "1h")):
        bundle[key] = {}
        for c in coins:
            p = os.path.join(path, "candles", _coin_file(c, interval))
            rows = []
            if os.path.exists(p):
                with open(p) as fh:
                    rows = json.load(fh)
            bundle[key][c] = _rows_to_bars(rows)
    bundle["theses_dirs"] = {d: os.path.join(path, "theses", d) for d in DESKS}
    bundle["theses_raw"] = {d: read_theses_raw(bundle["theses_dirs"][d]) for d in DESKS}
    return bundle


def save_bundle(bundle: Dict[str, Any], path: str, since_ms: int = LA.SINCE_MS) -> None:
    """Write `bundle` trimmed to what the review reads: fills, funding and
    orders on the desks' coins (orders from two days before `since_ms`), 5m
    bars from the UTC day of each coin's first fill since `since_ms`, 1h bars
    from four days before the first thesis or `since_ms`, and the thesis
    fields in THESIS_KEEP / CALL_KEEP / GRADE_KEEP."""
    os.makedirs(os.path.join(path, "candles"), exist_ok=True)
    with open(os.path.join(path, "meta.json"), "w") as fh:
        json.dump({"as_of_ms": bundle["as_of_ms"], "coins": list(LA.COINS)}, fh)
    day = 24 * LA.HOUR_MS
    for k in RAW_KEYS:
        body = bundle[k]
        if k == "orders":
            body = [o for o in body if (o.get("order") or {}).get("coin") in LA.COINS
                    and int((o.get("order") or {}).get("timestamp") or 0) >= since_ms - 2 * day]
        elif k == "fills":
            body = [f for f in body if f.get("coin") in LA.COINS]
        elif k == "funding":
            body = [r for r in body if (r.get("delta") or {}).get("coin") in LA.COINS]
        with open(os.path.join(path, f"{k}.json"), "w") as fh:
            json.dump(body, fh, separators=(",", ":"))
    first_thesis = min([_created_ms(t) for ts in bundle["theses_raw"].values() for t in ts] or [since_ms])
    first_fill = {c: min([int(f["time"]) for f in bundle["fills"] if f.get("coin") == c
                          and int(f["time"]) >= since_ms] or [bundle["as_of_ms"]]) for c in LA.COINS}
    for key, interval in (("bars5", "5m"), ("bars1h", "1h")):
        for c, bars in bundle[key].items():
            cut = (first_fill.get(c, since_ms) // day * day if key == "bars5"
                   else min(first_thesis, since_ms) - 4 * day)
            rows = _bars_to_rows([b for b in bars if b.t >= cut])
            with open(os.path.join(path, "candles", _coin_file(c, interval)), "w") as fh:
                json.dump(rows, fh, separators=(",", ":"))
    for desk, theses in bundle["theses_raw"].items():
        d = os.path.join(path, "theses", desk)
        os.makedirs(d, exist_ok=True)
        for raw in theses:
            with open(os.path.join(d, f"{raw.get('id')}.json"), "w") as fh:
                json.dump(trim_thesis(raw), fh, indent=1)


def _created_ms(raw: Dict[str, Any]) -> int:
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00"))
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    except (KeyError, ValueError):
        return 1 << 62


def thesis_directions(theses_dir: str) -> List[D.ThesisDirection]:
    """The simulator's reduced thesis form, parsed by the backtests' own
    loader (data.load_journaled_theses), oldest first. READ ONLY."""
    return D.load_journaled_theses(theses_dir) if theses_dir else []
