"""5-minute up/down markets — AI read on the CURRENT rolling window.

These are Polymarket's `{asset}-updown-5m-{epoch}` markets: a new one every 5
minutes, slug carries the window-start epoch, endDate = start + 300s. The URL the
operator pasted was one specific (expired) window; `current_slug` always resolves
the LIVE one from the clock, so the read follows the market as it rolls.

HONESTY FIRST (W-Z1): this is the pure latency lane — the gabagool arb pocket,
bot-saturated, measured 0/60 tradeable, and the explicit target of the 2026 taker
fee. Being *right* does not beat being *fast* here, and a 5-minute horizon is too
short for the LLM's news-synthesis edge (web search is useless — nothing resolves
in 5 min). So this is SHADOW ONLY, zero capital, forever unless the ledger proves
the impossible. What it CAN do is read live BTC momentum and record the AI's
directional call, so the operator can watch — and the ledger can confirm it is a
coin flip minus fees.

Context fed to the model is live price action, not web search: Binance 1m klines
(keyless) → last-5m / last-15m move, range, and where price sits in it.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

from services.polymarket_scout import ledger
from services.polymarket_scout.scout import GAMMA, _curl_get, market_yes_prob

WINDOW_S = 300
BOOK = "updown_5m"
BINANCE = "https://api.binance.com/api/v3/klines"
# asset symbol on Polymarket slug -> Binance pair
_PAIRS = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT",
          "doge": "DOGEUSDT"}

_SYS = (
    "You are a short-horizon crypto momentum trader. You are given LIVE price "
    "action for one asset and must estimate the probability it closes UP over the "
    "next ~5 minutes (last traded price at the window close above the price now). "
    "There is no news edge at this horizon — reason ONLY from the momentum, range "
    "position and recent candles given. Be honest: 5-minute direction is close to "
    "a coin flip, so stay near 0.50 unless the tape genuinely leans. Reply with "
    'ONLY this JSON on the last line: {"verdict":"UP"|"DOWN","up_prob":<0..1>,'
    '"reasoning":"<1-2 sentences>"}'
)


def window_start(now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    return int(now // WINDOW_S) * WINDOW_S


def current_slug(asset: str = "btc", now: Optional[float] = None) -> str:
    return f"{asset.lower()}-updown-5m-{window_start(now)}"


def current_market(asset: str = "btc", now: Optional[float] = None,
                   http_get: Callable[[str], Any] = _curl_get) -> Optional[Dict[str, Any]]:
    slug = current_slug(asset, now)
    d = http_get(f"{GAMMA}/markets?slug={slug}")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        return d[0]
    return None


# ── live price context (Binance 1m klines, keyless) ──────────────────────────
def _klines(pair: str, limit: int = 16, runner: Optional[Callable] = None) -> List[List[float]]:
    url = f"{BINANCE}?symbol={pair}&interval=1m&limit={limit}"
    try:
        if runner is not None:
            raw = runner(url)
        else:
            out = subprocess.run(["curl", "-s", "--max-time", "10", url],
                                 capture_output=True, text=True, timeout=15)
            raw = json.loads(out.stdout)
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def price_context(asset: str = "btc", runner: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    """Momentum snapshot from the last ~15 one-minute candles. None if unavailable."""
    pair = _PAIRS.get(asset.lower())
    if not pair:
        return None
    kl = _klines(pair, 16, runner=runner)
    if len(kl) < 6:
        return None
    closes = [float(k[4]) for k in kl]           # kline[4] = close
    highs = [float(k[2]) for k in kl]
    lows = [float(k[3]) for k in kl]
    now_px = closes[-1]
    def chg(n: int) -> float:
        return (now_px - closes[-1 - n]) / closes[-1 - n] * 100.0 if len(closes) > n and closes[-1 - n] else 0.0
    hi15, lo15 = max(highs[-15:]), min(lows[-15:])
    pos = (now_px - lo15) / (hi15 - lo15) if hi15 > lo15 else 0.5
    return {
        "asset": asset.upper(), "price": now_px,
        "chg_5m_pct": round(chg(5), 3), "chg_15m_pct": round(chg(15), 3),
        "range_pos_15m": round(pos, 3),          # 0=at 15m low, 1=at 15m high
        "last6_closes": [round(c, 2) for c in closes[-6:]],
    }


def build_prompt(ctx: Dict[str, Any], mkt_yes: Optional[float]) -> str:
    return (
        f"ASSET: {ctx['asset']}  price={ctx['price']}\n"
        f"5m move: {ctx['chg_5m_pct']:+.3f}%   15m move: {ctx['chg_15m_pct']:+.3f}%\n"
        f"position in 15m range: {ctx['range_pos_15m']:.2f} (0=low,1=high)\n"
        f"last 6 one-minute closes: {ctx['last6_closes']}\n"
        f"market currently prices UP at {mkt_yes if mkt_yes is not None else 'n/a'}.\n"
        "P(UP over the next 5 minutes)? JSON only."
    )


def _parse(body: str) -> Optional[Dict[str, Any]]:
    import re
    for cand in reversed(re.findall(r'\{[^{}]*"up_prob"[^{}]*\}', body or "")):
        try:
            o = json.loads(cand)
            p = float(o.get("up_prob"))
        except Exception:
            continue
        if 0.0 <= p <= 1.0:
            return {"up_prob": max(0.01, min(0.99, p)),
                    "verdict": "UP" if p >= 0.5 else "DOWN",
                    "reasoning": str(o.get("reasoning") or "")[:300]}
    return None


def analyze(asset: str = "btc", brain: Any = None, now: Optional[float] = None,
            http_get: Callable[[str], Any] = _curl_get,
            kline_runner: Optional[Callable] = None,
            record: bool = True, record_fn: Callable = ledger.record) -> Dict[str, Any]:
    """One read on the current window: live momentum -> AI P(up) -> record.

    Returns a display dict (market + context + verdict), always — a failed brain
    call or missing data yields a row with `up_prob=None` rather than raising."""
    now = time.time() if now is None else now
    mkt = current_market(asset, now, http_get=http_get)
    ctx = price_context(asset, runner=kline_runner)
    out: Dict[str, Any] = {
        "asset": asset.upper(), "slug": current_slug(asset, now),
        "market_id": str(mkt.get("id")) if mkt else None,
        "question": (mkt or {}).get("question"),
        "end_date": (mkt or {}).get("endDate"),
        "mkt_up": market_yes_prob(mkt) if mkt else None,
        "context": ctx, "up_prob": None, "verdict": None, "reasoning": "",
        "edge": None, "ts": int(now * 1000),
    }
    if ctx is None:
        out["reasoning"] = "no live price data"
        return out
    if brain is None:
        from hermes_trader.agents.ai_brain import get_brain
        brain = get_brain()
    try:
        body = brain.complete(_SYS, build_prompt(ctx, out["mkt_up"]), web_search=False)
    except Exception as exc:
        out["reasoning"] = f"brain error: {exc}"
        return out
    v = _parse(str(body or ""))
    if v is None:
        out["reasoning"] = "unparseable verdict"
        return out
    out.update({"up_prob": v["up_prob"], "verdict": v["verdict"], "reasoning": v["reasoning"]})
    if out["mkt_up"] is not None:
        out["edge"] = round(v["up_prob"] - out["mkt_up"], 4)
    if record and out["market_id"]:
        record_fn(market_id=out["market_id"], question=out["question"] or "",
                  side="YES" if v["verdict"] == "UP" else "NO",
                  token_id="", llm_yes=v["up_prob"], mkt_yes=out["mkt_up"] or 0.5,
                  fill_px=out["mkt_up"] or 0.5, edge=out["edge"] or 0.0,
                  end_date=out["end_date"] or "", category=asset.lower(),
                  reasoning=v["reasoning"], lane="updown_5m",
                  meta={"context": ctx, "shadow": True})
    return out


# ── cache for the dashboard (so a page load never triggers a brain call) ─────
def _cache_path() -> str:
    import os
    return os.path.join(ledger._state_dir(), "updown.json")


def load(now: Optional[float] = None) -> Dict[str, Any]:
    """Latest reads the refresher wrote. Always renderable; `status: empty` when
    the 5-min job has not run yet. `stale` once a read's window has closed."""
    import os
    now = time.time() if now is None else now
    empty = {"status": "empty", "generated_at": None, "reads": []}
    try:
        with open(_cache_path()) as fh:
            payload = json.load(fh)
    except Exception:
        return empty
    if not isinstance(payload, dict):
        return empty
    for r in payload.get("reads") or []:
        end = r.get("end_date") or ""
        try:
            import calendar
            end_s = calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S"))
            r["stale"] = now > end_s
            r["seconds_left"] = max(0, int(end_s - now))
        except Exception:
            r["stale"] = True
            r["seconds_left"] = 0
    gen = int(payload.get("generated_at") or 0)
    payload["age_s"] = int(now - gen) if gen else None
    payload["status"] = "ok"
    return payload


