"""Tier 1 -- perfect-foresight baseline (the revenue ceiling).

Optimizes each delivery day against the *realised* day-ahead prices. This is
the theoretical maximum: it assumes the operator knew tomorrow's prices exactly.
It is NOT a business case -- it is the yardstick the realistic backtest (Tier 2)
is measured against.

    uv run python scripts/run_perfect_foresight.py --start 2024-01-01 --end 2025-01-01
"""

from __future__ import annotations

import argparse

from _common import RUNS as RESULTS
from _common import legacy_frame, load_cfg, load_dataset, write_provenance
from gridflex.analysis.metrics import revenue_by_year, summarize_backtest
from gridflex.analysis.plots import plot_dispatch_week, plot_revenue_by_year
from gridflex.backtest import run_backtest
from gridflex.forecast import get_forecaster
from gridflex.optimize.params import DispatchParams


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-01-01")
    ap.add_argument(
        "--week-start",
        default=None,
        help="ISO date for the example dispatch chart (default: a "
        "week ~40%% into the period)",
    )
    args = ap.parse_args()

    gf_cfg = load_cfg()
    cfg = DispatchParams.from_config(gf_cfg)
    ds = load_dataset(gf_cfg, args.start, args.end)
    write_provenance(ds, RESULTS, "perfect_foresight")
    df = legacy_frame(ds)

    result = run_backtest(df, cfg, get_forecaster("perfect"), args.start, args.end)
    summary = summarize_backtest(result, cfg)

    print("\n=== Tier 1: perfect-foresight ceiling ===")
    print(
        f"  period                 {args.start} -> {args.end} ({summary['days']} days)"
    )
    print(f"  net revenue            EUR {summary['net_revenue_eur']:,.0f}")
    print(f"  annualised             EUR {summary['annual_net_eur']:,.0f}/yr")
    print(f"  per MW-year            EUR {summary['revenue_per_mw_year_eur']:,.0f}")
    print(f"  per MWh-year           EUR {summary['revenue_per_mwh_year_eur']:,.0f}")
    print(f"  equiv. full cycles/yr  {summary['equivalent_full_cycles_per_year']:,.0f}")
    if summary["payback_years"] == summary["payback_years"]:  # not NaN
        print(
            f"  simple payback         {summary['payback_years']:.1f} years "
            "(undiscounted, revenue only; NOT an investment return)"
        )
    else:
        print(
            "  simple payback         not computed (economics.capex_eur_per_kwh unset)"
        )

    # Revenue by price regime (year) -- the gas-crisis effect on arbitrage value.
    by_year = revenue_by_year(result)
    print("\n  net revenue by year (price regime):")
    for yr, val in by_year.items():
        print(f"    {yr}   EUR {val:,.0f}   ({val / cfg.power_mw:,.0f}/MW)")
    if len(by_year) > 1:
        plot_revenue_by_year(
            {"perfect": by_year},
            RESULTS / "perfect_revenue_by_year.png",
            title="Perfect-foresight revenue by year (calendar-year totals)",
        )
        print("  saved chart -> runs/latest/perfect_revenue_by_year.png")

    # Example week chart.
    sched = result.schedule
    wk_start = args.week_start or sched.index[int(len(sched) * 0.4)].normalize()
    week = sched.loc[str(wk_start) :]
    week = week.iloc[: 24 * 7]
    out = plot_dispatch_week(
        week,
        RESULTS / "tier1_dispatch_week.png",
        title="Perfect-foresight dispatch vs. day-ahead price (one week)",
    )
    print(f"\n  saved chart -> {out.relative_to(RESULTS.parent)}")


if __name__ == "__main__":
    main()
