"""AI-only scan — hand the whole board to the LLM and let it decide.

The normal engine gates every paid AI call behind cheap deterministic filters:
a trigger score, `analyze_perception` CONFIRMED, the runner pre-block. That is
the right default — most scans are noise and the gates save tokens. This module
is the deliberate opposite, an operator experiment (2026-07-25 directive):

    NO TA gate. NO volume/breakout filter. Raw board -> LLM -> verdict.

The bet under test: does the model's own judgment, given the unfiltered tape,
find trades the TA gates throw away? We cannot answer that while the gates
decide what the model is even allowed to see. So this path shows it everything.

What it does NOT sidestep is risk. Every LONG/SHORT still routes through
`maybe_execute`, so margin floor, the daily-loss kill, notional caps, leverage
bounds and order-safety are all in force. "Sidestep TA" means the SIGNAL gate,
never the RISK gate.

Token discipline: the board goes to the model as ONE batched call in a dense
fixed-schema table (`compact_line`), not one call per coin and not JSON. N
markets, one request. The reply is one compact line per actionable market.

Shadow-first, like every book here: `place=false` (default) records verdicts to
the `ai_only` shadow ledger and executes nothing. Flip `place=true` to arm it;
size is small and bounded by config. Config is hot-read — no restart.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from hermes_trader.agents import shadow_ledger

logger = __import__("logging").getLogger(__name__)

_TS_PATH = os.path.join(
    os.environ.get("HERMES_STATE_DIR")
    or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".state"),
    ".ai_only_scan_ts",
)
BOOK = "ai_only"

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "place": False,            # False = shadow record only; True = route to executor
    "interval_min": 30,
    "min_volume_usd": 0,       # 0 = raw board, no volume filter (the point of the mode)
    "max_markets": 40,         # blob + execution bound; keeps tokens and risk finite
    "min_confidence": 0.7,
    "web_search": False,
    "equity_fraction_per_trade": 0.05,
    "leverage": 5,
    "stop_pct": 0.08,
    "tp_pct": 0.16,
    "allow_shorts": True,
}

# LLMglish schema: a one-line legend the model reads once, then a dense grid.
# Columns are chosen for signal-per-token: 24h move, $vol, funding, OI, price.
_SCHEMA = "sym|d24=%chg24h|v=$Mvol24h|fund=fundingBps|oi=$Moi|px=mid"

_SYS = (
    "You are a discretionary crypto perp trader. You are handed the WHOLE board as "
    "a dense table — NO technical filter has pre-selected anything, you see every "
    "market. Pick only the ones where you have a real directional thesis for a "
    "multi-hour to multi-day hold; skip the rest (most of them). Be selective: a "
    "blank answer is correct if nothing stands out. For each pick output ONE line, "
    "nothing else, in this exact format:\n"
    "  SYM L|S 0.CONF three word reason\n"
    "SYM is the symbol verbatim (may contain ':'). L=long, S=short. CONF is your "
    "probability the trade is right, 0.50-0.99. No preamble, no table, no "
    "trailing commentary — only the pick lines. Then, as the VERY LAST line, emit "
    'exactly this JSON object and nothing after it: {"verdict":"DONE"}'
)
# The claude_cli brain drops any reply without a parseable {"verdict":...} object
# (ai_brain._contains_parseable_verdict_json). The required final sentinel line
# makes the batch reply pass that validator; parse_verdicts reads the pick lines
# above it and ignores the sentinel.


def _cfg(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = (config or {}).get("ai_only_mode") or {}
    return {**DEFAULTS, **raw} if isinstance(raw, dict) else dict(DEFAULTS)


def pct_24h(row: Dict[str, Any]) -> float:
    prev = float(row.get("prevDayPx") or 0)
    mid = float(row.get("midPx") or row.get("markPx") or 0)
    if prev <= 0 or mid <= 0:
        return 0.0
    return (mid - prev) / prev * 100.0


def compact_line(row: Dict[str, Any]) -> str:
    """One dense row. Numbers only, space-separated, fixed order per `_SCHEMA`.
    Millions for vol/OI, basis points for funding — small integers cost fewer
    tokens than raw floats and carry the same decision content."""
    vol_m = float(row.get("dayNtlVlm") or 0) / 1e6
    oi_m = float(row.get("openInterest") or 0) * float(row.get("midPx") or 0) / 1e6
    fund_bps = float(row.get("funding") or 0) * 1e4
    px = float(row.get("midPx") or row.get("markPx") or 0)
    return (f"{row.get('coin')} {pct_24h(row):+.1f} {vol_m:.0f} "
            f"{fund_bps:+.1f} {oi_m:.0f} {px:g}")


def eligible(universe: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Only a SAFETY floor is applied, never a signal filter: drop priceless/dead
    rows and (optionally) a volume floor the operator can set to 0. Ranked by
    24h volume so the blob's cap keeps the most liquid, most fillable names."""
    floor = float(cfg.get("min_volume_usd") or 0)
    rows = [r for r in universe
            if float(r.get("midPx") or r.get("markPx") or 0) > 0
            and float(r.get("dayNtlVlm") or 0) >= floor]
    rows.sort(key=lambda r: float(r.get("dayNtlVlm") or 0), reverse=True)
    return rows[: int(cfg.get("max_markets") or 40)]


