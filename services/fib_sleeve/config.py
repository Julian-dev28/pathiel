"""The fib sleeve's settings: one frozen dataclass, defaults in code, an
optional operator override file in the state directory.

Two swing anchors, `swing`: "range24" (the DEFAULT since 2026-11-16) or
"zigzag" (the sleeve's first anchor, kept as an option). Only one is active
at a time; `direction`, `level`, `entry`, `stop`, `target` and `ratchet` are
shared knobs read against whichever swing is active.

**range24** (default): the operator, 2026-11-16 ~08:30Z, "for the fib make
it [the] low high range" - the swing anchor is the 24h range `services.
strategies.levels` already reports (the same low and high the Desk page's
Levels card and `scripts/oil_levels.py` show: `price_range` on the last 24
CLOSED 1h bars), not a zigzag pivot. Direction is the range's last leg: the
high after the low is an up-swing (buy the retrace), the low after the high
a down-swing (sell the retrace) - `services.strategies.evals.
fib_retrace_test.swing_from_window`, the backtest's own "range24" anchor,
called on that same window (`rule.current_swing_range24`), not re-derived.
The defaults are the best "24h range, with the swing, resting-limit entry"
row of `docs/strategy/FIB_RETRACE_TEST_2026-11-16.md`'s own grid
(`range24_with_L382_limit_atr1_T272_be50`: `fib_retrace_test.variants()`
narrowed to swing="range24", direction="with", entry="limit" and re-judged
on the doc's own cache, since the doc's own "best variant per family" table
only prints the ceiling across EVERY entry type, and that ceiling uses
"confirm", which this sleeve does not place): on the
Brent journal, beside the live book, at 6% risk, 10 trades, 50% wins,
+$117.11, max drawdown 22.6% (vs the live book's 27.9%), avg R +0.98. Beside
the replay: 20 trades, +$99.18. Beside WTI: 13 trades, +$73.75. Alone on
the Brent journal it loses (-$14.86 on 23 trades), and alone on both
208-day sets it loses (-$127.14 Brent, -$119.65 WTI): **verdict FAIL** (the
family's own bar in the doc: 10+ trades beside live, none pass or lean).
Worse evidence than the zigzag row below (FAIL means tried-and-failed, NOISE
means not enough trades to judge) - the operator asked for this anchor
anyway, same footing as running a NOISE-verdict rule small.

**zigzag** (kept as an option, no longer the default): the sleeve's first
row, the best 1h-zigzag row of the same doc (`zz2_with_L382_limit_atr1_
T618`, 3 trades, 3 wins, +$240.50 beside live on the Brent journal at 6%
risk, verdict NOISE) with the 75% break-even ratchet. On Brent the be50,
be75 and off twins of that row are the same trades to the cent (the ratchet
never changed an exit), so be75 was the row the operator asked for at the
number the doc prints. Switching `swing` to "zigzag" via the override file
does NOT restore `level`/`stop`/`target`/`ratchet` to these zigzag-tuned
values; override those too (0.382, "atr1", 0.618, "be75") for the exact row
above - every value is independently valid (part of the grid F.variants()
crossed), just not the one this doc names.

`enabled` is False in code. Only the override file can turn it on:
`<state dir>/fib_sleeve/config.json`, written by
`scripts/fib_sleeve_tick.py --enable`. An unknown key or a value outside
the tested grid is refused, so a typo cannot run an untested rule.

A resting entry or an open position already working under the OTHER swing
mode when `swing` changes is left alone, managed to completion with its own
levels (`services/fib_sleeve/rule.py`'s `resting_matches`/`other_swing_mode`,
`services/fib_sleeve/tick.py`'s flat-with-a-resting-entry branch); the
active `swing` only governs a brand NEW entry. An open position never reads
`cfg.swing` at all (the stop/target it manages are whatever already rests
on the venue; the break-even ratchet never reads the swing either), so it
is swing-change-safe by construction.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

SUB_ACCOUNT = "0x3d3b471f6f18f1e3018064112abb9bf6d46cc272"   # sub-account "pathiel" of the master wallet
DIR_NAME = "fib_sleeve"
_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")


class ConfigError(ValueError):
    """The override file asks for something the sleeve does not run."""


@dataclass(frozen=True)
class SleeveConfig:
    desk_id: str = "fib"
    coin: str = "xyz:BRENTOIL"
    account: str = SUB_ACCOUNT            # the account target: orders carry vaultAddress = this
    enabled: bool = False                 # nothing is placed, cancelled or moved while False
    # the rule (names and values are fib_retrace_test's)
    swing: str = "range24"                # "range24" (the 24h range) or "zigzag" (a 1h pivot); see module doc
    zigzag_k: float = 2.0                 # swing=="zigzag" only: a pivot confirmed after k x ATR14 on 1h bars
    direction: str = "with"               # buy a retrace of an up-swing, sell a retrace of a down-swing
    level: float = 0.382                  # the retracement the limit rests at
    entry: str = "limit"
    stop: str = "atr1"                    # the level less 1 x ATR14 (1h)
    target: float = 0.272                 # the -27.2% extension beyond the swing extreme
    ratchet: str = "be50"                 # stop to the entry once price covers 50% of the way to target
    # size
    risk_frac: float = 0.06               # of the sub-account's equity, lost at the stop
    leverage: int = 20                    # the cap
    leverage_fit: Tuple[float, ...] = (20.0, 15.0)
    min_r: float = 2.0                    # place_bracket's floor
    taker_fee: float = 0.00009            # the account's measured xyz fee, 0.90 bps
    slippage: float = 0.0003              # 3 bps on a stop fill, the sim's constant
    tick_size: float = 0.01
    # kill switch
    max_consecutive_losses: int = 3
    max_drawdown_frac: float = 0.20       # of the sub-account's starting equity
    starting_equity_usd: Optional[float] = None   # None: the equity at the first live tick

    @property
    def variant_name(self) -> str:
        """The row's name in fib_retrace_test's variants.csv: "range24" when
        that swing is active, "zz{k}" for the zigzag (fib_retrace_test.
        Variant.name's own convention, exactly)."""
        swing_tag = f"zz{self.zigzag_k:g}" if self.swing == "zigzag" else self.swing
        return (f"{swing_tag}_{self.direction}_L{self.level * 1000:.0f}_{self.entry}_{self.stop}"
                f"_T{self.target * 1000:.0f}_{self.ratchet}")

    def public(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def validate(cfg: SleeveConfig) -> SleeveConfig:
    from services.strategies.evals import fib_retrace_test as F
    problems = []
    if not _ADDRESS_RE.fullmatch(str(cfg.account)):
        problems.append("account must be a 0x address of 40 hex characters")
    if cfg.swing not in ("zigzag", "range24"):
        problems.append("swing must be 'zigzag' or 'range24'")
    if f"zz{cfg.zigzag_k:g}" not in F.ZZ_K:
        problems.append(f"zigzag_k {cfg.zigzag_k:g} was not tested (2, 3, 4)")
    if cfg.direction not in ("with", "long"):
        problems.append("direction must be 'with' or 'long'")
    if cfg.level not in F.FIBS:
        problems.append(f"level {cfg.level} is not one of {F.FIBS}")
    if cfg.entry != "limit":
        problems.append("only the resting-limit entry is built")
    if cfg.stop not in F.STOPS:
        problems.append(f"stop must be one of {F.STOPS}")
    if cfg.target not in F.TARGETS:
        problems.append(f"target must be one of {F.TARGETS}")
    if cfg.ratchet not in ("off", "be50", "be75"):
        problems.append("ratchet must be off, be50 or be75")
    if not 0 < cfg.risk_frac <= 0.06:
        problems.append("risk_frac must be in (0, 0.06]: 6% is the tested size and the ceiling")
    if not 1 <= int(cfg.leverage) <= 20:
        problems.append("leverage must be 1 to 20")
    if any(c <= 0 or c > cfg.leverage for c in cfg.leverage_fit):
        problems.append("leverage_fit values must be in (0, leverage]")
    if cfg.min_r < 2.0:
        problems.append("min_r cannot go under place_bracket's 2R floor")
    if cfg.max_consecutive_losses < 1 or not 0 < cfg.max_drawdown_frac <= 0.20:
        problems.append("kill switch: losses >= 1 and drawdown in (0, 0.20]")
    if cfg.starting_equity_usd is not None and cfg.starting_equity_usd <= 0:
        problems.append("starting_equity_usd must be positive")
    if problems:
        raise ConfigError("; ".join(problems))
    return cfg


def sleeve_dir(state_dir: Optional[str] = None) -> str:
    """The sleeve's own directory under the state directory (override file,
    kill-switch state, tick log). PATHIEL_STATE_DIR, else the project root,
    the same rule as pathiel.state.state_file."""
    if state_dir is None:
        state_dir = os.environ.get("PATHIEL_STATE_DIR") or os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(state_dir, DIR_NAME)


def override_path(state_dir: Optional[str] = None) -> str:
    return os.path.join(sleeve_dir(state_dir), "config.json")


def from_overrides(overrides: Dict[str, Any]) -> SleeveConfig:
    fields = {f.name for f in dataclasses.fields(SleeveConfig)}
    unknown = sorted(set(overrides) - fields)
    if unknown:
        raise ConfigError(f"unknown setting(s): {', '.join(unknown)}")
    clean = dict(overrides)
    if "leverage_fit" in clean:
        clean["leverage_fit"] = tuple(float(x) for x in clean["leverage_fit"])
    if "enabled" in clean and not isinstance(clean["enabled"], bool):
        raise ConfigError("enabled must be true or false")
    return validate(dataclasses.replace(SleeveConfig(), **clean))


def load(state_dir: Optional[str] = None) -> SleeveConfig:
    """Defaults, with the override file on top when it exists. A file that
    cannot be read as a JSON object, or that fails validation, raises: the
    sleeve never runs on a config it could not read."""
    path = override_path(state_dir)
    if not os.path.isfile(path):
        return validate(SleeveConfig())
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        raise ConfigError(f"{path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: must be a JSON object")
    return from_overrides(raw)


def write_override(changes: Dict[str, Any], state_dir: Optional[str] = None) -> SleeveConfig:
    """Merge `changes` into the override file (validated first) and return
    the resulting config. The only writer of that file."""
    path = override_path(state_dir)
    current: Dict[str, Any] = {}
    if os.path.isfile(path):
        with open(path) as fh:
            current = json.load(fh)
    merged = {**current, **changes}
    cfg = from_overrides(merged)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return cfg
