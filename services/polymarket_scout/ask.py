"""Ask the brain about SPECIFIC markets, by name.

The lanes pick their own candidates on a pre-registered rule. This is the manual
door: name the markets, get a verdict on each, no matter how the ranking felt
about them.

The ledger rule does not bend for it. Every verdict is printed, but only the ones
clearing their lane's edge threshold are RECORDED as paper trades — recording a
sub-threshold read would quietly widen the hypothesis the ledger is grading, and
then the go-live gate would be measuring something we never pre-registered.

    python -m services.polymarket_scout.ask "Sherif vs Badosa" "Fed increase"
    python -m services.polymarket_scout.ask --id 973277 --no-record
"""
from __future__ import annotations

import argparse
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.polymarket_scout import ledger, trending
from services.polymarket_scout.forecaster import BrainForecaster
from services.polymarket_scout.run import SPORTS_LANE_CFG, TRENDING_CFG
from services.polymarket_scout.scout import PolymarketClient, decide_side, signed_edge


def _emit(line: str) -> None:
    """Line-flushed stdout. Each forecast is a multi-minute web-search call, so a
    block-buffered pipe shows an empty log for the whole run and looks hung."""
    print(line, flush=True)


def lane_of(row: Dict[str, Any]) -> str:
    """Sports rows grade in the sports lane even when they were reached by name —
    the lane is a property of the market, not of how we found it."""
    tags = set(row.get("tags") or [])
    return "sports" if (row.get("sport") or {"sports", "esports"} & tags) else "trending"


def cfg_for(row: Dict[str, Any]) -> Dict[str, Any]:
    return SPORTS_LANE_CFG if lane_of(row) == "sports" else TRENDING_CFG


