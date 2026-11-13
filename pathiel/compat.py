"""Backwards compatibility for the pathia -> pathiel rename.

The project was renamed. Two things it named are not ours to rename unilaterally,
because other people's files already point at them:

  the environment   65 distinct PATHIA_* variables live in .env.local, in
                    `fly secrets`, and in the Vercel project. Renaming the
                    defaults without accepting the old names would have silently
                    fallen back to built-in defaults on every one of them —
                    which for PATHIA_STATE_DIR means a read-only directory and
                    for PATHIA_AUTH_NONCE_SECRET means no sign-in at all. A
                    config that stops being read is far worse than one that
                    errors, because everything keeps running and quietly means
                    something else.

  the state files   ~/.pathia-session-log.jsonl is 8600 events of real trading
                    history. The dashboard derives equity, P&L, the funnel and
                    every closed trade from it. A renamed default would not lose
                    the file; it would orphan it, and the dashboard would render
                    a healthy, empty, entirely wrong account.

So both old spellings keep working. New deployments get the new names, existing
ones are not asked to do anything, and this module is the only place that knows
the project used to be called something else.

Import it before anything reads the environment — `pathiel/__init__.py` does.
"""

from __future__ import annotations

import os
from typing import Optional

OLD_PREFIX = "PATHIA_"
NEW_PREFIX = "PATHIEL_"


def mirror_environment(env: Optional[dict] = None) -> int:
    """Make PATHIA_X and PATHIEL_X the same variable, whichever was set.

    Mirrored in both directions so a mixed environment — an old .env.local
    beside a new Vercel variable — resolves the same way no matter which module
    happens to read which name. An explicitly set value is never overwritten:
    setting both to different values is a mistake, and the one the operator
    typed most recently is not knowable here, so neither wins by surprise.

    Returns the number of keys it filled in, for the tests.
    """
    env = os.environ if env is None else env
    filled = 0
    for key, value in list(env.items()):
        if key.startswith(OLD_PREFIX):
            twin = NEW_PREFIX + key[len(OLD_PREFIX):]
        elif key.startswith(NEW_PREFIX):
            twin = OLD_PREFIX + key[len(NEW_PREFIX):]
        else:
            continue
        if twin not in env:
            env[twin] = value
            filled += 1
    return filled


def legacy_path(new_path: str, old_path: str) -> str:
    """Prefer `new_path`, but use `old_path` when only that one exists.

    Deliberately not a migration. Moving a file the trading loop may have open
    is a worse failure than reading it where it lies, and a rename that silently
    relocates 8600 events gives the operator nothing to undo. Point the env var
    at whichever you want, or move it yourself when the loop is stopped.
    """
    if os.path.exists(new_path):
        return new_path
    if os.path.exists(old_path):
        return old_path
    return new_path


def resolve_state_file(env_var: str, new_default: str, old_default: str) -> str:
    """The full precedence for a state file: env first, then whichever exists.

    `env_var` is checked under both spellings, because mirror_environment has
    already run by the time anything calls this.
    """
    explicit = os.environ.get(env_var)
    if explicit:
        return explicit
    return legacy_path(new_default, old_default)


# Runs at import. Everything below `pathiel/` reads the environment at module
# scope, so this has to happen before any of them are imported — see
# pathiel/__init__.py.
mirror_environment()