def build_prompt(rows: List[Dict[str, Any]]) -> str:
    lines = "\n".join(compact_line(r) for r in rows)
    return (f"SCHEMA: {_SCHEMA}\n"
            f"BOARD ({len(rows)} markets):\n{lines}\n\n"
            'Your picks (pick lines, then the {"verdict":"DONE"} sentinel line):')


def parse_verdicts(text: str, valid: Optional[set] = None) -> List[Dict[str, Any]]:
    """Parse the model's compact reply into verdict rows. Tolerant: skips the
    END sentinel, blank lines, and anything not shaped like a pick. When `valid`
    is given, a symbol the model invented (hallucination) is dropped."""
    out: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 3:
            continue
        sym, side_tok, conf_tok = parts[0], parts[1].upper(), parts[2]
        if side_tok not in ("L", "S"):
            continue
        try:
            conf = float(conf_tok)
        except ValueError:
            continue
        if not (0.0 <= conf <= 1.0):
            continue
        if valid is not None and sym not in valid:
            continue
        out.append({"coin": sym, "verdict": "LONG" if side_tok == "L" else "SHORT",
                    "side": "long" if side_tok == "L" else "short",
                    "confidence": conf,
                    "reason": parts[3].strip() if len(parts) > 3 else ""})
    return out


