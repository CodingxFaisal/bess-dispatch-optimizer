"""Minimal end-to-end example on the committed SMARD fixture (offline).

Produces a complete run directory (backtest, finance, forecast evaluation,
sensitivity) from 14 days of real SMARD data (2024-03-25 to 2024-04-07,
including the 23 h day on 31 March) in well under a minute, with no network
access. It exercises every pipeline stage; its numbers are a demonstration,
not research results.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from gridflex.backtest.engine import run_backtest
from gridflex.config.models import DayPeriod, GridFlexConfig
from gridflex.data.dataset import build_market_dataset
from gridflex.data.smard import SmardClient
from gridflex.finance.sensitivity import run_sensitivity
from gridflex.forecasting.evaluate import (
    accuracy,
    dm_test,
    forecasts_frame,
    rolling_forecasts,
)
from gridflex.forecasting.features import build_feature_table
from gridflex.forecasting.models import PersistenceForecaster, SeasonalNaiveForecaster
from gridflex.reporting.export import write_backtest_run, write_finance

EXAMPLE_STRATEGIES = [
    "perfect_foresight",
    "forecast_persistence",
    "heuristic_persistence",
]
DATA_WINDOW = (dt.date(2024, 3, 25), dt.date(2024, 4, 8))
BACKTEST = DayPeriod(start_day=dt.date(2024, 3, 30), end_day=dt.date(2024, 4, 8))
FORECAST_DAYS = [dt.date(2024, 4, 1) + dt.timedelta(days=i) for i in range(7)]
SENSITIVITY = DayPeriod(start_day=dt.date(2024, 4, 1), end_day=dt.date(2024, 4, 4))


def build_example_run(cfg: GridFlexConfig, fixture_cache: Path, out: Path) -> Path:
    """Write the example run to ``out`` (e.g. ``runs/example``) and return it."""
    client = SmardClient.from_config(cfg.data, cache_dir=fixture_cache, offline=True)
    ds = build_market_dataset(
        cfg, client, *DATA_WINDOW, series=("price", "load_forecast")
    )

    results, meta = run_backtest(
        cfg, ds, period=BACKTEST, strategy_names=EXAMPLE_STRATEGIES
    )
    meta["example"] = "offline fixture example (scripts/run_example.py); not results"
    write_backtest_run(out / "backtest", results, meta, ds)
    write_finance(out / "backtest", cfg)

    table = build_feature_table(ds.frame, cfg)
    frames = {
        m.model_id: forecasts_frame(
            rolling_forecasts(m, table, cfg.market, FORECAST_DAYS), ds.frame["price"]
        )
        for m in (PersistenceForecaster(), SeasonalNaiveForecaster())
    }
    fc = out / "forecast_evaluation"
    fc.mkdir(parents=True, exist_ok=True)
    bench = frames["seasonal_naive"]
    metrics = pd.DataFrame(
        [
            {"family": k, "label": k, **accuracy(v, bench).as_dict()}
            for k, v in frames.items()
        ]
    )
    metrics.to_csv(fc / "holdout_metrics.csv", index=False)
    metrics.to_csv(fc / "selection.csv", index=False)
    metrics.assign(year=2024).to_csv(fc / "holdout_by_year.csv", index=False)
    pd.DataFrame(
        [
            {
                "model_a": "seasonal_naive",
                "model_b": "persistence",
                **dm_test(bench, frames["persistence"]),
            }
        ]
    ).to_csv(fc / "dm_tests.csv", index=False)
    pd.concat(frames.values()).to_csv(fc / "holdout_forecasts.csv", index=False)

    sens = run_sensitivity(
        cfg, ds, period=SENSITIVITY, strategy_names=["perfect_foresight"]
    )
    (out / "sensitivity").mkdir(parents=True, exist_ok=True)
    sens.to_csv(out / "sensitivity" / "sensitivity.csv", index=False)
    return out
