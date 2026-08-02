"""Optional AI pass over an already-computed trend read.

Hard rule: the model never produces a NUMBER that lands on the tab. Every
figure it sees was computed deterministically upstream; its only job is to
connect them — which flags corroborate which trend, what would invalidate the
read, what a trader should watch this week. If the LLM call fails, the tab is
unchanged except for a status line, because the deterministic half is the
product and this is the garnish.

Routed through the LOCAL Claude Code CLI (operator subscription), never a
hosted API — the project rule. Optional `WebSearch` for the catalyst pass.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

CLI = os.environ.get("CLAUDE_CLI_COMMAND", "claude")
MODEL = os.environ.get("TREND_AI_MODEL", "claude-opus-4-8")
TIMEOUT_S = float(os.environ.get("TREND_AI_TIMEOUT_S", "180"))

SYSTEM = """You are a markets analyst reading a PRE-COMPUTED trend report.

Rules, no exceptions:
- Every number in your output must be copied from the input. Never compute,
  estimate, or invent a price, percentage, probability or sample size.
- If the evidence in the report is weak, say it is weak. A report whose own
  backtest says the direction is a coin flip does not get a confident call.
- No hedging filler. Short declarative sentences.
- Name specific tickers / markets. "Some alts look strong" is worthless.

Reply with ONE json object and nothing else:
{"headline": "<=120 chars, the single most important thing this week",
 "narrative": "3-6 sentences tying the regime, the leaders/laggards and the flags together",
 "setups": [{"ticker": "<symbol or market>", "read": "<what the data says>",
             "trigger": "<the observable that would confirm it>",
             "invalidation": "<the observable that kills it>",
             "confidence": "low|medium|high"}],
 "watch": ["<dated catalyst or level to watch this week>", ...],
 "risks": ["<what would break this read>", ...]}"""


def _run(prompt: str, web_search: bool, model: str, timeout_s: float) -> str:
    args = [CLI, "-p", "--output-format", "json", "--max-turns",
            "8" if web_search else "1", "--tools", "WebSearch" if web_search else "",
            "--safe-mode", "--no-session-persistence", "--model", model]
    env = dict(os.environ)
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = model
    try:
        p = subprocess.run(args, input=prompt, capture_output=True, text=True,
                           timeout=timeout_s, env=env)
        return p.stdout or ""
    except Exception:
        return ""


def _unwrap(raw: str) -> str:
    """Pull the result body out of the CLI's JSON envelope."""
    if not raw:
        return ""
    try:
        env = json.loads(raw)
    except Exception:
        return raw
    if isinstance(env, dict):
        if env.get("is_error"):
            return ""
        return str(env.get("result") or "")
    return raw


def _parse(body: str) -> Optional[Dict[str, Any]]:
    """Last JSON object in the reply, tolerant of prose and code fences."""
    if not body:
        return None
    cleaned = re.sub(r"```(?:json)?", "", body)
    best = None
    depth = 0
    start = -1
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                chunk = cleaned[start:i + 1]
                try:
                    obj = json.loads(chunk)
                    if isinstance(obj, dict) and "headline" in obj:
                        best = obj
                except Exception:
                    pass
    return best


# ── prompt bodies (facts only, no interpretation) ────────────────────────────