def refresh(assets: Optional[List[str]] = None, brain: Any = None,
            record: bool = True, now: Optional[float] = None,
            http_get: Callable[[str], Any] = _curl_get,
            kline_runner: Optional[Callable] = None) -> Dict[str, Any]:
    """Read every configured asset's current window, write the cache. Used by the
    5-min scheduler job and callable by hand."""
    import os
    assets = assets or ["btc"]
    now = time.time() if now is None else now
    reads = [analyze(a, brain=brain, now=now, record=record,
                     http_get=http_get, kline_runner=kline_runner) for a in assets]
    payload = {"generated_at": int(now), "reads": reads}
    try:
        os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, _cache_path())
    except Exception:
        pass
    return payload


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="AI read on the current 5-min up/down window(s).")
    ap.add_argument("--assets", default="btc", help="comma list, e.g. btc,eth,sol")
    ap.add_argument("--no-record", action="store_true")
    args = ap.parse_args()
    p = refresh([a.strip() for a in args.assets.split(",") if a.strip()],
                record=not args.no_record)
    for r in p["reads"]:
        up = f"{r['up_prob']:.2f}" if r["up_prob"] is not None else "—"
        mk = f"{r['mkt_up']:.2f}" if r["mkt_up"] is not None else "—"
        print(f"[{r['asset']}] {r['slug']} AI_up={up} mkt_up={mk} "
              f"edge={r['edge']} :: {r['reasoning'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
