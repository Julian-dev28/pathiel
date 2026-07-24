"""Expected ROI on the scout's paper book — bracketed, never a single number.

There is no honest point estimate of this lane's ROI until the ledger resolves.
What IS computable today is the bracket the truth has to live inside:

  OUR CASE     — EV per $1 if our brain's probabilities are exactly right.
                 This is the ceiling, and it is worth what our calibration is
                 worth, which is currently unmeasured (n=0 resolved).
  MARKET CASE  — EV per $1 if the market's price is exactly right. This is the
                 floor, and it is ALWAYS negative by construction: at the
                 market's own probability a bet is a coin flip minus fees.
  BREAK-EVEN   — how much of the gap our brain has to actually be right about
                 for the book to clear zero. That is the number that decides
                 whether this lane ever gets capital.

Reported per lane, because the lanes are separate hypotheses. Annualised off
each position's own days-to-resolution — a +4% edge over 3 days is not the same
business as +4% over 90.

    python -m services.polymarket_scout.roi
    python -m services.polymarket_scout.roi --json
"""
from __future__ import annotations

import argparse
import calendar
import json
import time
from typing import Any, Dict, List, Optional

from services.polymarket_scout import ledger
from services.polymarket_scout.scout import FEE_PER_FILL, paper_pnl

DAY_S = 86_400.0
# Cap on annualisation. A market resolving tomorrow with a 5% edge annualises to
# absurd numbers that say nothing about capacity; clamp so the table stays honest.
MIN_DAYS = 1.0


def ev_per_dollar(win_prob: float, fill_px: float) -> float:
    """EV per $1 staked at `fill_px` when our side wins with probability
    `win_prob`, using the same fee model the ledger grades with."""
    p = max(0.0, min(1.0, float(win_prob)))
    return p * paper_pnl(True, fill_px) + (1.0 - p) * paper_pnl(False, fill_px)


def our_win_prob(row: Dict[str, Any]) -> float:
    """P(our side wins) under OUR forecast."""
    yes = float(row.get("llm_yes") or 0.0)
    return yes if row.get("side") == "YES" else 1.0 - yes


def market_win_prob(row: Dict[str, Any]) -> float:
    """P(our side wins) under the MARKET's price at signal time."""
    yes = float(row.get("mkt_yes") or 0.0)
    return yes if row.get("side") == "YES" else 1.0 - yes


