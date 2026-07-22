"""Run one shadow scan: read live Polymarket markets, filter to the mid-tail
judgment set, forecast the most liquid candidates with the LLM, and record every
divergence as a paper trade. ZERO capital. Grade later with `grade.py`.

    python -m services.polymarket_scout.run                 # live LLM (opus + search)
    python -m services.polymarket_scout.run --dry --limit 5 # no LLM, just show the funnel
"""
from __future__ import annotations

import argparse
import time
from typing import Any, Dict, List

from services.polymarket_scout import ledger
from services.polymarket_scout.forecaster import ClaudeForecaster, StubForecaster
from services.polymarket_scout.scout import (
    PolymarketClient, decide_side, is_judgment_market, market_yes_prob,
    _parse_tokens, signed_edge,
)

CFG = {"min_liquidity": 1000.0, "min_days": 3.0, "max_days": 21.0,
       "prob_floor": 0.10, "prob_ceiling": 0.90, "edge_threshold": 0.12}


def candidates(client: PolymarketClient, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    now = int(time.time() * 1000)
    mk = [m for m in client.open_markets() if is_judgment_market(m, now, cfg)]
    # most liquid first — that's where a paper fill is realistic
    mk.sort(key=lambda m: float(m.get("liquidity") or 0), reverse=True)
    return mk


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
        rec = record_fn(market_id=str(m.get("id")), question=m.get("question") or "",
                        side=side, token_id=token, llm_yes=llm_yes, mkt_yes=mkt_yes,
                        fill_px=fill_px, edge=edge, end_date=m.get("endDate") or "",
                        category=m.get("category") or "", reasoning=why)
        recorded.append(rec)
        print(f"[scout] {side:3s} {m.get('question')[:54]:<54} "
              f"llm={llm_yes:.2f} mkt={mkt_yes:.2f} edge={edge:+.2f} fill={fill_px:.2f}")
    return recorded


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="no LLM — just print the filtered funnel")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    client = PolymarketClient()
    if args.dry:
        cs = candidates(client, CFG)
        print(f"# {len(cs)} judgment-market candidates (liquid, 3-21d, mid-priced, non-latency)")
        for m in cs[:args.limit]:
            print(f"  {market_yes_prob(m):.2f}  liq={float(m.get('liquidity') or 0):>8.0f}  "
                  f"{(m.get('question') or '')[:70]}")
        return 0
    rec = scan(client, ClaudeForecaster(), CFG, limit=args.limit)
    print(f"\n# recorded {len(rec)} paper divergence trade(s) to the shadow ledger")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
