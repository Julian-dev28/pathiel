#!/usr/bin/env python
"""W-V0: extract every research event and tag its news-vs-TA quadrant.

Sources (the honest inventory — see findings/W-V_news_vs_ta.md):
  1. Session log (~/.pathia-session-log.jsonl, event=research): EVERY
     research event since 2026-06-19 — ts, coin, verdict, confidence,
     news_risk, entry_px, analysis_id. It does NOT carry news_context (the
     headline string was never logged there; verified 0 occurrences in 102MB).
  2. .agent-memory.json 'analyses': the rolling LAST 200 analyses DO carry
     news_context — joined by id to enrich the tail of the log.

Polarity: pathia.agents.mover_recorders.classify_news_polarity — the
SAME deterministic classifier the forward recorder uses (news_risk wins when
polar, else keyword polarity over the headline string).

Tags (directional verdicts only get a tradeable tag):
  ALIGNED   polar news matches verdict side (positive+LONG / negative+SHORT)
  CONFLICT  polar news opposes verdict side (the SKHX question)
  NEUTRAL   real news_context present but no polarity either way
  NO_NEWS_DATA  no usable news info (news_context absent from the log AND
                news_risk none/missing) — most of history; the reference pool.

Output: W-V0_events.json next to this file. Read-only; never imports
scripts.trading_loop.
"""
import json
import os
import sys
from pathlib import Path

_REPO = str(Path(__file__).resolve().parents[3])
sys.path.insert(0, _REPO)

from pathia.agents.mover_recorders import classify_news_polarity  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION_LOG = os.path.expanduser("~/.pathia-session-log.jsonl")
MEMORY = os.path.join(_REPO, ".agent-memory.json")
OUT = os.path.join(HERE, "W-V0_events.json")


def main() -> None:
    # memory enrichment: id -> (news_context, last_price)
    mem = {}
    for a in (json.load(open(MEMORY)).get("analyses") or []):
        if a.get("id"):
            mem[a["id"]] = a

    events = []
    with open(SESSION_LOG, "rb") as fh:
        for ln in fh:
            if b'"event":"research"' not in ln:
                continue
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if e.get("event") != "research":
                continue
            verdict = (e.get("verdict") or "").upper()
            coin = e.get("coin") or ""
            if not coin or verdict not in ("LONG", "SHORT", "PASS", "CLOSE"):
                continue
            m = mem.get(e.get("analysis_id")) or {}
            news_ctx = (m.get("news_context") or "").strip()
            has_ctx = bool(news_ctx) and news_ctx.lower() != "no news"
            news_risk = (e.get("news_risk") or m.get("news_risk") or "").lower()
            polar_risk = news_risk in ("positive", "negative")
            if polar_risk or has_ctx:
                polarity, source = classify_news_polarity(
                    news_risk if polar_risk else "none",
                    news_ctx if has_ctx else "")
            else:
                polarity, source = None, None
            side = {"LONG": "long", "SHORT": "short"}.get(verdict)
            if side is None:
                tag = "NON_DIRECTIONAL"
            elif polarity in ("positive", "negative"):
                tag = ("ALIGNED" if (polarity == "positive") == (side == "long")
                       else "CONFLICT")
            elif polarity == "neutral":
                tag = "NEUTRAL"
            else:
                tag = "NO_NEWS_DATA"
            events.append({
                "ts": int(e["ts"]),
                "coin": coin,
                "verdict": verdict,
                "side": side,
                "confidence": e.get("confidence"),
                "news_risk": news_risk or None,
                "news_context": news_ctx if has_ctx else None,
                "news_polarity": polarity,
                "polarity_source": source,
                "tag": tag,
                "web_search_used": bool(e.get("web_search_used")),
                "daily_move_pct": e.get("daily_move_pct"),
            })

    with open(OUT, "w") as fh:
        json.dump(events, fh)

    # summary
    from collections import Counter
    print(f"events: {len(events)}  (memory-enriched: "
          f"{sum(1 for x in events if x['news_context'])})")
    print("tags (all):", dict(Counter(x["tag"] for x in events)))
    dir_ev = [x for x in events if x["side"]]
    print("tags (directional):", dict(Counter(x["tag"] for x in dir_ev)))

    # Part 2 — the SKHX class: xyz events with polar news OPPOSING the verdict
    print("\n--- xyz CONFLICT set (news polar + opposing verdict) ---")
    n = 0
    for x in events:
        if x["coin"].startswith("xyz") and x["tag"] == "CONFLICT":
            n += 1
            print(x["ts"], x["coin"], x["verdict"], x["confidence"],
                  x["news_polarity"], (x["news_context"] or "")[:120])
    if n == 0:
        print("(empty — zero xyz conflict events in the whole recorded history)")

    # xyz news-blindness rate (memory-enriched slice only, where ctx is known)
    xyz_known = [x for x in events if x["coin"].startswith("xyz")
                 and x["ts"] >= min((m0.get("created_at") or 0)
                                    for m0 in mem.values())]
    xyz_with_news = [x for x in xyz_known if x["news_context"]]
    print(f"\nxyz events inside the memory window: {len(xyz_known)}, "
          f"with real news_context: {len(xyz_with_news)}")


if __name__ == "__main__":
    main()
