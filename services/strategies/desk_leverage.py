"""The leverage cap per desk.

Until 2026-11-19 one number capped every new entry: the risk profile's
`max_leverage` (20x on the live account). Operator, 2026-11-19: "ADD A
S&P500 MARKET STRATEGY 50X". The S&P desk trades at 50x; oil and every
other desk stay on the profile's cap, exactly as before. HYPE joined
2026-11-20 at 10x -- the venue's own max for that coin, not a choice.

    DESK_MAX_LEVERAGE = {"sp500": 50.0, "hype": 10.0}      # the code default
    "desk_max_leverage": {"sp500": 25}                     # agent config, wins

A desk with no entry in either place has NO cap of its own: `cap_for_coin`
answers None and the caller keeps the risk profile's `max_leverage`. So the
oil desk cannot change by anything in this module short of the operator
writing `"desk_max_leverage": {"oil": ...}` into his own config
(test_desk_leverage.py pins that).

A desk's cap applies to the coin that is the desk's PRIMARY instrument (the
only coin a desk's tick trades). The venue's own max leverage still bounds
it at the call sites (`fit_leverage`, `size_trade`), and place_bracket's
half-liquidation rule runs at whatever leverage results.

Fail closed: a cap that is not a number in [1, 50] is ignored, which leaves
the (lower) profile cap in force. Pure apart from the optional config read.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from contracts import runtime
from services.strategies.desks import registry_spec

CONFIG_KEY = "desk_max_leverage"
DESK_MAX_LEVERAGE: Dict[str, float] = {"sp500": 50.0, "hype": 10.0}
MIN_CAP, MAX_CAP = 1.0, 50.0        # contracts.risk.models.RiskProfile.max_leverage's own bounds


def _valid(raw: Any) -> Optional[float]:
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        return None
    return cap if MIN_CAP <= cap <= MAX_CAP else None


def desk_caps(config: Optional[Mapping[str, Any]] = None) -> Dict[str, float]:
    """{desk: cap} in force: the code defaults, overridden per desk by the
    config's `desk_max_leverage`. Invalid entries are dropped. A public
    install gets no code default: its 3x profile cap is only ever lifted by
    a cap the user saved in their own config."""
    out = {} if runtime.is_public() else dict(DESK_MAX_LEVERAGE)
    saved = (config or {}).get(CONFIG_KEY)
    if isinstance(saved, Mapping):
        for desk, raw in saved.items():
            cap = _valid(raw)
            if cap is not None and isinstance(desk, str):
                out[desk] = cap
    return out


def _config(config: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if config is not None:
        return config
    try:
        from pathiel.agents.config_store import read_agent_config
        return read_agent_config() or {}
    except Exception:  # noqa: BLE001 - no config means the code defaults
        return {}


def cap_for_coin(coin: str, config: Optional[Mapping[str, Any]] = None) -> Optional[float]:
    """The cap of the desk whose primary instrument is `coin`, or None when
    no such desk has one (the caller keeps the risk profile's cap). With
    `config` None the saved agent config is read."""
    for desk, cap in desk_caps(_config(config)).items():
        spec = registry_spec(desk)
        if spec and any(i.coin == coin and i.role == "primary" for i in spec.instruments):
            return cap
    return None


def entry_leverage(coin: str, profile_cap: Optional[float],
                   config: Optional[Mapping[str, Any]] = None) -> Optional[float]:
    """The leverage a new entry on `coin` sizes at: its desk's cap when the
    desk has one, else `profile_cap` unchanged (None stays None)."""
    cap = cap_for_coin(coin, config)
    return cap if cap is not None else profile_cap


def profile_for_coin(profile: Any, coin: str, config: Optional[Mapping[str, Any]] = None) -> Any:
    """The risk profile a trade on `coin` sizes with: `profile` itself (the
    same object) unless the coin's desk has its own cap, then a copy whose
    `max_leverage` is that cap. Everything else in the profile is kept."""
    cap = cap_for_coin(coin, config)
    if cap is None:
        return profile
    return profile.model_copy(update={"max_leverage": cap})


def effective_cap(coin: str, config: Optional[Mapping[str, Any]] = None) -> Optional[float]:
    """The leverage cap a new entry on `coin` is actually held to right now,
    fully resolved to a number: the coin's own desk cap (desk_caps) when it
    has one (sp500 50x, hype 10x), else the account's own saved risk
    profile cap (services.strategies.desks.profile_leverage_cap) -- oil has
    no entry of its own in DESK_MAX_LEVERAGE, so without this fallback a
    caller would have to already know to pass the right number in, and the
    2026-11-20 bug report is exactly a caller that didn't: the Desk page
    hardcoded 20x (oil's number) for every desk, including HYPE's 10x.
    Backs GET /api/desks/active's `leverage_cap` -- the Desk page's per-desk
    leverage picker and the call card's "the desk's rule" line."""
    from services.strategies.desks import profile_leverage_cap
    return entry_leverage(coin, profile_leverage_cap(), config)


__all__ = ["CONFIG_KEY", "DESK_MAX_LEVERAGE", "cap_for_coin", "desk_caps", "entry_leverage", "profile_for_coin",
          "effective_cap"]
