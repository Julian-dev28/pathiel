"""Small desk-type heuristics shared by sources/news.py and
sources/catalysts.py: "is this an oil desk" / "is this a crypto desk" /
"what tickers count as crypto."

Why this exists instead of a DeskSpec field: `contracts/strategies/models.py`
is owned by desk-core (services/intel may only make non-breaking additions,
and heuristic source-gating is not a contract concern — see
docs/overhaul/SWARM_RULES.md section 6). These are source-selection rules
internal to this service, not part of the desk/intel interface.
"""
from __future__ import annotations

import json
import os

from contracts.strategies.models import DeskSpec

UNLOCK_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "pathiel", "agents", "unlock_map.json",
)
# Fallback if pathiel/agents/unlock_map.json ever moves — that file is owned
# by another agent's lane (SWARM_RULES section 6), so this read is
# defensive and this constant is the floor, not the ceiling.
_CRYPTO_TICKERS_FALLBACK = frozenset({
    "BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "AVAX", "LINK", "ADA",
})


def crypto_tickers() -> frozenset:
    try:
        with open(UNLOCK_MAP_PATH) as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and raw:
            return frozenset(str(v).upper() for v in raw.values())
    except Exception:
        pass
    return _CRYPTO_TICKERS_FALLBACK


def instrument_tokens(spec: DeskSpec) -> list:
    """Instrument labels plus their coin ticker (HL-prefix stripped), e.g.
    `xyz:BRENTOIL` -> "BRENTOIL". Used both as extra news-query phrases and
    for desk-type sniffing."""
    toks = []
    for i in spec.instruments:
        toks.append(i.label)
        coin = i.coin.split(":", 1)[-1]
        if len(coin) >= 3:
            toks.append(coin)
    return toks


def is_oil_desk(spec: DeskSpec) -> bool:
    hay = (spec.id + " " + " ".join(instrument_tokens(spec))).lower()
    return any(k in hay for k in ("oil", "wti", "brent", "crude"))


def is_crypto_desk(spec: DeskSpec) -> bool:
    tickers = crypto_tickers()
    coins = {i.coin.split(":", 1)[-1].upper() for i in spec.instruments}
    labels = {i.label.upper() for i in spec.instruments}
    return bool(coins & tickers) or bool(labels & tickers)


def is_equity_desk(spec: DeskSpec) -> bool:
    """True if any instrument looks like a plain equity ticker rather than
    a commodity/crypto perp — used to gate the best-effort SEC EDGAR
    earnings-date catalyst. Deliberately crude: 1-5 uppercase letters,
    not a known crypto ticker, not one of the commodity instruments this
    service already knows by name."""
    known_non_equity = {"WTI", "BRENT", "CL", "BRENTOIL", "GOLD", "XAU"}
    tickers = crypto_tickers()
    for i in spec.instruments:
        coin = i.coin.split(":", 1)[-1].upper()
        label = i.label.upper()
        if coin in tickers or label in tickers:
            continue
        if coin in known_non_equity or label in known_non_equity:
            continue
        if coin.isalpha() and 1 <= len(coin) <= 5:
            return True
    return False
