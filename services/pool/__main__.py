"""python -m services.pool <command> [options]

    verify   check the signed files under pool/ (offline; --online and --fresh for a pull request)
    join     show, then sign, the join statement and write the member file
    leave    show, then sign, the leave statement and write the leave file
    status   this account's standing and the pool's counts
    build    grade every member's public fills and write docs/scoreboard/
    report   one account's own graded report

Each command lives in the module contracts.pool.CLI_COMMANDS names and is
imported only when asked for. That module's main(argv) gets the whole
argument list, command first, and returns the exit code.
"""
from __future__ import annotations

import importlib
import sys
from typing import List, Optional

import contracts.pool as pool


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if args else 2
    command = args[0]
    module = pool.CLI_COMMANDS.get(command)
    if module is None:
        print(f"pathiel pool: unknown command {command!r}. Commands: {', '.join(sorted(pool.CLI_COMMANDS))}",
              file=sys.stderr)
        return 2
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as e:
        if e.name != module:
            raise
        print(f"pathiel pool: {command!r} is not in this checkout ({module} is missing)", file=sys.stderr)
        return 2
    return int(mod.main(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
