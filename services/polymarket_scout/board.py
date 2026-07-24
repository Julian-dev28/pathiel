"""The board — one cached JSON payload the dashboard renders, built off-thread.

Split of duties, and why: the dashboard must never block on Polymarket's API or
on an LLM call, so nothing here runs inside a request. `refresh()` (cron / the
scout's daily run) does the network work and writes
`.state/polymarket_scout/board.json`; `load()` is a pure file read the web route
can call on every poll for free. A stale cache renders with a STALE badge rather
than 500-ing or hanging the page.

The payload joins three things the UI needs together:
  1. TRENDING / BREAKING — what Polymarket itself is showing right now.
  2. Our AI brain's forecast for the markets it has already judged (from the
     shadow ledger), so every card can show market price vs our probability.
  3. The scoreboard — resolved paper PnL and the Brier comparison that decides
     whether the LLM actually beats the market's own price.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from services.polymarket_scout import ledger, trending
from services.polymarket_scout.scout import PolymarketClient, make_gamma_resolver

# The cron refresh is hourly; anything older than 3h is called out in the UI as
# stale rather than shown as if it were live.
STALE_AFTER_S = 3 * 3600
GATE = {"min_n": 150, "min_mean_pnl": 0.03}


def _path() -> str:
    return os.path.join(ledger._state_dir(), "board.json")


# ── join ─────────────────────────────────────────────────────────────────────
def forecasts_by_market(rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """market_id -> our newest recorded forecast. The ledger is append-only and
    ascending, so the last row for an id wins."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in (rows if rows is not None else ledger.load()):
        mid = str(r.get("market_id") or "")
        if not mid:
            continue
        out[mid] = {"llm_yes": r.get("llm_yes"), "mkt_yes_at_signal": r.get("mkt_yes"),
                    "side": r.get("side"), "edge": r.get("edge"),
                    "fill_px": r.get("fill_px"), "reasoning": r.get("reasoning"),
                    "ts": r.get("ts"), "resolved": bool(r.get("resolved"))}
    return out


