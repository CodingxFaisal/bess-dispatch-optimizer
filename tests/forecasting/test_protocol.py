"""Evaluation protocol: config validation and selection/hold-out separation."""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml

from gridflex.config import ConfigError, ForecastingConfig, GridFlexConfig, load_config
from gridflex.forecasting import build_feature_table, evaluate_holdout, select_models
from tests_helpers import DEFAULT_CONFIG, synthetic_market_frame


def _load(tmp_path: Path, **fc: Any) -> GridFlexConfig:
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["forecasting"].update(fc)
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_default_protocol(gf_cfg: GridFlexConfig) -> None:
    fc = gf_cfg.forecasting
    assert fc is not None
    assert fc.selection_period.end_day <= fc.holdout_period.start_day
    assert fc.holdout_period.end_day == dt.date(2025, 10, 1)  # end of hourly regime
    assert fc.exogenous_series == ["load_forecast"]


@pytest.mark.parametrize(
    "fc",
    [
        {
            "holdout_period": {"start_day": "2023-06-01", "end_day": "2024-06-01"}
        },  # overlap
        {"history_start_day": "2023-02-01"},  # history after selection start
        {"exogenous_series": ["gas_price"]},  # unknown series
        {"price_lags_days": [2, 3]},  # no lag for the naive baselines
        {"selection_period": {"start_day": "2024-01-01", "end_day": "2023-01-01"}},
        {"lasso": {"calibration_window_days_grid": [7]}},  # window too short
    ],
)
def test_invalid_protocols_rejected(tmp_path: Path, fc: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, **fc)


def test_selection_and_holdout_are_separate(gf_cfg: GridFlexConfig) -> None:
    fc = ForecastingConfig.model_validate(
        {
            "history_start_day": "2024-01-01",
            "selection_period": {"start_day": "2024-03-01", "end_day": "2024-03-15"},
            "holdout_period": {"start_day": "2024-03-15", "end_day": "2024-03-29"},
            "lasso": {
                "calibration_window_days_grid": [28, 56],
                "recalibrate_every_days": 7,
            },
            "gbr": {
                "calibration_window_days_grid": [28],
                "max_depth_grid": [3],
                "max_iter": 40,
            },
        }
    )
    frame = synthetic_market_frame("2024-01-01", 100, seed=5)
    table = build_feature_table(frame, gf_cfg)
    seen: list[str] = []
    sel = select_models(fc, table, gf_cfg.market, frame["price"], progress=seen.append)
    assert set(sel.table["label"]) == {
        "persistence",
        "seasonal_naive",
        "lasso_w28",
        "lasso_w56",
        "gbr_w28_d3",
    }
    assert (sel.table["n_days"] == 14).all()
    lasso = sel.table[sel.table["family"] == "lasso"]
    assert sel.chosen["lasso"] == lasso.loc[lasso["mae"].idxmin(), "label"]
    metrics, frames = evaluate_holdout(
        fc, sel.chosen, table, gf_cfg.market, frame["price"]
    )
    for f in frames.values():
        days = set(f["delivery_day"])
        assert min(days) == dt.date(2024, 3, 15) and max(days) == dt.date(2024, 3, 28)
        assert (f["origin_utc"] < f["delivery_interval_utc"]).all()
    naive = metrics[metrics["family"] == "seasonal_naive"].iloc[0]
    assert naive["rmae"] == pytest.approx(1.0)
    assert seen[0].startswith("selection:") and len(seen) == 5
