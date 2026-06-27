"""B16 funding_momentum 💰 — DATA-BLOCKED. Funding-rate TREND predicts price (persistent funding =
persistent directional pressure). dataset.json carries only a single funding SNAPSHOT per coin
(universe[c]['funding']); the TREND needs a funding TIME SERIES from data_logger (~1-2wk).
This file stubs the tradeable rule and self-tests it on a SYNTHETIC funding+price series so the
logic is ready to run the day the feed lands."""
import statistics

def funding_signal(funding_hist, k=8, thresh=0.0):
    """Decision rule (lookahead-safe): given funding rates up to and INCLUDING the decision bar,
    return 'long'/'short'/None. Long when the trailing-k funding TREND (slope sign) is persistently
    positive AND level positive (longs paying = crowded long pressure -> the project's edge is to
    LEAN with persistent pressure as momentum, sweep both signs in real test)."""
    if len(funding_hist) < k:
        return None
    seg = funding_hist[-k:]
    # simple slope: mean of second half minus first half
    half = k // 2
    slope = statistics.mean(seg[half:]) - statistics.mean(seg[:half])
    level = statistics.mean(seg)
    if slope > thresh and level > 0:
        return "long"      # persistent & rising positive funding -> continuation (test vs fade in real run)
    if slope < -thresh and level < 0:
        return "short"
    return None

def _selftest():
    # synthetic: rising positive funding -> long; falling negative -> short; flat -> None
    rising_pos = [0.001*i for i in range(10)]              # 0..0.009, slope+, level+
    falling_neg = [-0.001*i for i in range(10)]            # 0..-0.009, slope-, level-
    flat = [0.0001]*10
    assert funding_signal(rising_pos) == "long", funding_signal(rising_pos)
    assert funding_signal(falling_neg) == "short", funding_signal(falling_neg)
    assert funding_signal(flat) is None, funding_signal(flat)
    assert funding_signal([0.001]*4, k=8) is None  # too short
    print("funding_momentum stub self-test PASSED (3 cases + short-history guard)")

if __name__ == "__main__":
    _selftest()
    # confirm only a snapshot exists in the cached dataset (proves the block)
    import alpha_lib as A
    d = A.load_dataset()
    f = d.get("universe", {}).get("BTC", {}).get("funding")
    print(f"cached BTC funding = {f!r} (single snapshot scalar -> NO time series -> cannot test trend)")
