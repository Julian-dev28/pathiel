"""What to actually DO with each lane — the action layer over the evidence.

The rest of this service answers "what happened and is it real". This module
answers the only question that matters afterwards: **do something, or don't?**

Every action carries four fields so it can be acted on without re-reading the
tables:

    do            the instruction, imperative, specific
    because       the number it came from (never a vibe)
    trigger       the observable that says "now"
    invalidate    the observable that says "stop"

Rules, not opinions:

  - An action whose evidence is a coin flip is a **DON'T**, and it is stated as
    loudly as a DO. Most weeks, most lanes, that is the honest output.
  - Nothing here sizes a position or names a dollar amount. It says what the
    evidence supports; capital is the operator's call.
  - Confidence is derived from the lane's own backtest, not asserted.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# do / don't / watch — the three shapes an action can take
DO, DONT, WATCH = "do", "dont", "watch"

# How far today's breadth has to sit from the week's before the day is a
# different tape. 25pp: the week's number is a 7-bar average, so a single
# session inside +-25pp of it is the same tape sampled once, while 6% green
# against a 50% week (observed 2026-08-03) plainly is not.
BREADTH_FLIP_PP = 25.0


def _a(kind: str, do: str, because: str, trigger: str = "", invalidate: str = "",
       confidence: str = "medium", tag: str = "") -> Dict[str, Any]:
    return {"kind": kind, "do": do, "because": because, "trigger": trigger,
            "invalidate": invalidate, "confidence": confidence, "tag": tag}


# ── HL ───────────────────────────────────────────────────────────────────────


def hl_actions(payload: Dict[str, Any], max_names: int = 4) -> List[Dict[str, Any]]:
    """Actions from the 7-day Hyperliquid read, per sector."""
    out: List[Dict[str, Any]] = []
    ev = payload.get("eval") or {}
    reads = payload.get("reads") or []

    # 1. The forecast's own verdict decides how the whole tab may be used.
    if ev.get("status") == "ok":
        if ev.get("beats_coinflip"):
            out.append(_a(DO, "Trade the forecast direction — it clears the coin-flip bar.",
                          f"walk-forward {ev['dir_hit']*100:.1f}% over n={ev['n']} "
                          f"({ev['dir_edge_sigma']}σ)", confidence="high", tag="method"))
        else:
            out.append(_a(DONT,
                          "Do NOT trade the p(up) column as a direction call. Use the band: "
                          "size so the p10–p90 range is survivable, and put stops outside it.",
                          f"walk-forward {ev['dir_hit']*100:.1f}% over n={ev['n']} "
                          f"({ev['dir_edge_sigma']}σ vs a coin flip), but band coverage "
                          f"{ev['coverage_80']*100:.1f}% vs 80% nominal — the range is honest, "
                          f"the arrow is not",
                          confidence="high", tag="method"))

    for sector, reg in sorted((payload.get("regimes") or {}).items()):
        if reg.get("status") != "ok":
            continue
        rows = [r for r in reads if r.get("sector", "crypto") == sector]
        bench = reg.get("bench", "BTC")
        tag = sector

        # 2. TODAY vs the week. Everything above is a 7-day read off daily
        # bars: it only moves when a bar closes, so without this line a
        # week-old instruction reads as current at any hour. Descriptive only —
        # it says whether the tape that produced the week is still the tape now,
        # and never claims a day-of-week effect (tested on the 5m lane, no
        # weekday bucket survived Bonferroni).
        b1, b7 = reg.get("breadth_1d_pct"), reg.get("breadth_pct")
        if b1 is not None:
            bench_1d = reg.get("bench_ret_1d")
            today = (f"today {b1:.0f}% of the scan is green vs {b7:.0f}% on the week"
                     + (f", {bench} {bench_1d:+.1f}% on the day" if bench_1d is not None else ""))
            gap = b1 - b7
            if gap <= -BREADTH_FLIP_PP:
                out.append(_a(DONT,
                              f"Today is broadly RED in {sector} — the long lines below are "
                              f"describing last week's tape, not this session.",
                              today, trigger="wait for the next daily close, or buy the "
                                             "pullback names only where the level holds",
                              confidence="medium", tag=tag))
            elif gap >= BREADTH_FLIP_PP:
                out.append(_a(DONT,
                              f"Today is broadly GREEN in {sector} — the short lines below are "
                              f"describing last week's tape, not this session.",
                              today, trigger="wait for the next daily close, or sell the "
                                             "bounce names only where the level fails",
                              confidence="medium", tag=tag))
            else:
                out.append(_a(WATCH, f"Today in {sector}: the week's tape still holds.",
                              today, confidence="medium", tag=tag))

        # 3. Regime -> which BOOK is even appropriate this week.
        breadth, trend_share = reg.get("breadth_pct", 50), reg.get("trend_share_pct", 0)
        if trend_share < 40:
            out.append(_a(DONT,
                          f"Skip trend-following in {sector} this week — run mean-reversion "
                          f"or stay flat.",
                          f"only {trend_share:.0f}% of the scan is in a real trend; the rest "
                          f"round-tripped", tag=tag))
        elif breadth >= 60:
            out.append(_a(DO, f"Long the {sector} leaders — breadth supports it.",
                          f"{breadth:.0f}% of the scan green, {trend_share:.0f}% trending, "
                          f"{bench} {reg.get('bench_ret_7d'):+.1f}%", tag=tag))
        elif breadth <= 40:
            out.append(_a(DO, f"Favour the SHORT side in {sector}; longs are fighting the tape.",
                          f"only {breadth:.0f}% of the scan is green with {bench} "
                          f"{reg.get('bench_ret_7d'):+.1f}%", tag=tag))

        # 4. Dispersion -> pair trades vs directional.
        alt = reg.get("alt_strength_pct")
        if alt is not None and abs(alt) >= 2:
            out.append(_a(DO,
                          ("Trade relative strength (long leaders / short laggards), not direction."
                           if alt > 0 else
                           f"Do not pay up for single names — express the view in {bench} itself."),
                          f"median residual vs {bench} is {alt:+.1f}pp", tag=tag))

        # 5. The actual names, with levels.
        ups = [r for r in rows if r.get("label") in ("STRONG_UP", "UP")]
        downs = [r for r in rows if r.get("label") in ("STRONG_DOWN", "DOWN")]
        ups.sort(key=lambda r: -(r.get("score") or 0))
        downs.sort(key=lambda r: (r.get("score") or 0))
        for r in ups[:max_names]:
            f = r.get("forecast") or {}
            out.append(_a(WATCH,
                          f"{r['coin']} — long candidate on a pullback, not at the highs.",
                          f"{float(r.get('ret_7d') or 0):+.1f}% on the week, efficiency "
                          f"{r.get('efficiency'):.2f} (clean path), {label_words(r.get('label'))}"
                          + _today_note(r, "long"),
                          trigger=f"holds the 7d EMA around {_fmt(r.get('ema7'))}",
                          invalidate=f"closes below {_fmt(r.get('low_7d'))} (7d low) or "
                                     f"below the p10 {_fmt(f.get('p10'))}",
                          confidence="medium", tag=tag))
        for r in downs[:max_names]:
            f = r.get("forecast") or {}
            out.append(_a(WATCH,
                          f"{r['coin']} — short candidate on a bounce.",
                          f"{float(r.get('ret_7d') or 0):+.1f}% on the week, efficiency "
                          f"{r.get('efficiency'):.2f}, {label_words(r.get('label'))}"
                          + _today_note(r, "short"),
                          trigger=f"fails at the 7d EMA around {_fmt(r.get('ema7'))}",
                          invalidate=f"closes above {_fmt(r.get('high_7d'))} (7d high) or "
                                     f"above the p90 {_fmt(f.get('p90'))}",
                          confidence="medium", tag=tag))

        # 6. Traps.
        chops = [r for r in rows if r.get("label") == "CHOP"
                 and abs(float(r.get("ret_7d") or 0)) >= 8]
        if chops:
            out.append(_a(DONT,
                          "Do not chase these — the weekly number is a round trip, not a trend: "
                          + ", ".join(f"{r['coin']} {float(r['ret_7d'] or 0):+.0f}%"
                                      for r in chops[:5]),
                          "big weekly move with efficiency under 0.15", tag=tag))

        fund = reg.get("mean_funding_apr_pct")
        if fund is not None and fund >= 15:
            out.append(_a(WATCH,
                          "Crowded longs — a flush is cheap to hedge here.",
                          f"scan funding averages {fund:+.1f}% APR", tag=tag))

    # 7. Dated catalysts beat everything else in the window.
    for r in reads:
        for fl in (r.get("flags") or []):
            if fl.get("kind") == "event":
                out.append(_a(WATCH, f"{r['coin']} — dated catalyst inside the forecast window.",
                              fl.get("note", ""),
                              trigger="position before the date, not after",
                              confidence="high", tag="catalyst"))
                break
    return out


def _today_note(read: Dict[str, Any], side: str) -> str:
    """Where TODAY sits inside a weekly WATCH — is the entry live or not yet?

    "long candidate on a pullback" is only actionable on a day the thing is
    actually pulling back, and the 7-day read cannot tell you that: it is the
    same number all day. Sized in daily sigma so a 3% day means something
    different on ADA than on BTC. Descriptive, never a forecast.
    """
    r1, sig = read.get("ret_1d"), read.get("sigma_day_pct")
    if r1 is None:
        return ""
    r1 = float(r1)
    z = f" ({abs(r1) / float(sig):.1f}σ)" if sig else ""
    px, ema7 = read.get("px"), read.get("ema7")
    big = bool(sig) and abs(r1) >= 0.5 * float(sig)
    if side == "long":
        if r1 < 0 and big:
            note = f" — TODAY {r1:+.1f}%{z}: the pullback is happening now"
        elif r1 > 0 and big:
            note = f" — TODAY {r1:+.1f}%{z}: extending, wrong day to pay up"
        else:
            note = f" — TODAY {r1:+.1f}%{z}, inside a normal day"
        if px is not None and ema7 is not None and float(px) < float(ema7):
            note += "; already under the 7d EMA, so the trigger below is not live"
    else:
        if r1 > 0 and big:
            note = f" — TODAY {r1:+.1f}%{z}: the bounce is happening now"
        elif r1 < 0 and big:
            note = f" — TODAY {r1:+.1f}%{z}: still falling, no bounce to sell"
        else:
            note = f" — TODAY {r1:+.1f}%{z}, inside a normal day"
        if px is not None and ema7 is not None and float(px) > float(ema7):
            note += "; already over the 7d EMA, so the trigger below is not live"
    return note


# Trend labels are machine constants (STRONG_UP, EMA_STACK_BULL). They read as
# log output inside a sentence, so the narrative spells them.
_LABEL_WORDS = {
    "STRONG_UP": "strongly up", "UP": "up", "CHOP": "chopping",
    "DOWN": "down", "STRONG_DOWN": "strongly down", "STABLE": "stable",
    "CHURN": "churning",
}


def label_words(label: object) -> str:
    raw = str(label or "").strip()
    return _LABEL_WORDS.get(raw, raw.replace("_", " ").lower())


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "–"
    return f"{v:,.0f}" if v >= 1000 else (f"{v:.2f}" if v >= 1 else f"{v:.4g}")


BUILDERS = {"hl": hl_actions}


def build(lane: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Actions for one lane, grouped so the tab can lead with DO and DON'T."""
    fn = BUILDERS.get(lane)
    if not fn or not payload or payload.get("status") not in ("ok", None):
        return {"status": "empty", "actions": []}
    try:
        actions = fn(payload)
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200], "actions": []}
    return {
        "status": "ok",
        "lane": lane,
        "actions": actions,
        "counts": {k: sum(1 for a in actions if a["kind"] == k)
                   for k in (DO, DONT, WATCH)},
    }
