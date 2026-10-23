"""Run the rolling day-ahead backtest for the configured strategies.

    uv run python scripts/run_backtest.py [--offline]
        [--start YYYY-MM-DD --end YYYY-MM-DD] [--strategies a,b,...] [--out runs/NAME]

Period and strategies default to ``backtest`` in configs/default.yaml.
Outputs (git-ignored), default ``runs/latest/backtest/``:

* ``daily.csv``: one row per strategy and delivery day (status, energies,
  expected and realised settlement, solver and forecast metadata).
* ``intervals.csv``: committed schedule and prices per interval.
* ``summary.csv``: totals per strategy.
* ``market_data.csv``: validated market inputs (UTC, clean units).
* ``metadata.json``: git commit, config fingerprint and dump, package
  versions, policy, data provenance.

Totals here are diagnostics. Annualised and normalised financial metrics are
defined in Stage 7 (docs/RESULTS.md).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd

from _common import RUNS, load_cfg, load_dataset
from gridflex.backtest import run_backtest
from gridflex.config import DayPeriod
from gridflex.reporting.export import write_backtest_run


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--start", help="first delivery day (default: config)")
    ap.add_argument("--end", help="end delivery day, exclusive (default: config)")
    ap.add_argument("--strategies", help="comma-separated subset (default: all)")
    ap.add_argument("--out", default=str(RUNS / "backtest"))
    args = ap.parse_args()

    cfg = load_cfg()
    if cfg.backtest is None:
        sys.exit("configs/default.yaml has no backtest section")
    period = cfg.backtest.period
    if args.start or args.end:
        period = DayPeriod(
            start_day=dt.date.fromisoformat(args.start)
            if args.start
            else period.start_day,
            end_day=dt.date.fromisoformat(args.end) if args.end else period.end_day,
        )
    history_start = (
        cfg.forecasting.history_start_day
        if cfg.forecasting
        else period.start_day - dt.timedelta(days=8)
    )
    series = ("price", *(cfg.forecasting.exogenous_series if cfg.forecasting else ()))
    t0 = time.perf_counter()
    ds = load_dataset(
        cfg,
        history_start.isoformat(),
        period.end_day.isoformat(),
        series=series,
        offline=args.offline or None,
    )
    names = args.strategies.split(",") if args.strategies else None
    results, meta = run_backtest(cfg, ds, period=period, strategy_names=names)
    meta["runtime_s"] = round(time.perf_counter() - t0, 1)

    out = Path(args.out)
    summary = write_backtest_run(out, results, meta, ds)

    pd.set_option("display.width", 200)
    print(
        f"Backtest {period.start_day} -> {period.end_day} (exclusive), "
        f"{len(results)} strategies, {meta['runtime_s']} s"
    )
    cols = [
        "strategy",
        "days",
        "failed_days",
        "expected_net_eur",
        "gross_revenue_eur",
        "degradation_cost_eur",
        "net_operating_revenue_eur",
        "energy_sold_mwh",
        "equivalent_full_cycles",
        "share_of_perfect_foresight_net",
    ]
    print(summary[[c for c in cols if c in summary]].round(3).to_string(index=False))
    print(f"git {meta['git']}  config {meta['config_fingerprint'][:12]}")
    print(f"outputs -> {out}")


if __name__ == "__main__":
    main()
