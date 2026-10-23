"""Day-ahead price forecasters.

All forecasters work on a :class:`FeatureTable` and produce one
:class:`PriceForecast` per delivery day D, issued at the day-ahead decision
instant (D-1 12:00 Europe/Berlin). Before predicting, every forecaster checks
that its inputs were public at the origin; trained forecasters also check
their training rows (``check_training_rows``).

* :class:`PersistenceForecaster`: price at the same wall-clock time on D-1.
* :class:`SeasonalNaiveForecaster`: price at the same wall-clock time on D-7.
* :class:`LassoForecaster`: LEAR-style linear model in the spirit of Lago et
  al. (2021, *Applied Energy* 293, 116983; epftoolbox, Apache-2.0). Prices and
  target are asinh-transformed after median/MAD scaling, inputs are
  standardised, and the L1 penalty is chosen by AIC (``LassoLarsIC``). Every
  transformation is fitted on the training window only. Independent
  implementation; no epftoolbox code is used.
* :class:`GbrForecaster`: scikit-learn ``HistGradientBoostingRegressor``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LassoLarsIC
from sklearn.preprocessing import StandardScaler

import gridflex
from gridflex.config.models import MarketConfig
from gridflex.data.availability import decision_time
from gridflex.forecasting.base import ForecastError, PriceForecast
from gridflex.forecasting.features import (
    FeatureTable,
    check_prediction_rows,
    check_training_rows,
)

FloatArray = npt.NDArray[np.float64]


def _config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]


class Forecaster(ABC):
    """Common interface: one forecast per delivery day."""

    model_id: str = "base"

    @property
    @abstractmethod
    def features(self) -> tuple[str, ...]: ...

    @abstractmethod
    def config(self) -> dict[str, Any]: ...

    @property
    def model_version(self) -> str:
        return f"gridflex-{gridflex.__version__}+{_config_hash(self.config())}"

    def forecast_day(
        self, table: FeatureTable, market: MarketConfig, delivery_day: dt.date
    ) -> PriceForecast:
        origin = decision_time(market, delivery_day)
        rows = table.rows_for_day(delivery_day)
        if len(rows) == 0:
            raise ForecastError(f"no intervals for delivery day {delivery_day}")
        sub = table.subset(self.features)
        check_prediction_rows(sub, rows, origin)
        X = sub.X.iloc[rows]
        if X.isna().to_numpy().any():
            missing = list(X.columns[X.isna().any().to_numpy()])
            raise ForecastError(
                f"{self.model_id}: inputs {missing} missing for {delivery_day}; "
                "no silent filling"
            )
        values, meta = self._predict(sub, rows, X, origin, delivery_day)
        feature_cut = sub.feature_published_at.iloc[rows].max().max()
        return PriceForecast(
            delivery_day=delivery_day,
            origin_utc=origin,
            values=pd.Series(values, index=X.index, name=self.model_id),
            model_id=self.model_id,
            model_version=self.model_version,
            feature_cutoff_utc=origin if pd.isna(feature_cut) else feature_cut,
            features=self.features,
            config=self.config(),
            **meta,
        )

    @abstractmethod
    def _predict(
        self,
        table: FeatureTable,
        rows: np.ndarray,
        X: pd.DataFrame,
        origin: pd.Timestamp,
        delivery_day: dt.date,
    ) -> tuple[FloatArray, dict[str, Any]]: ...


# --------------------------------------------------------------------------- #
# Naive baselines                                                             #
# --------------------------------------------------------------------------- #
class _LagForecaster(Forecaster):
    lag_days: int = 1

    @property
    def features(self) -> tuple[str, ...]:
        return (f"price_lag_{self.lag_days}d",)

    def config(self) -> dict[str, Any]:
        return {"model": self.model_id, "lag_days": self.lag_days}

    def _predict(
        self,
        table: FeatureTable,
        rows: np.ndarray,
        X: pd.DataFrame,
        origin: pd.Timestamp,
        delivery_day: dt.date,
    ) -> tuple[FloatArray, dict[str, Any]]:
        return X.iloc[:, 0].to_numpy(dtype=np.float64), {}


class PersistenceForecaster(_LagForecaster):
    model_id = "persistence"
    lag_days = 1


class SeasonalNaiveForecaster(_LagForecaster):
    model_id = "seasonal_naive"
    lag_days = 7


# --------------------------------------------------------------------------- #
# Trained models                                                              #
# --------------------------------------------------------------------------- #
@dataclass
class _Fit:
    fitted_for_day: dt.date
    origin: pd.Timestamp
    model: Any
    n_rows: int
    data_cutoff: pd.Timestamp
    delivery_end: pd.Timestamp


class _TrainedForecaster(Forecaster):
    """Rolling recalibration on a trailing window of whole delivery days."""

    def __init__(self, window_days: int, recalibrate_every_days: int) -> None:
        if window_days < 1 or recalibrate_every_days < 1:
            raise ValueError("window_days and recalibrate_every_days must be >= 1")
        self.window_days = window_days
        self.recalibrate_every_days = recalibrate_every_days
        self._fit: _Fit | None = None

    def training_rows(
        self, table: FeatureTable, origin: pd.Timestamp, delivery_day: dt.date
    ) -> np.ndarray:
        """Rows of the trailing window that were fully public at ``origin``."""
        day = np.datetime64(delivery_day, "D")
        days = table.delivery_day
        in_window = (days < day) & (days >= day - np.timedelta64(self.window_days, "D"))
        public = (table.target_published_at <= origin).to_numpy() & (
            ~(table.feature_published_at > origin).to_numpy().any(axis=1)
        )
        complete = table.X.notna().all(axis=1).to_numpy() & table.y.notna().to_numpy()
        return np.flatnonzero(in_window & public & complete)

    def _needs_fit(self, delivery_day: dt.date) -> bool:
        f = self._fit
        if f is None or delivery_day < f.fitted_for_day:
            return True
        return (delivery_day - f.fitted_for_day).days >= self.recalibrate_every_days

    def _predict(
        self,
        table: FeatureTable,
        rows: np.ndarray,
        X: pd.DataFrame,
        origin: pd.Timestamp,
        delivery_day: dt.date,
    ) -> tuple[FloatArray, dict[str, Any]]:
        if self._needs_fit(delivery_day):
            train = self.training_rows(table, origin, delivery_day)
            check_training_rows(table, train, origin, delivery_day)
            min_rows = 24 * 14
            if len(train) < min_rows:
                raise ForecastError(
                    f"{self.model_id}: only {len(train)} training rows before "
                    f"{delivery_day} (need {min_rows})"
                )
            model = self._new_model()
            model.fit(table.X.iloc[train], table.y.iloc[train])
            step = table.X.index[1] - table.X.index[0]
            self._fit = _Fit(
                fitted_for_day=delivery_day,
                origin=origin,
                model=model,
                n_rows=len(train),
                data_cutoff=max(
                    table.target_published_at.iloc[train].max(),
                    table.feature_published_at.iloc[train].max().max(),
                ),
                delivery_end=table.X.index[train[-1]] + step,
            )
        f = self._fit
        assert f is not None
        pred = np.asarray(f.model.predict(X), dtype=np.float64)
        return pred, {
            "training_data_cutoff_utc": f.data_cutoff,
            "training_delivery_end_utc": f.delivery_end,
            "fitted_at_origin_utc": f.origin,
            "n_training_rows": f.n_rows,
        }

    @abstractmethod
    def _new_model(self) -> Any: ...


PRICE_FEATURES = (
    "price_lag_1d",
    "price_lag_2d",
    "price_lag_7d",
    "price_mean_lag_1d",
    "price_min_lag_1d",
    "price_max_lag_1d",
)
LOAD_FEATURES = ("load_forecast", "load_forecast_lag_1d")


class LearModel:
    """asinh(median/MAD)-scaled prices, standardised inputs, LassoLarsIC(AIC).

    Calendar effects enter as one-hot interval-of-day and day-of-week dummies.
    All statistics (median, MAD, means, standard deviations, dummy levels) are
    learned in :meth:`fit` from the training rows only.
    """

    def __init__(
        self, price_cols: tuple[str, ...], other_cols: tuple[str, ...]
    ) -> None:
        self.price_cols = price_cols
        self.other_cols = other_cols

    def _asinh(self, x: FloatArray) -> FloatArray:
        return np.asarray(np.arcsinh((x - self.median_) / self.mad_), dtype=np.float64)

    def _design(self, X: pd.DataFrame) -> FloatArray:
        prices = self._asinh(X[list(self.price_cols)].to_numpy(dtype=np.float64))
        other = X[list(self.other_cols)].to_numpy(dtype=np.float64)
        iod = X["interval_of_day"].to_numpy()
        dow = X["day_of_week"].to_numpy()
        dummies = (
            np.column_stack(
                [(iod == v).astype(np.float64) for v in self.iod_levels_[1:]]
                + [(dow == v).astype(np.float64) for v in self.dow_levels_[1:]]
            )
            if len(self.iod_levels_) + len(self.dow_levels_) > 2
            else np.empty((len(X), 0))
        )
        return np.column_stack([prices, other, dummies])

    def fit(self, X: pd.DataFrame, y: pd.Series) -> LearModel:
        target = y.to_numpy(dtype=np.float64)
        self.median_ = float(np.median(target))
        mad = float(np.median(np.abs(target - self.median_)))
        self.mad_ = mad if mad > 0 else 1.0
        self.iod_levels_ = np.unique(X["interval_of_day"].to_numpy())
        self.dow_levels_ = np.unique(X["day_of_week"].to_numpy())
        design = self._design(X)
        self.scaler_ = StandardScaler().fit(design)
        self.lasso_ = LassoLarsIC(criterion="aic", max_iter=2500)
        self.lasso_.fit(self.scaler_.transform(design), self._asinh(target))
        return self

    def predict(self, X: pd.DataFrame) -> FloatArray:
        z = self.lasso_.predict(self.scaler_.transform(self._design(X)))
        return np.asarray(np.sinh(z) * self.mad_ + self.median_, dtype=np.float64)


class LassoForecaster(_TrainedForecaster):
    model_id = "lasso"

    @property
    def features(self) -> tuple[str, ...]:
        return (*PRICE_FEATURES, *LOAD_FEATURES, "interval_of_day", "day_of_week")

    def config(self) -> dict[str, Any]:
        return {
            "model": "lasso_lear",
            "window_days": self.window_days,
            "recalibrate_every_days": self.recalibrate_every_days,
            "criterion": "aic",
            "transform": "asinh_median_mad",
        }

    def _new_model(self) -> LearModel:
        return LearModel(PRICE_FEATURES, LOAD_FEATURES)


class GbrForecaster(_TrainedForecaster):
    model_id = "gbr"

    def __init__(
        self,
        window_days: int,
        recalibrate_every_days: int,
        *,
        max_depth: int = 6,
        learning_rate: float = 0.05,
        max_iter: int = 400,
        l2_regularization: float = 1.0,
        random_state: int = 42,
    ) -> None:
        super().__init__(window_days, recalibrate_every_days)
        self.params = dict(
            max_depth=max_depth,
            learning_rate=learning_rate,
            max_iter=max_iter,
            l2_regularization=l2_regularization,
            random_state=random_state,
        )

    @property
    def features(self) -> tuple[str, ...]:
        return (
            *PRICE_FEATURES,
            *LOAD_FEATURES,
            "hour",
            "day_of_week",
            "month",
            "is_weekend",
        )

    def config(self) -> dict[str, Any]:
        return {
            "model": "hist_gradient_boosting",
            "window_days": self.window_days,
            "recalibrate_every_days": self.recalibrate_every_days,
            "early_stopping": False,
            **self.params,
        }

    def _new_model(self) -> HistGradientBoostingRegressor:
        return HistGradientBoostingRegressor(early_stopping=False, **self.params)