def select(rows: Sequence[Dict[str, Any]], needles: Sequence[str],
           ids: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """Rows matching any needle (case-insensitive substring of question or event
    title) or any exact market id. One row per market, first match wins, input
    order of the needles preserved so the report reads like the request."""
    by_id = {str(r.get("market_id")): r for r in rows}
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for mid in ids:
        r = by_id.get(str(mid))
        if r and str(mid) not in seen:
            seen.add(str(mid))
            out.append(r)
    for needle in needles:
        n = needle.strip().lower()
        if not n:
            continue
        for r in rows:
            hay = f"{r.get('question','')} {r.get('event_title','')}".lower()
            if n in hay and r["market_id"] not in seen:
                seen.add(r["market_id"])
                out.append(r)
                break
    return out


def ask(client: PolymarketClient, forecaster, needles: Sequence[str] = (),
        ids: Sequence[str] = (), record: bool = True, record_fn=ledger.record,
        rows: Optional[List[Dict[str, Any]]] = None,
        now_ms: Optional[int] = None,
        printer: Callable[[str], None] = _emit) -> List[Dict[str, Any]]:
    """Forecast each named market. Returns one verdict dict per market, whether
    or not it cleared the bar."""
    if rows is None:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        rows = (trending.collect(client, now_ms=now, cfg=TRENDING_CFG)
                + trending.collect_sports(client, now_ms=now, cfg=SPORTS_LANE_CFG))
    picks = select(rows, needles, ids)
    if not picks:
        printer("[ask] no market matched")
        return []
    already = {str(r.get("market_id")) for r in ledger.load()}
    verdicts: List[Dict[str, Any]] = []
    for row in picks:
        mkt_yes = row.get("yes")
        ctx = (f"{row.get('event_title','')} | tags: {', '.join(row.get('tags') or [])} | "
               f"{'LIVE, current score ' + row['score'] if row.get('score') else ''} | "
               f"resolves {row.get('end_date','')}")
        fc = forecaster.forecast(row.get("question") or "", ctx)
        lane, cfg = lane_of(row), cfg_for(row)
        thr = float(cfg.get("edge_threshold", 0.15))
        v: Dict[str, Any] = {
            "market_id": row["market_id"], "question": row.get("question"),
            "lane": lane, "mkt_yes": mkt_yes, "threshold": thr,
            "payout_x": trending.payout_x(row), "live": bool(row.get("live")),
            "llm_yes": None, "edge": None, "side": None, "reasoning": "",
            "recorded": False, "skip_reason": "",
        }
        if fc is None:
            v["skip_reason"] = "brain declined / unparseable"
            verdicts.append(v)
            printer(f"[ask] {row['question'][:60]:<60} FORECAST FAILED")
            continue
        llm_yes, why = fc
        edge = signed_edge(llm_yes, mkt_yes)
        side = decide_side(edge, thr)
        v.update({"llm_yes": llm_yes, "edge": edge, "side": side, "reasoning": why})
        if side is None:
            v["skip_reason"] = f"|edge| {abs(edge):.2f} < {lane} threshold {thr:.2f}"
        elif str(row["market_id"]) in already:
            v["skip_reason"] = "already in the ledger (no duplicate reads)"
        elif not record:
            v["skip_reason"] = "--no-record"
        else:
            token = row["yes_token"] if side == "YES" else row["no_token"]
            ask_px = client.best_ask(token)
            fill = (ask_px[0] if ask_px is not None else
                    (row.get("ask") if side == "YES" else 1.0 - (row.get("bid") or 0.0)))
            if not fill:
                v["skip_reason"] = "no touch to fill against"
            else:
                record_fn(market_id=row["market_id"], question=row.get("question") or "",
                          side=side, token_id=token, llm_yes=llm_yes, mkt_yes=mkt_yes,
                          fill_px=fill, edge=edge, end_date=row.get("end_date") or "",
                          category=(row.get("tags") or [""])[0], reasoning=why, lane=lane,
                          meta={"breaking": bool(row.get("breaking")),
                                "change_24h": row.get("change_24h"),
                                "volume_24h": row.get("volume_24h"),
                                "event_title": row.get("event_title"),
                                "live": bool(row.get("live")),
                                "sport": row.get("sport") or "",
                                "asked": True, "url": row.get("url")})
                v["recorded"] = True
                v["fill_px"] = fill
                already.add(str(row["market_id"]))
        printer(f"[ask] {(row.get('question') or '')[:52]:<52} "
                f"mkt={mkt_yes:.2f} ai={llm_yes:.2f} edge={edge:+.2f} "
                f"{(side or 'NO-BET'):6s} {'RECORDED' if v['recorded'] else v['skip_reason']}")
        verdicts.append(v)
    return verdicts


def analyze_row(row: Dict[str, Any], forecaster, record: bool = False,
                record_fn=ledger.record,
                client: Optional[PolymarketClient] = None) -> Dict[str, Any]:
    """Forecast ONE already-fetched board row (the dashboard 'Analyze' button).

    No universe scan: the row carries everything (`yes`, tokens, tags, score), so
    the only cost is the single brain call. Returns the verdict dict. Recording
    still honours the lane's edge threshold — a click does not lower the bar.
    """
    # REFRESH the YES price off the live CLOB book BEFORE forecasting, so the
    # verdict and edge are against the current price, not the cached board row
    # (Gamma lags; the click should see what the app sees).
    mkt_yes = row.get("yes")
    if row.get("yes_token"):
        try:
            from services.polymarket_scout.updown import clob_midpoint
            live = clob_midpoint(row["yes_token"])
            if live is not None:
                mkt_yes = live
        except Exception:
            pass
    lane, cfg = lane_of(row), cfg_for(row)
    thr = float(cfg.get("edge_threshold", 0.15))
    v: Dict[str, Any] = {
        "market_id": row.get("market_id"), "question": row.get("question"),
        "lane": lane, "mkt_yes": mkt_yes, "threshold": thr,
        "payout_x": trending.payout_x(row), "live": bool(row.get("live")),
        "llm_yes": None, "edge": None, "side": None, "reasoning": "",
        "recorded": False, "skip_reason": "",
    }
    if mkt_yes is None:
        v["skip_reason"] = "market has no price"
        return v
    ctx = (f"{row.get('event_title','')} | tags: {', '.join(row.get('tags') or [])} | "
           f"{'LIVE, current score ' + row['score'] if row.get('score') else ''} | "
           f"resolves {row.get('end_date','')}")
    fc = forecaster.forecast(row.get("question") or "", ctx)
    if fc is None:
        v["skip_reason"] = "brain declined / unparseable"
        return v
    llm_yes, why = fc
    edge = signed_edge(llm_yes, mkt_yes)
    side = decide_side(edge, thr)
    v.update({"llm_yes": llm_yes, "edge": edge, "side": side, "reasoning": why})
    if side is None:
        v["skip_reason"] = f"|edge| {abs(edge):.2f} < {lane} threshold {thr:.2f}"
        return v
    if str(row.get("market_id")) in {str(r.get("market_id")) for r in ledger.load()}:
        v["skip_reason"] = "already in the ledger"
    elif not record:
        v["skip_reason"] = "analyze-only (not recorded)"
    else:
        token = row.get("yes_token") if side == "YES" else row.get("no_token")
        fill = None
        if token and client is not None:
            ap = client.best_ask(token)
            fill = ap[0] if ap else None
        if fill is None:
            fill = row.get("ask") if side == "YES" else (1.0 - (row.get("bid") or 0.0))
        if fill:
            record_fn(market_id=row.get("market_id"), question=row.get("question") or "",
                      side=side, token_id=token or "", llm_yes=llm_yes, mkt_yes=mkt_yes,
                      fill_px=fill, edge=edge, end_date=row.get("end_date") or "",
                      category=(row.get("tags") or [""])[0], reasoning=why, lane=lane,
                      meta={"breaking": bool(row.get("breaking")),
                            "live": bool(row.get("live")), "sport": row.get("sport") or "",
                            "event_title": row.get("event_title"), "asked": True,
                            "url": row.get("url")})
            v["recorded"] = True
            v["fill_px"] = fill
    return v


def analyze_market_id(market_id: str, forecaster, board_payload: Dict[str, Any],
                      record: bool = False, record_fn=ledger.record,
                      client: Optional[PolymarketClient] = None) -> Optional[Dict[str, Any]]:
    """Find `market_id` across the board feeds and analyze it. None if not found."""
    for feed in ("trending", "breaking", "sports", "longshots", "edges"):
        for row in board_payload.get(feed) or []:
            if str(row.get("market_id")) == str(market_id):
                return analyze_row(row, forecaster, record=record,
                                   record_fn=record_fn, client=client)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("needles", nargs="*", help="substring of the question or event title")
    ap.add_argument("--id", action="append", default=[], help="exact market id (repeatable)")
    ap.add_argument("--no-record", action="store_true",
                    help="print verdicts only; write nothing to the ledger")
    args = ap.parse_args()
    fc = BrainForecaster()
    print(f"# brain provider: {fc.provider}")
    vs = ask(PolymarketClient(), fc, needles=args.needles, ids=args.id,
             record=not args.no_record)
    print(f"\n# {len(vs)} verdict(s), {sum(1 for v in vs if v['recorded'])} recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