def days_to_resolution(row: Dict[str, Any], now_s: Optional[float] = None) -> Optional[float]:
    """Signal ts -> market end date, in days. Capital is locked for this long,
    which is what makes an annualised number meaningful."""
    end = str(row.get("end_date") or "")
    try:
        end_s = float(calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S")))
    except Exception:
        return None
    start_s = float(row.get("ts") or 0) / 1000.0 or (now_s or time.time())
    return max(MIN_DAYS, (end_s - start_s) / DAY_S)


def breakeven_calibration(our_ev: float, mkt_ev: float) -> Optional[float]:
    """Fraction of the our-case/market-case gap the brain must actually deliver
    for the book to break even. 0.0 = free money, 1.0 = needs to be perfectly
    calibrated, >1.0 = cannot clear zero even if it is right."""
    span = our_ev - mkt_ev
    if span <= 0:
        return None
    return round((0.0 - mkt_ev) / span, 4)


def concentration(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How many independent bets this book actually holds.

    `n` counts rows; the gate's statistics assume those rows are independent
    draws. They are not: the first 15 live reads contained two rows on the same
    Israel-Iran ceasefire event and two on the same Trump-Netanyahu meeting.
    `effective_n` counts distinct EVENTS (Polymarket's own grouping of one
    underlying question) and `top_theme_share` is the largest single tag's share
    — the number that says "this book is really one macro bet".
    """
    n = len(rows)
    if n == 0:
        return {"n": 0}
    events, themes = {}, {}
    for r in rows:
        meta = r.get("meta") or {}
        ev = str(meta.get("event_title") or r.get("question") or "")
        events[ev] = events.get(ev, 0) + 1
        tag = str(r.get("category") or "") or "untagged"
        themes[tag] = themes.get(tag, 0) + 1
    # "untagged" is a recording gap (the judgment lane stores no category), not a
    # theme — naming it as the top theme would hide the real concentration.
    real = {k: v for k, v in themes.items() if k != "untagged"} or themes
    top_tag, top_count = max(real.items(), key=lambda kv: kv[1])
    return {"n": n, "effective_n": len(events),
            "independence": round(len(events) / n, 3),
            "top_theme": top_tag, "top_theme_share": round(top_count / n, 3),
            "untagged_share": round(themes.get("untagged", 0) / n, 3)}


def summarise(rows: List[Dict[str, Any]], now_s: Optional[float] = None) -> Dict[str, Any]:
    """Equal-weight book: $1 per position, the way the ledger records them."""
    n = len(rows)
    if n == 0:
        return {"n": 0}
    our = [ev_per_dollar(our_win_prob(r), float(r.get("fill_px") or 0)) for r in rows]
    mkt = [ev_per_dollar(market_win_prob(r), float(r.get("fill_px") or 0)) for r in rows]
    holds = [d for d in (days_to_resolution(r, now_s) for r in rows) if d is not None]
    our_mean, mkt_mean = sum(our) / n, sum(mkt) / n
    hold = sum(holds) / len(holds) if holds else None
    out: Dict[str, Any] = {
        "n": n,
        "our_case_roi_per_position": round(our_mean, 4),
        "market_case_roi_per_position": round(mkt_mean, 4),
        "breakeven_share_of_edge": breakeven_calibration(our_mean, mkt_mean),
        "mean_hold_days": round(hold, 1) if hold else None,
        "mean_edge_pp": round(sum(abs(float(r.get("edge") or 0)) for r in rows) / n * 100, 1),
        "mean_fill": round(sum(float(r.get("fill_px") or 0) for r in rows) / n, 3),
        **{k: v for k, v in concentration(rows).items() if k != "n"},
    }
    if hold:                        # simple non-compounded turns per year
        turns = 365.0 / hold
        out["turns_per_year"] = round(turns, 1)
        out["our_case_annualised"] = round(our_mean * turns, 3)
        out["market_case_annualised"] = round(mkt_mean * turns, 3)
    return out


def mark_to_market(rows: List[Dict[str, Any]],
                   prices: Dict[str, float]) -> Dict[str, Any]:
    """Unrealised PnL if every open position were closed at the CURRENT price.

    This is NOT a result — a market that moved our way can move back, and only
    resolution pays. But it is the earliest honest evidence that exists: if the
    price migrates toward our number after we take the other side of it, the
    forecast contained information the book had not priced yet. Positions the
    board no longer lists (resolved, expired, delisted) are skipped, not
    guessed.
    """
    marks: List[float] = []
    detail: List[Dict[str, Any]] = []
    for r in rows:
        px = prices.get(str(r.get("market_id")))
        if px is None:
            continue
        fill = float(r.get("fill_px") or 0.0)
        # value of the side we hold, at the current price of YES
        now_val = px if r.get("side") == "YES" else 1.0 - px
        if fill <= 0:
            continue
        marks.append((now_val - fill) / fill)
        detail.append({"q": (r.get("question") or "")[:56], "side": r.get("side"),
                       "fill": round(fill, 3), "now": round(now_val, 3),
                       "mtm_pct": round((now_val - fill) / fill, 3)})
    if not marks:
        return {"n": 0}
    detail.sort(key=lambda d: d["mtm_pct"], reverse=True)
    ordered = sorted(marks)
    mid = len(ordered) // 2
    median = (ordered[mid] if len(ordered) % 2
              else (ordered[mid - 1] + ordered[mid]) / 2)
    # The mean on a book this small is one position. Report the median beside it:
    # +38% mean with 3/11 in the money is a single correct call, not an edge.
    return {"n": len(marks), "mean_mtm_pct": round(sum(marks) / len(marks), 4),
            "median_mtm_pct": round(median, 4),
            "winners": sum(1 for m in marks if m > 0),
            "best": detail[0], "worst": detail[-1], "detail": detail}


def board_prices() -> Dict[str, float]:
    """market_id -> current YES price, from the board cache. Cache read only —
    this must stay free enough to run on every ROI check."""
    try:
        from services.polymarket_scout import board
        payload = board.load()
    except Exception:
        return {}
    out: Dict[str, float] = {}
    for feed in ("trending", "breaking", "sports", "longshots", "edges"):
        for row in payload.get(feed) or []:
            if row.get("yes") is not None:
                out[str(row.get("market_id"))] = float(row["yes"])
    return out


def report(rows: Optional[List[Dict[str, Any]]] = None,
           now_s: Optional[float] = None,
           prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    rows = rows if rows is not None else ledger.load()
    open_rows = [r for r in rows if not r.get("resolved")]
    out: Dict[str, Any] = {
        "generated_at": int(now_s or time.time()),
        "fee_per_fill": FEE_PER_FILL,
        "resolved": sum(1 for r in rows if r.get("resolved")),
        "open": len(open_rows),
        "book": summarise(open_rows, now_s),
        "by_lane": {lane: summarise([r for r in open_rows
                                     if ledger.row_lane(r) == lane], now_s)
                    for lane in ledger.LANES},
        "mark_to_market": mark_to_market(
            open_rows, prices if prices is not None else board_prices()),
        "caveat": ("OUR CASE assumes the brain's probabilities are correct; that "
                   "is the hypothesis under test, not a result. Realised ROI is "
                   "unknown until the ledger resolves (see `resolved`)."),
    }
    return out


def _fmt(rep: Dict[str, Any]) -> str:
    L = [f"# Polymarket scout — expected ROI bracket "
         f"({rep['open']} open, {rep['resolved']} RESOLVED)",
         f"# fee model: {rep['fee_per_fill']:.0%}/fill, charged again on redemption\n"]
    L.append(f"{'lane':<12}{'n':>4}{'eff n':>7}{'our case':>11}{'market case':>13}"
             f"{'breakeven':>11}{'hold d':>9}{'edge pp':>9}{'our ann.':>10}")
    for name, s in [("BOOK", rep["book"])] + list(rep["by_lane"].items()):
        if not s.get("n"):
            continue
        be = s["breakeven_share_of_edge"]
        L.append(f"{name:<12}{s['n']:>4}{s.get('effective_n', 0):>7}"
                 f"{s['our_case_roi_per_position']:>+11.1%}"
                 f"{s['market_case_roi_per_position']:>+13.1%}"
                 f"{(f'{be:.0%}' if be is not None else '—'):>11}"
                 f"{(s['mean_hold_days'] or 0):>9.1f}"
                 f"{s['mean_edge_pp']:>9.1f}"
                 f"{(s.get('our_case_annualised') or 0):>+10.0%}")
    b = rep["book"]
    if b.get("n"):
        L.append(f"\nconcentration: {b['effective_n']}/{b['n']} distinct events "
                 f"({b['independence']:.0%} independent) · biggest theme "
                 f"'{b['top_theme']}' = {b['top_theme_share']:.0%} of the book")
    m = rep.get("mark_to_market") or {}
    if m.get("n"):
        L.append(f"\nMARK-TO-MARKET (unrealised, {m['n']} of {rep['open']} still quoted): "
                 f"{m['mean_mtm_pct']:+.1%} mean · {m['median_mtm_pct']:+.1%} MEDIAN · "
                 f"{m['winners']}/{m['n']} in the money")
        L.append(f"  best  {m['best']['mtm_pct']:+7.1%}  {m['best']['side']:3s} "
                 f"fill {m['best']['fill']:.2f} -> {m['best']['now']:.2f}  {m['best']['q']}")
        L.append(f"  worst {m['worst']['mtm_pct']:+7.1%}  {m['worst']['side']:3s} "
                 f"fill {m['worst']['fill']:.2f} -> {m['worst']['now']:.2f}  {m['worst']['q']}")
        L.append("  a price moving toward our number is evidence, not payment — "
                 "only resolution pays.")
    L.append("\nread: 'breakeven' is the share of our claimed edge that has to be REAL")
    L.append("for the book to clear zero. 'market case' is what we earn if the price")
    L.append("is right and we are noise — negative by construction, that is the fees.")
    L.append(f"\n{rep['caveat']}")
    if rep["resolved"] == 0:
        L.append("\nREALISED ROI: none. n=0 resolved. Every number above is conditional.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = report()
    print(json.dumps(rep, indent=1) if args.json else _fmt(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
