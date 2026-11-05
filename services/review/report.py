"""The review's markdown report and its one-line summary. Pure: a dict in,
text out. Verdict first, tiny tables, plain words."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from services.review import stats as S

Z80 = 1.959964 + 0.841621    # two-sided 5% test, 80% power


def _f(x: Optional[float], nd: int = 2, sign: bool = False) -> str:
    if x is None:
        return "-"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def _ci(lo: Optional[float], hi: Optional[float], nd: int = 2, sign: bool = True) -> str:
    if lo is None:
        return ""
    return f" [{_f(lo, nd, sign)}, {_f(hi, nd, sign)}]"


def _usd(x: float) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x):.2f}"


def _pci(lo: Optional[float], hi: Optional[float]) -> str:
    return "" if lo is None else f" [{_pct(lo)}, {_pct(hi)}]"


def _p(p: Optional[float]) -> str:
    return "-" if p is None else ("<0.001" if p < 0.001 else f"{p:.3f}")


def trades_needed(sd_r: Optional[float], effect_r: float = 0.4) -> Optional[int]:
    """Trades one slice needs before a true `effect_r` mean shows at the 5%
    level 80% of the time, at this spread of outcomes (normal approximation)."""
    if not sd_r:
        return None
    return int(math.ceil((Z80 * sd_r / effect_r) ** 2))


def sd(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = S.mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def verdict(res: Dict[str, Any]) -> List[str]:
    rows, recs = res["rows"], res["records"]
    allrow = next((r for r in rows if r["dim"] == "all"), None)
    edges = [r["key"] for r in rows if r["label"] == S.EDGE]
    leaks = [r["key"] for r in rows if r["label"] == S.LEAK]
    lean_neg = [r["key"] for r in rows if r["label"] == S.LEAN_NEG]
    lean_pos = [r["key"] for r in rows if r["label"] == S.LEAN_POS]
    flukes = [r["key"] for r in rows if r["label"] == S.FLUKE]
    out = []
    if not recs:
        return ["**No closed trades in the window.** Nothing to learn from yet."]
    if edges or leaks:
        out.append(f"**{'EDGE: ' + ', '.join(edges) if edges else 'No edge'}. "
                   f"{'LEAK: ' + ', '.join(leaks) if leaks else 'No leak'}.**")
    elif len(recs) < 3 * S.MIN_N_EDGE:
        out.append(f"**No edge and no leak yet.** {len(recs)} closed trades is too few to tell skill from luck: "
                   f"an EDGE or LEAK needs n >= {S.MIN_N_EDGE} in one slice, a mean R CI clear of 0 and p < 0.05.")
    else:
        out.append(f"**No edge and no leak.** No slice of {len(recs)} closed trades clears the bar "
                   f"(n >= {S.MIN_N_EDGE}, a mean R CI clear of 0, p < 0.05).")
    if allrow and allrow["mean_r"] is not None:
        out.append(f"All trades: net {_usd(allrow['net_usd'])}, mean {allrow['mean_r']:+.2f}R"
                   f"{_ci(allrow['r_lo'], allrow['r_hi'])}, win rate {_pct(allrow['win_rate'])}"
                   f"{_pci(allrow['win_lo'], allrow['win_hi'])}.")
    if lean_neg:
        out.append(f"Leaning the wrong way: {', '.join(lean_neg)}.")
    if lean_pos:
        out.append(f"Leaning the right way: {', '.join(lean_pos)}.")
    if flukes:
        out.append(f"One trade carries it (fluke): {', '.join(flukes)}.")
    s = sd([r["r_mult"] for r in recs if r.get("r_mult") is not None])
    need = trades_needed(s)
    if need:
        out.append(f"At this spread of outcomes (sd {s:.2f}R a trade), a real +0.4R edge needs about "
                   f"{need} trades in one slice to show. Treat every pattern below as a lead, not a rule.")
    lucks = sorted(r["luck_pct"] for r in recs if r.get("luck_pct") is not None)
    if lucks:
        med = S.quantile(lucks, 0.5)
        out.append(f"Timing: the median trade sat at the {med:.0f}th percentile of 500 random entries the "
                   f"same day, same side, same stop (50 = the day's market did the work).")
    return out


def slice_table_md(rows: Sequence[Dict[str, Any]]) -> List[str]:
    out = ["| Slice | n | Win % | Mean R | Net $ | p | Label |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['key']} | {r['n']} | {_pct(r['win_rate'])}{_pci(r['win_lo'], r['win_hi'])} | "
                   f"{_f(r['mean_r'], 2, True)}{_ci(r['r_lo'], r['r_hi'])} | "
                   f"{_f(r['net_usd'], 2, True)}{_ci(r['net_lo'], r['net_hi'])} | {_p(r['p'])} | {r['label']} |")
    return out


def _read(pct: Optional[float]) -> str:
    if pct is None:
        return "-"
    return "timing helped" if pct >= 80 else "timing hurt" if pct <= 20 else "the day's market"


def render(res: Dict[str, Any]) -> str:
    rows, recs = res["rows"], res["records"]
    date = res["as_of"][:10]
    L: List[str] = [f"# Trade review, {date}", ""]
    v = verdict(res)
    L += [v[0], ""] + [f"- {line}" for line in v[1:]]
    L += ["", f"Window: trades entered since {res['since']}, data as of {res['as_of']}. "
              f"Ledger: {res['ledger']['total']} trades ({len(res['ledger']['added'])} new, "
              f"{len(res['ledger']['revised'])} revised this run). "
              f"Provisional (inside the 12h settle window, may still change): {res['provisional'] or 'none'}.", ""]
    flagged = [r for r in rows if r["label"] != S.NOISE]
    L += ["## What stands out", ""]
    if flagged:
        L += slice_table_md(flagged)
    else:
        L.append("Every slice is NOISE.")
    L += ["", "## What to try next", ""]
    props = res["proposals"]["proposals"]
    if props:
        for p in props:
            new = " (new)" if p["id"] in res["new_proposals"] else ""
            L.append(f"- **{p['lever']}** for {p['slice']} ({p['label']}, n={p['n']}, mean "
                     f"{_f(p['mean_r'], 2, True)}R){new}: {p['why']}. Try {', '.join(p['params'])}.")
            L.append(f"  Run: `{p['command']}`")
            if p.get("prior"):
                L.append(f"  Last test: {p['prior']}.")
            if p["conflicts"]:
                L.append(f"  Conflicts with keep: {', '.join(p['conflicts'])}.")
        L += ["", "Backtest first, on the operator's OK. Adopt only if it beats live on most datasets as a "
                  "plateau, and then only on the operator's go. This review never edits live config."]
    else:
        L.append("Nothing to test: no slice leans the wrong way.")
    keeps, watch = res["proposals"]["keeps"], res["proposals"]["watch"]
    if keeps:
        L += ["", "Keep / protect:"]
        for k in keeps:
            hurt = f" A change to {', '.join(k['levers'])} would cut these trades." if k["levers"] else ""
            L.append(f"- {k['slice']} ({k['label']}, n={k['n']}, mean {_f(k['mean_r'], 2, True)}R).{hurt}")
    if watch:
        L += ["", "Watch (no lever reaches it yet):"]
        for w in watch:
            L.append(f"- {w['slice']} ({w['label']}, n={w['n']}, mean {_f(w['mean_r'], 2, True)}R).")
    L += ["", "## Thesis calls vs a coin flip", "",
          "| Horizon | Desk | Hits | Rate | p vs 50% |", "|---|---|---|---|---|"]
    for c in res["calls"]:
        if c["n"]:
            L.append(f"| {c['horizon']} | {c['desk']} | {c['hits']}/{c['n']} | {_pct(c['rate'])}"
                     f"{_pci(c['lo'], c['hi'])} | {_p(c['p'])} |")
    L += ["", "7d is the grader's verdict (theses graded so far). 24h: did price move the call's way in "
              "the day after it was written. Calls a few hours apart share most of their 24h, so they are "
              "not independent: read p as a ceiling on confidence."]
    L += ["", "## Luck check, per trade", "",
          "| Trade | Net $ | R | Walked R | Day median R | Luck pct | Read |", "|---|---|---|---|---|---|---|"]
    for r in recs:
        name = f"{'Brent' if 'BRENT' in r['coin'] else 'Silver' if 'SILVER' in r['coin'] else 'Gold' if 'GOLD' in r['coin'] else r['coin']} " \
               f"{r['side'][0].upper()} {r['entry'][5:16].replace('T', ' ')}"
        tags = f" ({'+'.join(r['tags'])})" if r.get("tags") else ""
        L.append(f"| {name}{tags} | {r['net_usd']:+.2f} | {_f(r.get('r_mult'), 2, True)} | "
                 f"{_f(r.get('luck_actual_r'), 2, True)} | {_f(r.get('luck_median_r'), 2, True)} | "
                 f"{_f(r.get('luck_pct'), 0)} | {_read(r.get('luck_pct'))} |")
    L += ["", "Walked R: the real entry replayed with clean stop and target fills, the same rule as the "
              "random draws, so the percentile compares timing only."]
    L += ["", "## All slices", ""]
    L += slice_table_md(rows)
    L += ["", "## How to read this", "",
          f"- n is small. Nothing under n={S.MIN_N_EDGE} is ever called EDGE or LEAK.",
          "- R = net $ (fees, funding in) over the $ the opening stop risked at full size.",
          "- Mean R and net $ CIs: seeded bootstrap, 2,000 resamples. p: seeded permutation test of the "
          "slice's mean R against every other trade (the all row: against 0).",
          "- LEANING: p < 0.2, or under 10 trades all the same sign. FLUKE: dropping the biggest trade flips "
          "the sign. NOISE: the rest, and anything under 3 trades.",
          "- Luck pct: where the trade's R sits among 500 random entries that UTC day, same side, stop and "
          "target distance, same holding time, on 5m bars.",
          "", "## How to rerun", "",
          "```", "python3 -m services.review run", "```", "",
          "Public Hyperliquid reads only, no LLM calls, no orders. Ledger: "
          "`$PATHIEL_STATE_DIR/review/ledger.jsonl`. Code and labels: `services/review/README.md`.", ""]
    return "\n".join(L)


def summary_line(res: Dict[str, Any]) -> str:
    rows = res["rows"]
    count = {lab: sum(1 for r in rows if r["label"] == lab) for lab in S.LABELS}
    allrow = next((r for r in rows if r["dim"] == "all"), None)
    net = f"{allrow['net_usd']:+.2f}" if allrow else "0"
    mr = f"{allrow['mean_r']:+.2f}R" if allrow and allrow["mean_r"] is not None else "-"
    props = res["proposals"]["proposals"]
    new_leaks = ",".join(res["new_leaks"]) or "none"
    return (f"REVIEW {res['as_of']} trades={len(res['records'])} net={net} meanR={mr} "
            f"edge={count[S.EDGE]} leak={count[S.LEAK]} lean+={count[S.LEAN_POS]} lean-={count[S.LEAN_NEG]} "
            f"fluke={count[S.FLUKE]} proposals={len(props)} new_proposals={len(res['new_proposals'])} "
            f"new_leaks={new_leaks} report={res['report_rel']}")