def to_analysis(v: Dict[str, Any], row: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build the analysis dict `maybe_execute` consumes. `strategy_book` is set so
    the books-only entry gate admits it (main-engine AI entries are disabled);
    every downstream RISK gate still runs. entry = mid, stop/tp from config."""
    mid = float(row.get("midPx") or row.get("markPx") or 0)
    stop_pct = float(cfg.get("stop_pct") or 0.08)
    tp_pct = float(cfg.get("tp_pct") or 0.16)
    long = v["verdict"] == "LONG"
    stop = mid * (1 - stop_pct) if long else mid * (1 + stop_pct)
    tp = mid * (1 + tp_pct) if long else mid * (1 - tp_pct)
    return {
        "id": str(uuid.uuid4()),
        "coin": v["coin"], "verdict": v["verdict"], "side": v["side"],
        "confidence": float(v["confidence"]),
        "entryPx": mid, "stopPx": round(stop, 10), "tpPx": round(tp, 10),
        "reasoning": v.get("reason", ""),
        "composite_score": 0.0,               # no TA — this is the whole point
        "strategy_book": BOOK,
        # The executor sizes strategy books from these OVERRIDE keys, not from a
        # bare `leverage`/`equity_fraction` — set the ones it actually reads so
        # ai_only honors its own risk params instead of the global 12x / book frac.
        "strategy_book_equity_frac_override": float(cfg.get("equity_fraction_per_trade") or 0.05),
        "leverage_override": int(cfg.get("leverage") or 5),
        "source": "ai_only", "ai_brain_provider": "ai_only",
    }


def _read_ts(path: str = _TS_PATH) -> float:
    try:
        with open(path) as fh:
            return float(fh.read().strip() or 0)
    except Exception:
        return 0.0


def _write_ts(now: float, path: str = _TS_PATH) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(str(int(now)))
    except Exception:
        pass


def due(cfg: Dict[str, Any], last_ts: float, now: float) -> bool:
    return (now - last_ts) >= float(cfg.get("interval_min") or 30) * 60.0


def run_once(universe: List[Dict[str, Any]], config: Dict[str, Any],
             execute_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
             brain: Any = None, record_fn: Callable = shadow_ledger.record,
             now: Optional[float] = None) -> Dict[str, Any]:
    """One board read: build the blob, one batched brain call, parse, record
    every actionable verdict to the shadow ledger, and (if place=true) route the
    ones clearing min_confidence through execute_fn. Returns a summary dict."""
    cfg = _cfg(config)
    now = time.time() if now is None else now
    rows = eligible(universe, cfg)
    if not rows:
        return {"scanned": 0, "picks": 0, "recorded": 0, "placed": 0}
    if brain is None:
        from hermes_trader.agents.ai_brain import get_brain
        brain = get_brain()
    by_coin = {r["coin"]: r for r in rows}
    try:
        text = brain.complete(_SYS, build_prompt(rows), web_search=bool(cfg.get("web_search")))
    except Exception as exc:
        logger.warning(f"[ai-only] brain call failed: {exc}")
        return {"scanned": len(rows), "picks": 0, "recorded": 0, "placed": 0, "error": str(exc)}
    picks = parse_verdicts(str(text or ""), valid=set(by_coin))
    if not cfg.get("allow_shorts", True):
        picks = [p for p in picks if p["verdict"] == "LONG"]
    recorded = placed = 0
    results: List[Dict[str, Any]] = []
    for v in picks:
        row = by_coin[v["coin"]]
        record_fn(BOOK, coin=v["coin"], side=v["side"],
                  entry_ref_px=float(row.get("midPx") or row.get("markPx") or 0),
                  horizon_days=1.0, stop_pct=float(cfg.get("stop_pct") or 0.08),
                  meta={"confidence": v["confidence"], "reason": v.get("reason", ""),
                        "d24": round(pct_24h(row), 2), "placed": False})
        recorded += 1
        res = None
        if cfg.get("place") and v["confidence"] >= float(cfg.get("min_confidence") or 0.7) \
                and execute_fn is not None:
            res = execute_fn(to_analysis(v, row, cfg))
            if isinstance(res, dict) and res.get("executed"):
                placed += 1
        results.append({"coin": v["coin"], "verdict": v["verdict"],
                        "confidence": v["confidence"], "result": res})
    return {"scanned": len(rows), "picks": len(picks), "recorded": recorded,
            "placed": placed, "results": results}


def maybe_run(config: Dict[str, Any], universe: List[Dict[str, Any]],
              positions: Any = None,
              execute_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
              brain: Any = None, now: Optional[float] = None,
              ts_path: str = _TS_PATH) -> Optional[Dict[str, Any]]:
    """Loop entrypoint. No-op unless `ai_only_mode.enabled` and the interval has
    elapsed. Never raises into the loop; persists its own last-run timestamp so
    the 30-min cadence survives restarts. `positions` is accepted for a uniform
    book-hook signature and currently unused (entries are net-new)."""
    cfg = _cfg(config)
    if not cfg.get("enabled"):
        return None
    now = time.time() if now is None else now
    if not due(cfg, _read_ts(ts_path), now):
        return None
    _write_ts(now, ts_path)
    try:
        summary = run_once(universe, config, execute_fn=execute_fn, brain=brain, now=now)
    except Exception as exc:
        logger.warning(f"[ai-only] scan failed (non-fatal): {exc}")
        return {"error": str(exc)}
    logger.info(f"[ai-only] scanned {summary['scanned']} picks={summary['picks']} "
                f"recorded={summary['recorded']} placed={summary['placed']} "
                f"(place={cfg.get('place')})")
    return summary
