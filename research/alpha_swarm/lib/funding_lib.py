"""Funding-dataset loader for the data-frontier swarm. Pairs with alpha_lib.

funding.json shape: {meta, funding: {coin: [[time_ms, fundingRate, premium], ...]}}
HL funding is HOURLY; fundingRate is the per-hour rate (a long pays it to a short
when positive). Daily carry ~= sum of 24 hourly rates.
"""
from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any

FUNDING = Path(os.environ.get(
    "HERMES_ALPHA_FUNDING",
    Path(__file__).resolve().parent.parent / "funding.json",
))

T, RATE, PREM = 0, 1, 2


def load_funding(path: Path | str = FUNDING) -> dict:
    d = json.loads(Path(path).read_text())
    d.setdefault("coins", d.get("meta", {}).get("coins", list(d.get("funding", {}).keys())))
    return d


def rows(d: dict, coin: str) -> list[list[float]]:
    return d["funding"].get(coin, []) or []


def rate_at(d: dict, coin: str, t_ms: int, tol_ms: int = 3_600_000) -> float | None:
    """Hourly funding rate at-or-just-before t_ms (no lookahead). None if none within tol."""
    rs = rows(d, coin)
    best = None
    for r in rs:
        if r[T] <= t_ms:
            best = r
        else:
            break
    if best is None or (t_ms - best[T]) > tol_ms:
        return None
    return best[RATE]


def cum_funding(d: dict, coin: str, start_ms: int, end_ms: int) -> float:
    """Sum of hourly funding rates over (start_ms, end_ms] — the carry a SHORT collects
    (longs pay shorts when funding > 0). Lookahead-safe if you pass completed timestamps."""
    return sum(r[RATE] for r in rows(d, coin) if start_ms < r[T] <= end_ms)


def trailing_funding(d: dict, coin: str, t_ms: int, hours: int) -> float | None:
    """Mean hourly funding over the `hours` ending at t_ms (a positioning/sentiment gauge)."""
    lo = t_ms - hours * 3_600_000
    xs = [r[RATE] for r in rows(d, coin) if lo < r[T] <= t_ms]
    return sum(xs) / len(xs) if xs else None
