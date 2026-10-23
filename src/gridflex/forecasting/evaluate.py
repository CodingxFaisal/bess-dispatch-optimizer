"""Chronological forecast evaluation, model selection and accuracy metrics.

Protocol (``forecasting`` config):

1. **Selection.** Each candidate configuration is run day by day over
   ``selection_period``. For every family (Lasso, GBR), the configuration with
   the lowest MAE is chosen. Naive baselines have no hyperparameters.
2. **Hold-out.** The chosen configurations and the baselines are run once over
   ``holdout_period`` and scored. The hold-out takes no part in any choice.

Trained models recalibrate on trailing windows as the days roll forward, using
only data public at each origin (enforced by the feature guards).

Metrics (EUR/MWh, over all delivery intervals of the period):

* ``MAE = mean |p_hat - p|`` and ``RMSE = sqrt(mean (p_hat - p)^2)``;
* ``bias = mean (p_hat - p)``;
* ``rMAE = MAE / MAE_seasonal_naive`` on the same intervals (Lago et al.
  2021). Values below 1 beat the weekly seasonal-naive benchmark.

Diebold-Mariano test (multivariate, after Lago et al. 2021): the loss
differential per day is ``d_t = mean_i |e_A,t,i| - mean_i |e_B,t,i|``, and
``DM = mean(d) / (std(d) / sqrt(N))``. The one-sided p-value is
``1 - Phi(DM)`` for H1: B is more accurate than A. No autocorrelation
correction is applied; day-ahead forecasts for consecutive days are treated
as one-step-ahead.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm

from gridflex.config.models import DayPeriod, ForecastingConfig, MarketConfig
from gridflex.forecasting.base import PriceForecast
from gridflex.forecasting.features import FeatureTable
from gridflex.forecasting.models import (
    Forecaster,
    GbrForecaster,
    LassoForecaster,
    PersistenceForecaster,
    SeasonalNaiveForecaster,
)


def days_in(period: DayPeriod) -> list[dt.date]:
    n = (period.end_day - period.start_day).days
    return [period.start_day + dt.timedelta(days=i) for i in range(n)]


def rolling_forecasts(
    model: Forecaster,
    table: FeatureTable,
    market: MarketConfig,
    days: Iterable[dt.date],
) -> list[PriceForecast]:
    return [model.forecast_day(table, market, d) for d in days]


def forecasts_frame(forecasts: list[PriceForecast], actual: pd.Series) -> pd.DataFrame:
    """Stack forecasts with the realised price per delivery interval."""
    frame = pd.concat([f.to_frame() for f in forecasts], ignore_index=True)
    frame["price_actual_eur_per_mwh"] = actual.reindex(
        pd.DatetimeIndex(frame["delivery_interval_utc"])
    ).to_numpy()
    if frame["price_actual_eur_per_mwh"].isna().any():
        raise ValueError("realised prices missing for some forecast intervals")
    frame["error_eur_per_mwh"] = (
        frame["price_forecast_eur_per_mwh"] - frame["price_actual_eur_per_mwh"]
    )
    return frame


@dataclass(frozen=True)
class Accuracy:
    model_id: str
    n_intervals: int
    n_days: int
    mae: float
    rmse: float
    bias: float
    rmae: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def accuracy(frame: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> Accuracy:
    err = frame["error_eur_per_mwh"].to_numpy(dtype=np.float64)
    mae = float(np.mean(np.abs(err)))
    rmae = None
    if benchmark is not None:
        b = benchmark.set_index("delivery_interval_utc")["error_eur_per_mwh"]
        b = b.reindex(pd.DatetimeIndex(frame["delivery_interval_utc"]))
        if b.isna().any():
            raise ValueError("benchmark does not cover the same intervals")
        rmae = mae / float(np.mean(np.abs(b.to_numpy())))
    return Accuracy(
        model_id=str(frame["model_id"].iloc[0]),
        n_intervals=len(frame),
        n_days=int(frame["delivery_day"].nunique()),
        mae=mae,
        rmse=float(math.sqrt(np.mean(err**2))),
        bias=float(np.mean(err)),
        rmae=rmae,
    )


def dm_test(frame_a: pd.DataFrame, frame_b: pd.DataFrame) -> dict[str, float]:
    """One-sided DM test; small p-values mean B is more accurate than A."""
    da = frame_a.groupby("delivery_day")["error_eur_per_mwh"].apply(
        lambda e: e.abs().mean()
    )
    db = frame_b.groupby("delivery_day")["error_eur_per_mwh"].apply(
        lambda e: e.abs().mean()
    )
    d = (da - db.reindex(da.index)).dropna().to_numpy(dtype=np.float64)
    n = len(d)
    sd = float(np.std(d, ddof=1)) if n > 1 else 0.0
    if n < 2 or sd == 0:
        return {"dm_stat": math.nan, "p_value_b_better": math.nan, "n_days": float(n)}
    stat = float(np.mean(d) / (sd / math.sqrt(n)))
    return {
        "dm_stat": stat,
        "p_value_b_better": float(1 - norm.cdf(stat)),
        "n_days": float(n),
    }


# --------------------------------------------------------------------------- #
# Candidates and selection                                                    #
# --------------------------------------------------------------------------- #
ModelFactory = Callable[[], Forecaster]


def candidate_factories(fc: ForecastingConfig) -> dict[str, dict[str, ModelFactory]]:
    """Family -> {label: factory}. Fresh instances per run (no shared state)."""
    lasso: dict[str, ModelFactory] = {
        f"lasso_w{w}": partial(LassoForecaster, w, fc.lasso.recalibrate_every_days)
        for w in fc.lasso.calibration_window_days_grid
    }
    gbr: dict[str, ModelFactory] = {
        f"gbr_w{w}_d{d}": partial(
            GbrForecaster,
            w,
            fc.gbr.recalibrate_every_days,
            max_depth=d,
            learning_rate=fc.gbr.learning_rate,
            max_iter=fc.gbr.max_iter,
            l2_regularization=fc.gbr.l2_regularization,
            random_state=fc.random_state,
        )
        for w in fc.gbr.calibration_window_days_grid
        for d in fc.gbr.max_depth_grid
    }
    return {
        "persistence": {"persistence": PersistenceForecaster},
        "seasonal_naive": {"seasonal_naive": SeasonalNaiveForecaster},
        "lasso": lasso,
        "gbr": gbr,
    }


@dataclass(frozen=True)
class SelectionResult:
    table: pd.DataFrame  # one row per candidate with selection-period metrics
    chosen: dict[str, str]  # family -> label


def select_models(
    fc: ForecastingConfig,
    table: FeatureTable,
    market: MarketConfig,
    actual: pd.Series,
    progress: Callable[[str], None] | None = None,
) -> SelectionResult:
    days = days_in(fc.selection_period)
    families = candidate_factories(fc)
    bench = forecasts_frame(
        rolling_forecasts(SeasonalNaiveForecaster(), table, market, days), actual
    )
    rows = []
    for family, cands in families.items():
        for label, make in cands.items():
            if progress:
                progress(f"selection: {label}")
            frame = forecasts_frame(
                rolling_forecasts(make(), table, market, days), actual
            )
            acc = accuracy(frame, bench)
            rows.append({"family": family, "label": label, **acc.as_dict()})
    result = pd.DataFrame(rows)
    chosen = {
        fam: str(result.loc[grp["mae"].idxmin(), "label"])
        for fam, grp in result.groupby("family")
    }
    return SelectionResult(result, chosen)


def evaluate_holdout(
    fc: ForecastingConfig,
    chosen: dict[str, str],
    table: FeatureTable,
    market: MarketConfig,
    actual: pd.Series,
    progress: Callable[[str], None] | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Run each chosen configuration once on the hold-out; return metrics and frames."""
    days = days_in(fc.holdout_period)
    families = candidate_factories(fc)
    frames: dict[str, pd.DataFrame] = {}
    for family, label in chosen.items():
        if progress:
            progress(f"hold-out: {label}")
        frames[label] = forecasts_frame(
            rolling_forecasts(families[family][label](), table, market, days), actual
        )
    bench = frames[chosen["seasonal_naive"]]
    metrics = pd.DataFrame(
        [
            {"family": fam, "label": lab, **accuracy(frames[lab], bench).as_dict()}
            for fam, lab in chosen.items()
        ]
    )
    return metrics, frames
