"""W-N3: zero-capital news-catalyst recorder (Lane N spec, 2026-07-11).

W-N replay failed every wire gate (GDELT blind on 8/10 small-cap pairs, n=1
tradeable replay), so per the lane verdict the news edge gets a forward SHADOW
RECORDER, not capital. Every 30 minutes this lane reads the live Google News
coverage-surge signal (coin_catalyst — 2 cached keyless RSS queries per coin,
zero GDELT, zero HL rate-budget impact) for the current scan candidates and
records a hypothetical LONG per read to the unified shadow ledger.

Both breaking and non-breaking reads are recorded on the same coins — the
non-breaking reads ARE the matched null, so the grader's breaking-vs-baseline
split is built into the data.

Go-live gate (do not weaken): >= 60 forward days AND EV25(breaking) > 0 AND
EV25(breaking) > EV25(non-breaking) AND n(breaking) >= 15. Until then the
only live consumer of news stays the research prompt (research.py already
reads google_news_search).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from hermes_trader.agents import shadow_ledger
from hermes_trader.agents.news_catalyst import coin_catalyst
from hermes_trader.agents.rebalancer_owned import state_file

logger = logging.getLogger(__name__)

_HOUR_MS = 3_600_000
_TS_FILE = state_file(".news_catalyst_live_ts.json")
_MAX_COINS_PER_PASS = 8  # keep the RSS pass bounded even on wild scan days


def _last_pass_ms() -> int:
    try:
        raw = json.load(open(_TS_FILE))
        return int(raw.get("ts", 0)) if isinstance(raw, dict) else 0
    except Exception:
        return 0


def _mark_pass(now_ms: int) -> None:
    try:
        with open(_TS_FILE, "w") as fh:
            json.dump({"ts": now_ms}, fh)
    except Exception:
        pass


def maybe_run(config: Dict[str, Any],
              perceptions: Optional[List[Dict[str, Any]]]) -> int:
    """Call once per scan with the cycle's perceptions. Throttled to one pass
    per scan_interval_min. Returns rows recorded."""
    cfg = (config.get("news_catalyst") or {})
    if not bool(cfg.get("enabled", True)):
        return 0
    now_ms = int(time.time() * 1000)
    interval_min = float(cfg.get("scan_interval_min", 30.0))
    if now_ms - _last_pass_ms() < interval_min * 60_000:
        return 0
    _mark_pass(now_ms)  # mark first: a failing RSS pass must not retry-storm

    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for p in perceptions or []:
        coin = str(p.get("coin") or "")
        if not coin or coin in seen:
            continue
        seen.add(coin)
        if len(seen) > _MAX_COINS_PER_PASS:
            break
        try:
            mid = float(p.get("mid") or 0.0)
        except (TypeError, ValueError):
            mid = 0.0
        if mid <= 0:
            continue
        try:
            rep = coin_catalyst(coin)
        except Exception as exc:
            logger.debug(f"[news-catalyst-live] {coin}: read failed ({exc})")
            continue
        rows.append({
            "coin": coin, "side": "long",
            "signal_bar_t": (now_ms // _HOUR_MS) * _HOUR_MS,
            "entry_ref_px": mid, "horizon_days": 1.0, "stop_pct": 15.0,
            "meta": {
                "n_recent": rep.n_recent,
                "surge_x": rep.surge_x,
                "breaking": bool(rep.breaking),
                "top3_titles": [a.title for a in (rep.headlines or [])[:3]],
                "shadow": True,
            },
        })
    n = shadow_ledger.record_many("news_catalyst", rows)
    if n:
        n_breaking = sum(1 for r in rows if r["meta"]["breaking"])
        logger.info(f"[news-catalyst-live] recorded {n} read(s), {n_breaking} breaking")
    return n
