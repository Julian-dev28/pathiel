"""The live rule against the backtest's own walker, on seeded synthetic
candles: at every fill `fib_retrace_test.walk` makes for the sleeve's
variant, `rule.plan_entry`, given only what a live tick has at that bar's
open, must name the same entry level, stop, target and leverage. This is
the proof that the sleeve trades the rule that was tested and not a
re-implementation that drifted. No network."""
from __future__ import annotations

import dataclasses
import random
from typing import List

import pytest

from pathiel.models.types import Candle
from services.fib_sleeve import rule
from services.fib_sleeve.config import SleeveConfig
from services.fib_sleeve.tests import _scenario as S
from services.strategies.evals import fib_retrace_test as F
from services.strategies.evals.trend_gate_test import base_params

CFG = SleeveConfig()
DAYS = 80


def series(seed: int) -> List[Candle]:
    """A random walk with a drift that changes daily: enough trend for the
    zigzag to make legs several ATR long."""
    rnd = random.Random(seed)
    px, drift, out = 100.0, 0.0, []
    t0 = S.T0 - DAYS * 24 * S.HOUR
    for i in range(DAYS * 96):
        if i % 96 == 0:
            drift = rnd.choice((-1, 1)) * rnd.uniform(0.0, 0.0012)
        o = px
        px = max(20.0, px * (1 + drift + rnd.gauss(0, 0.0012)))
        hi = max(o, px) * (1 + abs(rnd.gauss(0, 0.0004)))
        lo = min(o, px) * (1 - abs(rnd.gauss(0, 0.0004)))
        out.append(Candle(t=t0 + i * S.Q, o=round(o, 3), h=round(hi, 3), l=round(lo, 3), c=round(px, 3), v=1))
    return out


def to_hours(b15: List[Candle]) -> List[Candle]:
    out = []
    for i in range(0, len(b15) - 3, 4):
        ch = b15[i:i + 4]
        out.append(Candle(t=ch[0].t, o=ch[0].o, h=max(c.h for c in ch), l=min(c.l for c in ch), c=ch[-1].c, v=4))
    return out


@pytest.mark.parametrize("seed", [1, 2, 7])
def test_live_plan_equals_the_walkers_fill(seed):
    b15 = series(seed)
    hs = to_hours(b15)
    ctx = F.build_ctx(b15, hs)
    variant = F.Variant(f"zz{CFG.zigzag_k:g}", CFG.direction, CFG.level, CFG.entry, CFG.stop, CFG.target,
                        CFG.ratchet)
    assert variant.name == CFG.variant_name
    params = dataclasses.replace(base_params(), tick_size=CFG.tick_size)
    res = F.walk(ctx, variant, params, 96 * 5, len(b15) - 1, None, None, CFG.min_r)
    paths = res["paths"]
    assert len(paths) >= 8, "the series must make the walker trade, or this proves nothing"
    for q in paths:
        j = q["i"]
        now = b15[j].t
        plan = rule.plan_entry(CFG, rule.closed(hs, S.HOUR, now), b15[:j], b15[j].o, now,
                               params.max_leverage, resting=True)
        assert plan["ok"], (seed, j, plan.get("reason"))
        assert (1 if plan["side"] == "long" else -1) == q["side"]
        assert plan["stop"] == pytest.approx(q["stop"], abs=1e-9)
        assert plan["target"] == pytest.approx(q["target"], abs=1e-9)
        assert plan["leverage"] == q["lev"]
        gapped = q["side"] * (b15[j].o - plan["entry"]) <= 0
        if not gapped:
            assert plan["entry"] == pytest.approx(q["fill"], abs=1e-9)   # filled at the level itself


def test_the_walker_never_fills_where_the_live_rule_sees_a_spent_level():
    """The converse on one series: once a swing's trade is over, the walker
    never trades that swing again, and the live rule must not want to either
    when the level was traded through by a tick."""
    b15 = series(3)
    hs = to_hours(b15)
    ctx = F.build_ctx(b15, hs)
    variant = F.Variant("zz2", "with", 0.382, "limit", "atr1", 0.618, "be75")
    params = dataclasses.replace(base_params(), tick_size=CFG.tick_size)
    paths = F.walk(ctx, variant, params, 96 * 5, len(b15) - 1, None, None, CFG.min_r)["paths"]
    checked = 0
    for q in paths:
        j, x = q["i"], q["x"]
        level = F.fib_price(ctx.swings["zz2"][j].origin, ctx.swings["zz2"][j].extreme, CFG.level)
        through = q["side"] * (min(c.l for c in b15[j:x + 1]) - level) <= -CFG.tick_size if q["side"] > 0 \
            else q["side"] * (max(c.h for c in b15[j:x + 1]) - level) <= -CFG.tick_size
        after = x + 1
        if not through or after >= len(b15) or ctx.swings["zz2"][after] is None \
                or ctx.swings["zz2"][after].key != ctx.swings["zz2"][j].key:
            continue
        now = b15[after].t
        plan = rule.plan_entry(CFG, rule.closed(hs, S.HOUR, now), b15[:after], b15[after].o, now,
                               params.max_leverage)
        assert not plan["ok"] and plan["reason"] in ("level_spent", "weekend", "reopen_bar")
        checked += 1
    assert checked >= 1