def hl_prompt(payload: Dict[str, Any], top_n: int = 15) -> str:
    reg = payload.get("regime") or {}
    lines: List[str] = [
        "LANE: Hyperliquid perps, 7-day trend read.",
        f"REGIME: {reg.get('label')} | BTC 7d {reg.get('btc_ret_7d')}% ({reg.get('btc_label')}) "
        f"| breadth {reg.get('breadth_pct')}% green | {reg.get('trend_share_pct')}% trending "
        f"| dispersion {reg.get('dispersion_pct')}% | alt strength {reg.get('alt_strength_pct')}pp "
        f"| mean funding {reg.get('mean_funding_apr_pct')}% APR",
        "",
        "PER-COIN (7d ret / slope %day / r2 / efficiency / label / forecast p50 drift% / prob_up / flags):",
    ]
    for r in (payload.get("reads") or [])[:top_n]:
        f = r.get("forecast") or {}
        flags = ",".join(x["code"] for x in (r.get("flags") or [])[:4]) or "-"
        lines.append(
            f"{r['coin']}: 7d {r.get('ret_7d'):+.1f}% | slope {r.get('slope_pct_day'):+.2f}%/d "
            f"| r2 {r.get('r2')} | eff {r.get('efficiency')} | {r.get('label')} "
            f"| fc {f.get('drift_pct', 0):+.1f}% p(up) {f.get('prob_up', 0):.2f} | {flags}")
    bt = payload.get("eval") or {}
    if bt:
        lines += ["", f"THIS FORECASTER'S OWN BACKTEST: n={bt.get('n')} anchors, "
                      f"directional hit {bt.get('dir_hit')} ({bt.get('dir_edge_sigma')} sigma vs coin flip), "
                      f"band coverage {bt.get('coverage_80')} vs 0.80 nominal, "
                      f"beats_coinflip={bt.get('beats_coinflip')}."]
    lines += ["", "DETERMINISTIC OBSERVATIONS:"] + \
             [f"- {o}" for o in (payload.get("observations") or [])]
    return "\n".join(lines)


def updown_prompt(payload: Dict[str, Any]) -> str:
    pat = payload.get("patterns") or {}
    cal = payload.get("calibration") or {}
    live = payload.get("live") or {}
    lines = [
        "LANE: Polymarket BTC 5-minute up/down.",
        f"SAMPLE: {pat.get('n_windows')} resolved windows ({payload.get('sample_days')} days). "
        f"Base UP rate {pat.get('base_rate')} CI {pat.get('base_ci')} "
        f"(p vs coin flip {pat.get('base_p_vs_coinflip')}).",
        f"CONDITIONALS SURVIVING BONFERRONI: {len(pat.get('significant') or [])} — {pat.get('verdict')}",
    ]
    for fam in (pat.get("families") or []):
        top = (fam.get("rows") or [{}])[0]
        lines.append(f"  {fam['family']}: strongest bucket {top.get('bucket')} "
                     f"n={top.get('n')} rate={top.get('rate')} lift={top.get('lift_pp')}pp "
                     f"p_bonf={top.get('p_bonf')}")
    lines += ["",
              f"IN-WINDOW MODEL (random walk at minute 3 of 5): Brier {cal.get('brier')} "
              f"vs null {cal.get('brier_null')} ({cal.get('skill_pct')}% skill), "
              f"calibration error {cal.get('calibration_err_pp')}pp, n={cal.get('n')} — {cal.get('verdict')}",
              f"LIVE WINDOW: move {live.get('move_bp')}bp, {live.get('minutes_left')}min left, "
              f"model p(up) {live.get('p_up_randomwalk')}, book "
              f"{(live.get('market') or {}).get('bid')}/{(live.get('market') or {}).get('ask')}, "
              f"buy-UP edge {live.get('edge_up_pp')}pp, buy-DOWN edge {live.get('edge_down_pp')}pp, "
              f"actionable={live.get('actionable')} ({live.get('note')})",
              "NOTE: the market resolves on Chainlink BTC/USD; these windows are mined from "
              "Binance klines, so sub-5pp gaps are feed noise, not edge."]
    roll = payload.get("rolling") or []
    if roll:
        lines.append("DAILY UP-RATE: " + ", ".join(
            f"{d['date_et']} {d['up_rate']:.3f}" for d in roll[-7:]))
    return "\n".join(lines)


