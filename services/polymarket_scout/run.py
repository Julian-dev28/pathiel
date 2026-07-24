"""Run one shadow scan. Two lanes, one contract, zero capital.

    JUDGMENT — quiet mid-tail markets (3-21d, liquid, mid-priced). The original
               W-Z1 thesis: being RIGHT beats being FAST.
    TRENDING — what polymarket.com's front page and /breaking are showing right
               now, ranked breaking-first. Same paper-fill discipline.

Both forecast through the project's AI brain (`BrainForecaster` ->
`hermes_trader.agents.ai_brain`), so the provider is whatever the trading engine
is running (claude_cli today) and there is one place to change models.

    python -m services.polymarket_scout.run                        # judgment lane
    python -m services.polymarket_scout.run --lane trending        # trending/breaking
    python -m services.polymarket_scout.run --dry --limit 5        # funnel only, no LLM
"""
from __future__ import annotations

import argparse
import time
from typing import Any, Dict, List, Optional

from services.polymarket_scout import ledger, trending
from services.polymarket_scout.forecaster import BrainForecaster, StubForecaster
from services.polymarket_scout.scout import (
    PolymarketClient, decide_side, is_judgment_market, market_yes_prob,
    _parse_tokens, signed_edge,
)

CFG = {"min_liquidity": 1000.0, "min_days": 3.0, "max_days": 21.0,
       "prob_floor": 0.10, "prob_ceiling": 0.90, "edge_threshold": 0.12}
# The trending lane crosses a wider, faster book, so it demands a bigger
# divergence before it will pay the spread: 15pp vs the judgment lane's 12pp.
TRENDING_CFG = {**trending.DEFAULT_CFG, "edge_threshold": 0.15}
# Sports settle on a scoreboard in hours. An LLM read is stale the moment the
# game moves, so this lane demands a 20pp gap and is opt-in (never in the cron):
# it exists to MEASURE whether the brain has any edge there, not to assume one.
SPORTS_LANE_CFG = {**trending.SPORTS_CFG, "edge_threshold": 0.20}


def event_id_of(m: Dict[str, Any]) -> str:
    """Polymarket's event id for a raw Gamma market. Two markets sharing an event
    share an underlying question ('nominee for X?' has one row per candidate), so
    this is the decorrelation key."""
    evs = m.get("events")
    if isinstance(evs, list) and evs and isinstance(evs[0], dict):
        return str(evs[0].get("id") or "")
    return ""


def candidates(client: PolymarketClient, cfg: Dict[str, Any],
               max_per_event: int = 1) -> List[Dict[str, Any]]:
    now = int(time.time() * 1000)
    mk = [m for m in client.open_markets() if is_judgment_market(m, now, cfg)]
    # most liquid first — that's where a paper fill is realistic
    mk.sort(key=lambda m: float(m.get("liquidity") or 0), reverse=True)
    if max_per_event <= 0:
        return mk
    # One read per event. The first two live judgment reads were both MI-13
    # nominee markets — the same primary, priced from opposite sides. That is one
    # bet recorded twice, and the go-live gate counts it as two.
    per: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for m in mk:
        ev = event_id_of(m) or str(m.get("id"))
        if per.get(ev, 0) >= max_per_event:
            continue
        per[ev] = per.get(ev, 0) + 1
        out.append(m)
    return out


def scan(client: PolymarketClient, forecaster, cfg: Dict[str, Any],
         limit: int = 10, record_fn=ledger.record,
         skip_ids: set = None) -> List[Dict[str, Any]]:
    # one paper trade per market — re-forecasting the same market daily adds
    # correlated duplicates, not independent evidence. A market resolves+leaves
    # open_markets before it could recur, so "ever recorded" is the right skip.
    if skip_ids is None:
        skip_ids = {str(r.get("market_id")) for r in ledger.load()}
    recorded: List[Dict[str, Any]] = []
    fresh = [m for m in candidates(client, cfg) if str(m.get("id")) not in skip_ids]
    for m in fresh[:limit]:
        mkt_yes = market_yes_prob(m)
        if mkt_yes is None:
            continue
        fc = forecaster.forecast(m.get("question") or "", m.get("description") or "")
        if fc is None:
            continue
        llm_yes, why = fc
        edge = signed_edge(llm_yes, mkt_yes)
        side = decide_side(edge, float(cfg.get("edge_threshold", 0.12)))
        if side is None:
            continue
        toks = _parse_tokens(m)
        token = toks[0] if side == "YES" else toks[1]
        ask = client.best_ask(token)
        if ask is None:
            continue                        # can't paper-fill what has no ask
        fill_px, _sz = ask                  # fill at the TOUCH, never the mid
        # `category` is empty on nearly every Gamma market; fall back to the
        # event's slug so the judgment lane's rows carry a theme the
        # concentration report can actually group on.
        evs = m.get("events") if isinstance(m.get("events"), list) else []
        theme = (m.get("category") or
                 (str(evs[0].get("slug") or "").split("-")[0] if evs and isinstance(evs[0], dict) else ""))
        rec = record_fn(market_id=str(m.get("id")), question=m.get("question") or "",
                        side=side, token_id=token, llm_yes=llm_yes, mkt_yes=mkt_yes,
                        fill_px=fill_px, edge=edge, end_date=m.get("endDate") or "",
                        category=theme, reasoning=why,
                        meta={"event_title": (evs[0].get("title") if evs and isinstance(evs[0], dict) else "") or ""})
        recorded.append(rec)
        print(f"[scout] {side:3s} {m.get('question')[:54]:<54} "
              f"llm={llm_yes:.2f} mkt={mkt_yes:.2f} edge={edge:+.2f} fill={fill_px:.2f}")
    return recorded


