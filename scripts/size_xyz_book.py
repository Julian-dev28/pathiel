#!/usr/bin/env python3
"""size_xyz_book.py — derive the equity_frac for xs_xyz_equities from math, not vibes.

The book deploys K_LONG + K_SHORT legs at once. Per-leg notional in the executor is
    leg_notional = dex_equity * equity_frac * leverage            (executor.py:646)
so the MARGIN the book consumes is
    margin = n_legs * leg_notional / leverage = n_legs * equity_frac * dex_equity
The leverage cancels: margin utilisation as a FRACTION of dex equity is
    U(f) = n_legs * f
independent of both leverage and the dex-equity magnitude. That is why f can be
solved without knowing the (runtime-only) per-dex equity.

Gross exposure over equity:
    G(f) = U(f) * leverage = n_legs * f * leverage

The book's validated return series (W-X2 cell A, PRIMARY resid7/k5/H5, n=34):
per-rebalance per-leg signed net25 EV mu_g and its stdev sigma_g (from the reported
Sharpe). On equity these scale by G: mu_E = G*mu_g, sigma_E = G*sigma_g. Log-growth
Kelly on the per-rebalance series:
    g(G) = G*mu_g - 0.5 * G^2 * sigma_g^2   ->   G* = mu_g / sigma_g^2
Full Kelly is almost always margin-infeasible here; we take a fractional Kelly and
let the margin cap bind, whichever is smaller.

Momentum-crash tail (the book's documented failure mode, xs_xyz_live.py:112): a
c-percentage-point widening of the long-minus-short spread costs c * G of equity
before the per-leg disaster stop truncates it.

Everything here is deterministic: same inputs -> same f. No network, no state.
"""
from __future__ import annotations

import argparse
import json

# ---- Validated edge (W-X2 cell A PRIMARY resid7/k5/H5, findings/W-X2_xs_xyz_equities.md) ----
MU_G = 0.0065          # per-rebalance per-leg signed net25 EV  (+0.65%)
SHARPE = 0.217         # per-rebalance net25 Sharpe -> sigma_g = MU_G / SHARPE
WORST_REBAL = -0.0679  # worst single-rebalance book EV observed (-6.79%)

# ---- Book structure (config: xs_xyz_equities k_per_leg=5 both sides) ----
K_PER_LEG = 5
N_LEGS = 2 * K_PER_LEG  # 5 long + 5 short = 10
LEVERAGE = 3            # xs_xyz_live.py:143 — 3x keeps the 20% disaster stop inside liq

# ---- Account / risk policy ----
MARGIN_MIN_FREE = 0.10   # config min_available_margin_pct — loop refuses entry below this
# Extra idle-margin head-room above the hard floor. Set to 0.20 (total 30% free) because
# (a) the book's ruin mode is a CORRELATED momentum crash that swings margin on every leg
# at once, and (b) the account is mid-drawdown ($138 -> $113, -18%): buy the buffer now.
MARGIN_PRICE_BUFFER = 0.20
KELLY_FRACTION = 0.35    # prudent fractional Kelly (< 0.5 standard); margin usually binds first
DISASTER_STOP = 0.20     # per-leg wide stop (backup_sl_pct_override=20)


