"""30-day backtest of the desk-tick loop's mechanics (entry/stop/target/
leverage/sizing), built from `docs/overhaul/BACKTEST_PLAN_2026-10-25.md`.

`desk_tick_sim.py` is the pure simulator (no I/O). `data.py` is the fetch
and disk cache for candles and funding history. The runnable eval lives at
`services/strategies/evals/desk_tick_backtest.py`.
"""
