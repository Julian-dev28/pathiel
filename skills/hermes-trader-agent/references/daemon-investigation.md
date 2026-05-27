# Investigation: `--daemon` Flag Behavior (2026-05-22)

**TL;DR for current operation:** use `scripts/restart.sh` — it handles
the nohup backgrounding correctly. Section below kept for historical
context on why the `--daemon` flag is a no-op.

## Problem
The standard restart command `python3 scripts/trading_loop.py --env prod --daemon` appeared to start but produced no output and the process died quickly.

## Root Cause
The `--daemon` flag and `--env` flag are **parsed but informational only** -- they have no operational effect on the process. From `scripts/trading_loop.py`:

```python
_parser.add_argument("--daemon", action="store_true")
# Later in execution:
_args, _unknown = _parser.parse_known_args()
# ... but --daemon is never actually used for any process management
```

The script loads `.env.local` manually (via direct file reading, not the argparse `--env` value) and respects `HERMES_SCAN_INTERVAL` env var. The loop itself is a simple `while True` with try/except + sleep.

## Correct Pattern
The trading loop has its own `while True` loop with `time.sleep(scan_interval)` internally, but it does NOT fork to background itself. It must be backgrounded externally:

```bash
nohup python3 scripts/trading_loop.py > logs/trading_loop.log 2>&1 &
```

## Key Takeaway
- `--env` and `--daemon` flags are accepted but **informational only**
- The script does NOT actually daemonize when `--daemon` is passed
- When run without backgrounding, the process dies when the terminal session ends
- Always use `nohup ... &` or a proper process manager for persistent operation
- Memory context notes the restart command without `nohup` -- this should be updated