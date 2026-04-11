"""Tier 3 (extension) -- storage-duration sweep.

Holds power fixed at the configured MW and sweeps energy so the battery is a
1h / 2h / 4h / 8h system, reporting revenue per MW-year and per MWh-year. This
finds the economic sweet spot and exposes the diminishing returns of adding
duration: the first hours of storage capture the fat daily spread; later hours
chase ever-smaller intraday wiggles.

Uses the perfect-foresight ceiling so the comparison isolates the effect of
duration itself (not forecast skill).

    python scripts/run_duration_sweep.py --start 2024-01-01 --end 2025-01-01
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import pandas as pd

from _common import RESULTS, CONFIG, load_config, load_or_build_dataset

from bess.analysis.metrics import summarize_backtest
from bess.analysis.plots import plot_duration_sweep
from bess.backtest import run_backtest
from bess.forecast import get_forecaster


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-01-01")
    ap.add_argument("--durations", default="1,2,4,8",
                    help="comma-separated durations in hours")
    args = ap.parse_args()

    cfg = load_config(CONFIG)
    df = load_or_build_dataset(args.start, args.end)
    durations = [float(x) for x in args.durations.split(",")]

    rows = []
    for dur in durations:
        cfg_d = replace(cfg, energy_mwh=cfg.power_mw * dur)
        res = run_backtest(df, cfg_d, get_forecaster("perfect"), args.start, args.end)
        s = summarize_backtest(res, cfg_d)
        rows.append({
            "duration_h": dur,
            "energy_mwh": cfg_d.energy_mwh,
            "revenue_per_mw_year_eur": s["revenue_per_mw_year_eur"],
            "revenue_per_mwh_year_eur": s["revenue_per_mwh_year_eur"],
            "cycles_per_year": s["equivalent_full_cycles_per_year"],
        })
        print(f"  {dur:>4.1f}h  EUR/MW-yr {s['revenue_per_mw_year_eur']:>10,.0f}"
              f"   EUR/MWh-yr {s['revenue_per_mwh_year_eur']:>9,.0f}"
              f"   cycles/yr {s['equivalent_full_cycles_per_year']:>6,.0f}")

    sweep = pd.DataFrame(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    sweep.to_csv(RESULTS / "duration_sweep.csv", index=False)
    plot_duration_sweep(sweep, RESULTS / "duration_sweep.png")
    print(f"\nsaved: duration_sweep.csv, duration_sweep.png  (in results/)")


if __name__ == "__main__":
    main()
