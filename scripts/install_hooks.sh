#!/usr/bin/env bash
# Point git at scripts/hooks/ so the pre-commit gate is version-controlled
# rather than living untracked in .git/hooks where it cannot be reviewed and is
# lost on a fresh clone.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" config core.hooksPath scripts/hooks
echo "core.hooksPath -> scripts/hooks"
echo "installed: $(ls "$ROOT/scripts/hooks")"
