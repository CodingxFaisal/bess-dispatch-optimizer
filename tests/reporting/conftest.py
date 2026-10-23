"""A small but complete run directory built offline from the SMARD fixture."""

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

from gridflex.backtest import run_backtest
from gridflex.config import DayPeriod, GridFlexConfig
from gridflex.data import SmardClient, build_market_dataset
from gridflex.finance import run_sensitivity
from gridflex.forecasting import (
    PersistenceForecaster,
    SeasonalNaiveForecaster,
    accuracy,
    build_feature_table,
    dm_test,
    forecasts_frame,
    rolling_forecasts,
)
from gridflex.reporting.export import write_backtest_run, write_finance
from tests_helpers import FIXTURE_CACHE, REPO_ROOT

APP_DIR = REPO_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

STRATS = ["perfect_foresight", "forecast_persistence", "heuristic_persistence"]


@pytest.fixture(scope="session")
def mini_run(tmp_path_factory: pytest.TempPathFactory, gf_cfg: GridFlexConfig) -> Path:
    root = tmp_path_factory.mktemp("runs") / "latest"
    client = SmardClient.from_config(gf_cfg.data, cache_dir=FIXTURE_CACHE, offline=True)
    ds = build_market_dataset(
        gf_cfg,
        client,
        dt.date(2024, 3, 25),
        dt.date(2024, 4, 8),
        series=("price", "load_forecast"),
    )
    period = DayPeriod(start_day=dt.date(2024, 3, 30), end_day=dt.date(2024, 4, 8))
    results, meta = run_backtest(gf_cfg, ds, period=period, strategy_names=STRATS)
    write_backtest_run(root / "backtest", results, meta, ds)
    write_finance(root / "backtest", gf_cfg)

    table = build_feature_table(ds.frame, gf_cfg)
    days = [dt.date(2024, 4, 1) + dt.timedelta(days=i) for i in range(7)]
    frames = {
        m.model_id: forecasts_frame(
            rolling_forecasts(m, table, gf_cfg.market, days), ds.frame["price"]
        )
        for m in (PersistenceForecaster(), SeasonalNaiveForecaster())
    }
    fc = root / "forecast_evaluation"
    fc.mkdir(parents=True)
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
        gf_cfg,
        ds,
        period=DayPeriod(start_day=dt.date(2024, 4, 1), end_day=dt.date(2024, 4, 4)),
        strategy_names=["perfect_foresight"],
    )
    (root / "sensitivity").mkdir()
    sens.to_csv(root / "sensitivity" / "sensitivity.csv", index=False)
    return root