def attach_forecasts(rows: List[Dict[str, Any]],
                     fc: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add `forecast` to each row, plus `live_edge` = our probability minus the
    market's CURRENT price. `edge` on the ledger row is frozen at signal time;
    `live_edge` is what the divergence is worth now, which is what a reader of
    the board actually wants to see."""
    out = []
    for r in rows:
        f = fc.get(str(r.get("market_id")))
        row = dict(r)
        row["forecast"] = f
        if f and f.get("llm_yes") is not None and r.get("yes") is not None:
            row["live_edge"] = round(float(f["llm_yes"]) - float(r["yes"]), 4)
        else:
            row["live_edge"] = None
        out.append(row)
    return out


def scoreboard(grade: Dict[str, Any]) -> Dict[str, Any]:
    """Ledger grade + explicit gate progress. `detail` is dropped: the board is a
    summary surface and the per-trade list belongs to the ledger CLI."""
    n = int(grade.get("n") or 0)
    mean = grade.get("mean_pnl_per_$")
    beats = bool(grade.get("llm_beats_market"))
    return {
        "n": n, "pending": int(grade.get("pending") or 0),
        "mean_pnl_per_$": mean, "win_rate": grade.get("win_rate"),
        "brier_llm": grade.get("brier_llm"), "brier_mkt": grade.get("brier_mkt"),
        "llm_beats_market": beats,
        "gate": {
            "min_n": GATE["min_n"], "min_mean_pnl": GATE["min_mean_pnl"],
            "n_ok": n >= GATE["min_n"],
            "pnl_ok": bool(mean is not None and mean >= GATE["min_mean_pnl"]),
            "brier_ok": beats,
            "passed": bool(n >= GATE["min_n"] and mean is not None
                           and mean >= GATE["min_mean_pnl"] and beats),
        },
    }


def build(client: Optional[PolymarketClient] = None, now_ms: Optional[int] = None,
          trending_limit: int = 48, breaking_limit: int = 24,
          grade_resolved: bool = True, provider: str = "") -> Dict[str, Any]:
    """Fetch + assemble the payload. Network-bound; never call from a request."""
    client = client or PolymarketClient()
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    rows = trending.collect(client, now_ms=now)
    # Sports is a second fetch, not a filter: the judgment feeds exclude tag 1
    # server-side, so the only way to have both boards is to ask twice.
    try:
        sports_rows = trending.collect_sports(client, now_ms=now)
    except Exception:
        sports_rows = []
    fc = forecasts_by_market()
    trend = attach_forecasts(trending.rank_trending(rows, trending_limit), fc)
    brk = attach_forecasts(trending.rank_breaking(rows, breaking_limit), fc)
    sports = attach_forecasts(trending.rank_sports(sports_rows), fc)
    # the >=3x board spans BOTH universes — a 20c underdog is a longshot whether
    # it is a ceasefire or a game line
    longs = trending.longshots(attach_forecasts(rows + sports_rows, fc))

    grade: Dict[str, Any] = {}
    if grade_resolved:
        try:
            grade = ledger.grade(make_gamma_resolver(client))
        except Exception:
            grade = {}

    # Our open divergences, biggest live edge first — the "what would we bet"
    # pane. Only rows we actually have a forecast for.
    edges = sorted((r for r in attach_forecasts(rows, fc) if r.get("live_edge") is not None),
                   key=lambda r: abs(r["live_edge"]), reverse=True)[:24]
    return {
        "generated_at": now,
        "provider": provider,
        "universe": len(rows) + len(sports_rows),
        "counts": {"trending": len(trend), "breaking": len(brk), "edges": len(edges),
                   "sports": len(sports), "longshots": len(longs)},
        "trending": trend, "breaking": brk, "edges": edges,
        "sports": sports, "longshots": longs,
        "scoreboard": scoreboard(grade),
        "cfg": {**{k: trending.DEFAULT_CFG[k] for k in
                   ("min_volume_24h", "min_hours", "max_days", "breaking_min_move")},
                "longshot_max_prob": trending.LONGSHOT_MAX_PROB},
    }


# ── cache ────────────────────────────────────────────────────────────────────
def save(payload: Dict[str, Any]) -> str:
    p = _path()
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, p)                    # atomic: a reader never sees half a file
    return p


def load(now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Read the cache. Always returns a renderable payload — an empty board with
    `status: empty` if the refresher has not run yet, never an exception."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    empty = {"generated_at": None, "status": "empty", "age_s": None, "stale": True,
             "universe": 0,
             "counts": {"trending": 0, "breaking": 0, "edges": 0, "sports": 0,
                        "longshots": 0},
             "trending": [], "breaking": [], "edges": [], "sports": [], "longshots": [],
             "scoreboard": scoreboard({}), "cfg": {}}
    try:
        with open(_path()) as fh:
            payload = json.load(fh)
    except Exception:
        return empty
    if not isinstance(payload, dict):
        return empty
    gen = int(payload.get("generated_at") or 0)
    age = max(0, (now - gen) // 1000) if gen else None
    payload["age_s"] = age
    payload["stale"] = bool(age is None or age > STALE_AFTER_S)
    payload["status"] = "stale" if payload["stale"] else "ok"
    return payload


def refresh(client: Optional[PolymarketClient] = None, provider: str = "") -> Dict[str, Any]:
    payload = build(client, provider=provider)
    save(payload)
    return payload


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Refresh the Polymarket board cache.")
    ap.add_argument("--print", action="store_true")
    args = ap.parse_args()
    p = refresh()
    c = p["counts"]
    print(f"[board] universe={p['universe']} trending={c['trending']} "
          f"breaking={c['breaking']} sports={c['sports']} "
          f"longshots={c['longshots']} edges={c['edges']} -> {_path()}")
    if args.print:
        for r in p["breaking"][:6]:
            print(f"  BRK {r['change_24h']:+.2f} {r['yes']:.2f} "
                  f"${r['volume_24h']:>10,.0f}  {r['question'][:56]}")
        for r in p["longshots"][:6]:
            print(f"  {r.get('payout_x') or 0:>5.1f}x {r['yes']:.2f} "
                  f"${r['volume_24h']:>10,.0f}  {r['question'][:56]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
