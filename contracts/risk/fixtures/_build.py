"""Regenerate: python -m contracts.risk.fixtures._build
ILLUSTRATIVE numbers for UI work only. services/risk computes the real ones."""
from pathlib import Path

from contracts.risk.models import BookFeasibility, DerivedLimits, RiskProfile

HERE = Path(__file__).parent

starter = RiskProfile(preset="starter", risk_per_trade_pct=3.0, daily_loss_limit_pct=8.0, max_leverage=10,
                      max_open_positions=1, allow_min_order_bump=True, max_bumped_risk_pct=6.0)
limits_15 = DerivedLimits(
    equity_usd=15.21, preset_applied="starter", profile=starter, risk_per_trade_usd=0.46,
    daily_loss_limit_usd=1.22, max_open_positions=1, min_equity_to_trade_usd=6.0, can_trade=True,
    books=[
        BookFeasibility(book="news_surge_short", enabled=True, can_trade=True, notional_usd=10.0, leverage=10,
                        margin_usd=1.0, stop_pct=6.0, risk_usd=0.6, risk_pct=3.9, bumped_to_min=True,
                        reason="Sized up to Hyperliquid's $10 minimum: risks $0.60 (3.9%) if the stop hits."),
        BookFeasibility(book="xs_reversal", enabled=True, can_trade=True, notional_usd=10.0, leverage=3,
                        margin_usd=3.33, stop_pct=4.0, risk_usd=0.4, risk_pct=2.6, bumped_to_min=False,
                        reason="Fits: $10 at 3x, risks $0.40 (2.6%)."),
        BookFeasibility(book="drawdown_ladder", enabled=True, can_trade=False, needs_equity_usd=525.0,
                        reason="Needs $525: five resting rungs of $10.50 each at 1x."),
    ],
    headline="You can trade 1 position at a time, risking about $0.46 per trade.",
    notes=["Daily pause after -$1.22."],
)
limits_5 = DerivedLimits(
    equity_usd=5.0, preset_applied="starter", profile=starter, risk_per_trade_usd=0.15,
    daily_loss_limit_usd=0.4, max_open_positions=1, min_equity_to_trade_usd=6.0, can_trade=False,
    books=[BookFeasibility(book="news_surge_short", enabled=True, can_trade=False, needs_equity_usd=10.0,
                           reason="Hyperliquid's $10 minimum order would risk 12% of $5. Add $5 to start.")],
    headline="Add at least $1 more to place your first trade.",
)


def main() -> None:
    for name, obj in (("profile_starter", starter), ("limits_15", limits_15), ("limits_5", limits_5)):
        (HERE / f"{name}.json").write_text(obj.model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    main()
