#!/usr/bin/env python3
"""Backtest: does a market-regime gate on SHORT entries improve EV?

Operator question (2026-07-22): the book is ~100% short and bleeding in a
rising market. Would gating new shorts to down-regime tape be EV+?

Method: pool every SHORT signal across the live short books' ledgers, tag each
by the market regime at its signal time (BTC 7d for crypto, the xyz index 7d
for tokenized equities — the operator's "btc up/down or snp up/down"), grade
each forward with the production lookahead-safe grader at the live 6% stop, and
split EV by regime. The gate is validated EV+ only if UP-regime shorts are
clearly worse than DOWN-regime — otherwise gating forfeits good trades.

This is deliberately the AGGREGATE portfolio test, not a per-book one: the
operator's problem is portfolio-level (all-short in an uptrend), and an earlier
single-book test found young_mover_short regime-INDEPENDENT — so the honest
question is whether the WHOLE short book benefits from a regime gate.
"""
from __future__ import annotations

import os
import sys
import time
import random
import statistics
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_ENV = _REPO / ".env.local"
if _ENV.is_file():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from hermes_trader.agents import shadow_ledger as SL  # noqa: E402
from hermes_trader.client.hl_client import fetch_hl_candles  # noqa: E402

# Books whose signals are (or invert to) SHORTs. news_catalyst is the big
# attention-fade dataset — its recorded LONGs invert to the validated short.
_SHORT_BOOKS = ["young_mover_short", "mover_pass_short", "news_surge_short",
                "engulf_short", "crash_continue_div_short"]
_INVERT_TO_SHORT = ["news_catalyst", "mover_pass"]   # recorded long -> short

_REGIME_LOOKBACK = 7   # days
_STOP_PCT = 6.0        # the live geometry


def _regime_series(coin: str):
    proxy = "xyz:XYZ100" if coin.startswith("xyz:") else "BTC"
    return proxy


_bars_cache: Dict[str, list] = {}


def _bars(coin: str):
    if coin not in _bars_cache:
        try:
            _bars_cache[coin] = fetch_hl_candles(coin, "1d", 120)
        except Exception:
            _bars_cache[coin] = []
    return _bars_cache[coin]


def _regime_at(coin: str, sig_t: int) -> Optional[str]:
    """up/down: sign of the proxy's return over the LOOKBACK days ending at the
    signal bar. Strictly uses bars at/before sig_t — no lookahead."""
    proxy = _regime_series(coin)
    bars = _bars(proxy)
    prior = [b for b in bars if int(getattr(b, "t", 0)) <= int(sig_t)]
    if len(prior) < _REGIME_LOOKBACK + 1:
        return None
    c0 = float(getattr(prior[-_REGIME_LOOKBACK - 1], "c", 0) or 0)
    c1 = float(getattr(prior[-1], "c", 0) or 0)
    if c0 <= 0:
        return None
    return "up" if c1 > c0 else "down"


def main() -> int:
    spec = importlib.util.spec_from_file_location(
        "sis", str(_REPO / "scripts" / "shadow_inverse_status.py"))
    sis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sis)
    now = int(time.time() * 1000)

    # pool all short signals, tagged by regime-at-entry
    pooled: List[dict] = []
    for book in _SHORT_BOOKS:
        for r in SL.load(book):
            if str(r.get("side")) == "short":
                pooled.append(dict(r, stop_pct=_STOP_PCT))
    for book in _INVERT_TO_SHORT:
        for r in sis.inverse_records(SL.load(book)):   # long -> short
            pooled.append(dict(r, stop_pct=_STOP_PCT))

    # dedup and split by regime
    kept, _ = SL.dedup_episodes(pooled)
    by_regime: Dict[str, List[dict]] = {"up": [], "down": []}
    for r in kept:
        reg = _regime_at(str(r.get("coin") or ""), int(r.get("signal_bar_t") or 0))
        if reg:
            by_regime[reg].append(r)

    print(f"# regime-gate backtest — {len(kept)} pooled short signals "
          f"({len(by_regime['up'])} up-regime, {len(by_regime['down'])} down-regime)")
    print(f"# gate is EV+ ONLY if UP-regime shorts are clearly worse than DOWN\n")
    print(f"{'regime at entry':<18} {'n':>4} {'short EV6bps':>12} {'win':>6} {'median':>8}")
    print("-" * 54)

    results = {}
    for reg in ("down", "up"):
        rows = by_regime[reg]
        if len(rows) < 8:
            print(f"{reg:<18} {len(rows):>4}  (too few)")
            continue
        fwd, fund, cache = sis._make_fetchers(rows, now)
        g = SL.grade_records(rows, fwd, now_ms=now, fetch_funding=fund, dedup=False)
        n = g.get("n", 0)
        ev = (g.get("slip6") or {}).get("mean_pct")
        win = (g.get("slip6") or {}).get("win")
        det = [d["ret_pct"] for d in (g.get("detail") or [])]
        med = statistics.median(det) if det else None
        results[reg] = ev
        print(f"{reg:<18} {n:>4} {ev:>+11.2f}% {win:>6} "
              f"{(f'{med:+.2f}%' if med is not None else '-'):>8}")

    print()
    if "up" in results and "down" in results:
        gap = results["down"] - results["up"]
        print(f"# down-minus-up EV gap: {gap:+.2f}pp")
        if results["up"] < 0 and results["down"] > results["up"] and gap > 1.0:
            print("# VERDICT: gate is EV+ — up-regime shorts are worse; halting "
                  "them lifts book EV.")
        elif results["up"] >= 0:
            print("# VERDICT: gate NOT justified — shorts are still +EV even in "
                  "an up-regime; gating forfeits good trades.")
        else:
            print("# VERDICT: MARGINAL — some regime effect but not a clean split; "
                  "size-scale, don't hard-gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