def scan_trending(client: PolymarketClient, forecaster, cfg: Optional[Dict[str, Any]] = None,
                  limit: int = 8, record_fn=ledger.record, skip_ids: Optional[set] = None,
                  now_ms: Optional[int] = None,
                  rows: Optional[List[Dict[str, Any]]] = None,
                  lane: str = "trending") -> List[Dict[str, Any]]:
    """Forecast the trending/breaking queue and record every divergence.

    Fill realism matches the judgment lane: the CLOB touch on the side we would
    take, with Gamma's quoted bestAsk/bestBid as the fallback when the book call
    fails. Never the mid — the mid is a price nobody trades at.
    """
    c = {**TRENDING_CFG, **(cfg or {})}
    if rows is None:
        rows = trending.collect(client, now_ms=now_ms, cfg=c)
    if skip_ids is None:
        skip_ids = {str(r.get("market_id")) for r in ledger.load()}
    recorded: List[Dict[str, Any]] = []
    for row in trending.forecast_queue(rows, skip_ids=skip_ids, limit=limit, cfg=c):
        mkt_yes = row.get("yes")
        if mkt_yes is None:
            continue
        ctx = (f"{row.get('event_title','')} | tags: {', '.join(row.get('tags') or [])} | "
               f"market resolves {row.get('end_date','')}")
        fc = forecaster.forecast(row.get("question") or "", ctx)
        if fc is None:
            continue
        llm_yes, why = fc
        edge = signed_edge(llm_yes, mkt_yes)
        side = decide_side(edge, float(c.get("edge_threshold", 0.15)))
        if side is None:
            continue
        token = row["yes_token"] if side == "YES" else row["no_token"]
        ask = client.best_ask(token)
        if ask is not None:
            fill_px = ask[0]
        else:                              # book call failed — fall back to the quote
            fill_px = row.get("ask") if side == "YES" else (1.0 - (row.get("bid") or 0.0))
            if not fill_px:
                continue
        rec = record_fn(market_id=row["market_id"], question=row.get("question") or "",
                        side=side, token_id=token, llm_yes=llm_yes, mkt_yes=mkt_yes,
                        fill_px=fill_px, edge=edge, end_date=row.get("end_date") or "",
                        category=(row.get("tags") or [""])[0], reasoning=why,
                        lane=lane,
                        meta={"breaking": bool(row.get("breaking")),
                              "change_24h": row.get("change_24h"),
                              "volume_24h": row.get("volume_24h"),
                              "event_title": row.get("event_title"),
                              "live": bool(row.get("live")),
                              "sport": row.get("sport") or "",
                              "url": row.get("url")})
        recorded.append(rec)
        flag = "BRK" if row.get("breaking") else "   "
        print(f"[scout:{flag}] {side:3s} {(row.get('question') or '')[:50]:<50} "
              f"llm={llm_yes:.2f} mkt={mkt_yes:.2f} edge={edge:+.2f} fill={fill_px:.2f}")
    return recorded


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="no LLM — just print the filtered funnel")
    ap.add_argument("--lane", choices=("judgment", "trending", "sports"), default="judgment")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--longshots", action="store_true",
                    help="dry mode: show the >=3x board instead of the lane funnel")
    args = ap.parse_args()
    client = PolymarketClient()
    if args.dry:
        if args.lane in ("trending", "sports"):
            sports = args.lane == "sports"
            cfg = SPORTS_LANE_CFG if sports else TRENDING_CFG
            rows = (trending.collect_sports(client, cfg=cfg) if sports
                    else trending.collect(client, cfg=cfg))
            if args.longshots:
                shots = trending.longshots(rows, limit=args.limit)
                print(f"# {len(shots)} markets at >= {1/trending.LONGSHOT_MAX_PROB:.1f}x "
                      f"(<= {trending.LONGSHOT_MAX_PROB:.0%}) of {len(rows)} candidates")
                for r in shots:
                    print(f"  {r['payout_x']:>6.2f}x {r['yes']:.2f} "
                          f"vol24h={r['volume_24h']:>10,.0f}  {r['question'][:60]}")
                return 0
            ranked = (trending.rank_sports(rows, limit=args.limit) if sports
                      else trending.forecast_queue(rows, limit=args.limit, cfg=cfg))
            print(f"# {len(rows)} {args.lane} candidates; "
                  f"{sum(1 for r in rows if r.get('live'))} live, "
                  f"{len(trending.rank_breaking(rows, limit=999, cfg=cfg))} breaking")
            for r in ranked:
                flag = "LIVE" if r.get("live") else ("BRK " if r["breaking"] else "    ")
                print(f"  {flag} {r['yes']:.2f} {r['change_24h']:+.2f}/24h "
                      f"vol24h={r['volume_24h']:>10,.0f}  {r['question'][:60]}")
            return 0
        cs = candidates(client, CFG)
        print(f"# {len(cs)} judgment-market candidates (liquid, 3-21d, mid-priced, non-latency)")
        for m in cs[:args.limit]:
            print(f"  {market_yes_prob(m):.2f}  liq={float(m.get('liquidity') or 0):>8.0f}  "
                  f"{(m.get('question') or '')[:70]}")
        return 0
    fc = BrainForecaster()
    print(f"# brain provider: {fc.provider}")
    if args.lane == "sports":
        rec = scan_trending(client, fc, cfg=SPORTS_LANE_CFG, limit=args.limit,
                            rows=trending.collect_sports(client, cfg=SPORTS_LANE_CFG),
                            lane="sports")
    elif args.lane == "trending":
        rec = scan_trending(client, fc, limit=args.limit)
    else:
        rec = scan(client, fc, CFG, limit=args.limit)
    print(f"\n# recorded {len(rec)} paper divergence trade(s) to the shadow ledger")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