def politics_prompt(payload: Dict[str, Any]) -> str:
    brd = payload.get("board") or {}
    mom = payload.get("momentum_test") or {}
    lines = [
        "LANE: Polymarket political markets, 7-day probability trend.",
        f"BOARD: {brd.get('n')} markets, median absolute weekly move "
        f"{brd.get('median_abs_move_pp')}pp, labels {brd.get('label_counts')}",
        f"DRIFT NULL TEST: corr {mom.get('corr')} z {mom.get('fisher_z')} n {mom.get('n')} "
        f"usable={mom.get('usable')} — {mom.get('verdict')}",
        "",
        "MOVERS (question | now | 7d change | label | days to resolve):",
    ]
    for r in (payload.get("reads") or [])[:14]:
        lines.append(f"{r['question'][:78]} | {r['p_now']:.2f} | {r['delta_7d_pp']:+.1f}pp "
                     f"| {r['label']} | {r.get('days_left')}d")
    lines += ["", "DETERMINISTIC OBSERVATIONS:"] + \
             [f"- {o}" for o in (payload.get("observations") or [])]
    return "\n".join(lines)


def recorders_prompt(payload: Dict[str, Any]) -> str:
    s = payload.get("summary") or {}
    lines = [
        "LANE: zero-capital recorders, forward-graded.",
        f"SUMMARY: {s.get('n_books')} books, {s.get('n_graded')} with resolved signals, "
        f"verdicts {s.get('verdicts')}, mean EV {s.get('mean_ev_pct')}%/signal @12bps, "
        f"{s.get('total_resolved')} of {s.get('total_signals')} signals resolved.",
        "",
        "BOOKS (verdict | resolved | ev% @12bps | @25bps | win | 1st half | 2nd half):",
    ]
    for b in (payload.get("books") or [])[:25]:
        lines.append(f"{b['book']}: {b['verdict']} | n={b['resolved']} | {b.get('ev_pct')} | "
                     f"{b.get('ev25_pct')} | {b.get('win_rate')} | {b.get('ev_first')} | "
                     f"{b.get('ev_second')}{' | DECAYING' if b.get('decaying') else ''}")
    lines += ["", "POLYMARKET PAPER LANES:"]
    for lane, g in ((payload.get("scout") or {}).get("lanes") or {}).items():
        lines.append(f"{lane}: rows={g.get('rows')} resolved={g.get('n')} "
                     f"pnl/$={g.get('mean_pnl_per_$')} win={g.get('win_rate')} "
                     f"brier_ours={g.get('brier_llm')} brier_mkt={g.get('brier_mkt')}")
    lines += ["", "DETERMINISTIC OBSERVATIONS:"] + \
             [f"- {o}" for o in (payload.get("observations") or [])]
    lines += ["", "In `setups`, use the BOOK NAME as `ticker` and say whether the evidence "
                  "supports promoting it, leaving it, or killing it. Never recommend "
                  "promoting a book whose verdict is PENDING or REFUTED."]
    return "\n".join(lines)


BUILDERS = {"hl": hl_prompt, "updown": updown_prompt, "politics": politics_prompt,
            "recorders": recorders_prompt}


def analyze(lane: str, payload: Dict[str, Any], web_search: bool = False,
            model: str = MODEL, timeout_s: float = TIMEOUT_S,
            runner: Optional[Any] = None) -> Dict[str, Any]:
    """Run the AI pass for one lane. Never raises; failures return a status."""
    build = BUILDERS.get(lane)
    if not build:
        return {"status": "bad_lane", "lane": lane}
    t0 = time.time()
    prompt = f"{SYSTEM}\n\n{build(payload)}"
    raw = (runner or _run)(prompt, web_search, model, timeout_s)
    parsed = _parse(_unwrap(raw))
    if not parsed:
        return {"status": "failed", "lane": lane, "model": model,
                "elapsed_s": round(time.time() - t0, 1),
                "error": "no parseable JSON object from the CLI"}
    return {
        "status": "ok",
        "lane": lane,
        "model": model,
        "web_search": bool(web_search),
        "elapsed_s": round(time.time() - t0, 1),
        "generated_at": int(time.time()),
        "headline": str(parsed.get("headline") or "")[:200],
        "narrative": str(parsed.get("narrative") or ""),
        "setups": parsed.get("setups") or [],
        "watch": parsed.get("watch") or [],
        "risks": parsed.get("risks") or [],
    }
