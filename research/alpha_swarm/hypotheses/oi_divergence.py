"""C1 oi_divergence — DATA-BLOCKED (needs OI time-series from data_logger).

dataset.json carries only a single openInterest snapshot per coin (universe[coin]
['openInterest']), NOT a time series. The hypothesis (price-up+OI-up = new-money
continuation; price-up+OI-down = short-covering fade) requires per-bar OI deltas.

So this file only STUBS + UNIT-TESTS the classification logic on synthetic data so
it is ready the moment data_logger has ~1-2wk of OI history wired.
"""
from __future__ import annotations


def classify_oi_price(dp: float, doi: float) -> str:
    """Standard OI/price taxonomy.
      price up + OI up   -> 'long_buildup'      (new money long -> continuation up)
      price up + OI down -> 'short_covering'    (fade the up move)
      price down + OI up -> 'short_buildup'     (new money short -> continuation down)
      price down + OI down-> 'long_unwinding'   (fade the down move / capitulation)
    """
    if dp >= 0 and doi >= 0:
        return "long_buildup"
    if dp >= 0 and doi < 0:
        return "short_covering"
    if dp < 0 and doi >= 0:
        return "short_buildup"
    return "long_unwinding"


def signal_from_classification(label: str) -> str:
    """Tradeable side implied by the taxonomy (continuation vs fade)."""
    return {
        "long_buildup": "long",      # continuation
        "short_buildup": "short",    # continuation
        "short_covering": "short",   # fade the squeeze-up
        "long_unwinding": "long",    # fade the capitulation
    }[label]


def _selftest():
    cases = [
        (0.05, 0.10, "long_buildup", "long"),
        (0.05, -0.10, "short_covering", "short"),
        (-0.05, 0.10, "short_buildup", "short"),
        (-0.05, -0.10, "long_unwinding", "long"),
        (0.0, 0.0, "long_buildup", "long"),
    ]
    for dp, doi, exp_lbl, exp_sig in cases:
        lbl = classify_oi_price(dp, doi)
        sig = signal_from_classification(lbl)
        assert lbl == exp_lbl, (dp, doi, lbl, exp_lbl)
        assert sig == exp_sig, (dp, doi, sig, exp_sig)
    print("oi_divergence stub self-test PASSED (5 cases). Logic ready for data_logger OI series.")


if __name__ == "__main__":
    _selftest()