def derive(mu_g=MU_G, sharpe=SHARPE, n_legs=N_LEGS, leverage=LEVERAGE,
           kelly_fraction=KELLY_FRACTION, margin_min_free=MARGIN_MIN_FREE,
           margin_price_buffer=MARGIN_PRICE_BUFFER):
    sigma_g = mu_g / sharpe

    # 1) Kelly on the per-rebalance return series (gross/equity domain)
    g_star = mu_g / (sigma_g ** 2)              # full-Kelly gross/equity
    g_kelly = kelly_fraction * g_star           # prudent fractional Kelly
    f_kelly = g_kelly / (n_legs * leverage)     # -> equity_frac

    # 2) Margin cap: U(f) = n_legs * f must leave (min_free + buffer) idle
    u_max = 1.0 - (margin_min_free + margin_price_buffer)
    f_margin = u_max / n_legs

    # 3) Binding f = the smaller; snap DOWN to a clean 0.005 grid. The +1e-9 guards
    #    the float-floor trap (0.070/0.005 == 13.9999... would floor to 0.065).
    f_raw = min(f_kelly, f_margin)
    f = int(f_raw / 0.005 + 1e-9) * 0.005

    # Realised numbers at the chosen f
    U = n_legs * f
    G = U * leverage
    ev_per_rebal_equity = G * mu_g              # book EV on equity per 5d rebalance
    ev_per_week = ev_per_rebal_equity * 7.0 / 5.0
    crash20_dd = 0.20 * G                        # 20pp spread crash, pre-stop
    worst_dd = abs(WORST_REBAL) * G              # historical worst rebalance scaled

    binding = "margin" if f_margin < f_kelly else "kelly"
    return {
        "inputs": {"mu_g": mu_g, "sigma_g": round(sigma_g, 4), "sharpe": sharpe,
                   "n_legs": n_legs, "leverage": leverage,
                   "kelly_fraction": kelly_fraction},
        "kelly": {"G_full": round(g_star, 2), "G_fractional": round(g_kelly, 2),
                  "f_kelly": round(f_kelly, 4)},
        "margin": {"U_max": round(u_max, 3), "f_margin": round(f_margin, 4)},
        "binding_constraint": binding,
        "recommended_equity_frac": round(f, 3),
        "at_recommended": {
            "margin_utilisation": round(U, 3),
            "free_margin_left": round(1.0 - U, 3),
            "gross_over_equity": round(G, 3),
            "ev_per_rebalance_on_equity_pct": round(ev_per_rebal_equity * 100, 3),
            "ev_per_week_on_equity_pct": round(ev_per_week * 100, 3),
            "crash_20pp_drawdown_pct": round(crash20_dd * 100, 1),
            "worst_hist_rebal_drawdown_pct": round(worst_dd * 100, 1),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--kelly-fraction", type=float, default=KELLY_FRACTION)
    args = ap.parse_args()
    r = derive(kelly_fraction=args.kelly_fraction)
    if args.json:
        print(json.dumps(r, indent=2))
        return
    i, k, m, a = r["inputs"], r["kelly"], r["margin"], r["at_recommended"]
    print("xs_xyz_equities sizing — derived, not eyeballed")
    print(f"  edge:   mu_g={i['mu_g']*100:+.2f}%/rebal  sigma_g={i['sigma_g']*100:.2f}%  Sharpe={i['sharpe']}")
    print(f"  struct: {i['n_legs']} legs  {i['leverage']}x  U(f)={i['n_legs']}*f  G(f)={i['n_legs']}*{i['leverage']}*f")
    print(f"  Kelly:  full G*={k['G_full']}  {i['kelly_fraction']}-Kelly G={k['G_fractional']}  -> f_kelly={k['f_kelly']}")
    print(f"  margin: leave {(m['U_max']):.0%} usable -> f_margin={m['f_margin']:.4f}")
    print(f"  BINDING: {r['binding_constraint']}")
    print(f"  => equity_frac = {r['recommended_equity_frac']}")
    print(f"     margin_util={a['margin_utilisation']:.0%}  free={a['free_margin_left']:.0%}  gross/eq={a['gross_over_equity']}x")
    print(f"     EV ~{a['ev_per_week_on_equity_pct']:+.2f}%/wk on equity  ({a['ev_per_rebalance_on_equity_pct']:+.2f}%/rebal)")
    print(f"     tail: 20pp momentum crash = -{a['crash_20pp_drawdown_pct']:.0f}% (pre per-leg 20% stop)  "
          f"worst-hist = -{a['worst_hist_rebal_drawdown_pct']:.0f}%")


if __name__ == "__main__":
    main()
