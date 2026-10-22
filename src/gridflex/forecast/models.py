"""Day-ahead price forecasters.

A battery operator bids into the day-ahead auction *before* the 12:00 gate
closure using a forecast of tomorrow's prices, then lives with the realised
prices. These forecasters produce that ex-ante price curve. Every one of them
uses only information available at gate closure -- no lookahead:

  * PerfectForesight  the clairvoyant upper bound (returns realised prices).
  * NaivePersistence  "tomorrow == today" (price lagged 24h).
  * SeasonalNaive     "tomorrow == same weekday last week" (lagged 168h).
  * MLForecaster      gradient boosting on calendar features, the day-ahead
                      load forecast and price lags, trained walk-forward.
                      (PV/wind forecasts are excluded: they are published
                      after gate closure; audit D1.)

All expose ``forecast(dataset, start, end) -> pd.Series`` aligned to the
realised-price index over ``[start, end)``. ``dataset`` must include history
*before* ``start`` for warmup (lags / training).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

# Price lags usable at gate closure: all >= 24h, i.e. fully known before the
# delivery day. A 1h lag would be lookahead (unknown for future delivery hours).
LAG_HOURS = (24, 48, 168)
# Exogenous inputs public at the D-1 12:00 decision for delivery day D (see
# gridflex.data.availability). PV/wind forecasts for D are published ~18:00 on
# D-1 and are therefore excluded (audit D1).
EXOGENOUS_COLS = ("load_forecast",)
FEATURE_COLS = [
    "hour",
    "dayofweek",
    "month",
    "is_weekend",
    *EXOGENOUS_COLS,
    "price_lag_24",
    "price_lag_48",
    "price_lag_168",
]


def _as_ts(value, tz) -> pd.Timestamp:
    """Coerce a string or (tz-aware/naive) Timestamp to the given timezone."""
    ts = pd.Timestamp(value)
    return ts.tz_localize(tz) if ts.tz is None else ts.tz_convert(tz)


class Forecaster(ABC):
    """Abstract price forecaster."""

    name: str = "base"

    @abstractmethod
    def forecast(self, dataset: pd.DataFrame, start, end) -> pd.Series:
        """Return predicted prices indexed like realised prices over [start,end)."""

    @staticmethod
    def _target_index(dataset: pd.DataFrame, start, end) -> pd.DatetimeIndex:
        idx = dataset.index
        start = _as_ts(start, idx.tz)
        end = _as_ts(end, idx.tz)
        return idx[(idx >= start) & (idx < end)]


class PerfectForesight(Forecaster):
    """Clairvoyant benchmark: the forecast *is* the realised price."""

    name = "perfect"

    def forecast(self, dataset, start, end) -> pd.Series:
        tgt = self._target_index(dataset, start, end)
        return dataset.loc[tgt, "price"].rename("perfect")


class NaivePersistence(Forecaster):
    """Tomorrow looks like today (price shifted 24 hours)."""

    name = "naive"

    def forecast(self, dataset, start, end) -> pd.Series:
        tgt = self._target_index(dataset, start, end)
        shifted = dataset["price"].shift(freq="24h")
        return shifted.reindex(tgt).rename("naive")


class SeasonalNaive(Forecaster):
    """Tomorrow looks like the same weekday last week (price shifted 168 hours)."""

    name = "seasonal_naive"

    def forecast(self, dataset, start, end) -> pd.Series:
        tgt = self._target_index(dataset, start, end)
        shifted = dataset["price"].shift(freq="168h")
        return shifted.reindex(tgt).rename("seasonal_naive")


def build_features(dataset: pd.DataFrame) -> pd.DataFrame:
    """Assemble the ML design matrix (features + target ``price``).

    Rows whose lags fall before the data start contain NaNs and are dropped by
    the caller before fitting.
    """
    df = pd.DataFrame(index=dataset.index)
    idx = dataset.index
    df["hour"] = idx.hour
    df["dayofweek"] = idx.day_of_week
    df["month"] = idx.month
    df["is_weekend"] = (idx.day_of_week >= 5).astype(int)
    for col in EXOGENOUS_COLS:
        df[col] = dataset[col].to_numpy()
    for lag in LAG_HOURS:
        df[f"price_lag_{lag}"] = dataset["price"].shift(freq=f"{lag}h").reindex(idx)
    df["price"] = dataset["price"].to_numpy()
    return df


class MLForecaster(Forecaster):
    """Gradient-boosted day-ahead price forecast, trained walk-forward.

    To stay honest and fast, the model is retrained every ``retrain_every_days``
    on an expanding window of all history *strictly before* the delivery day,
    then used to predict whole delivery days at once. Nothing from the delivery
    day or later ever enters training.
    """

    name = "ml"

    def __init__(
        self,
        retrain_every_days: int = 30,
        min_train_hours: int = 24 * 60,
        random_state: int = 42,
        **model_kwargs,
    ):
        self.retrain_every_days = retrain_every_days
        self.min_train_hours = min_train_hours
        self.random_state = random_state
        self.model_kwargs = dict(
            learning_rate=0.05,
            max_iter=400,
            max_depth=6,
            l2_regularization=1.0,
            early_stopping=False,
            random_state=random_state,
        )
        self.model_kwargs.update(model_kwargs)

    def _new_model(self) -> HistGradientBoostingRegressor:
        return HistGradientBoostingRegressor(**self.model_kwargs)

    def forecast(self, dataset, start, end) -> pd.Series:
        feats = build_features(dataset).dropna(subset=[*FEATURE_COLS, "price"])
        tgt_index = self._target_index(dataset, start, end)
        tz = dataset.index.tz

        # Iterate delivery days in the market timezone.
        day_starts = pd.date_range(
            _as_ts(start, tz).normalize(),
            _as_ts(end, tz).normalize(),
            freq="D",
            tz=tz,
            inclusive="left",
        )

        preds = []
        fallback = SeasonalNaive()
        model = None
        days_since_fit = 10**9
        for day in day_starts:
            day_end = day + pd.Timedelta(days=1)
            day_rows = feats[(feats.index >= day) & (feats.index < day_end)]
            if day_rows.empty:
                continue

            train = feats[feats.index < day]
            if len(train) < self.min_train_hours:
                # Not enough history yet -> fall back to a rule of thumb.
                fb = fallback.forecast(dataset, day, day_end)
                preds.append(fb.rename("ml"))
                continue

            if model is None or days_since_fit >= self.retrain_every_days:
                model = self._new_model()
                model.fit(train[FEATURE_COLS], train["price"])
                days_since_fit = 0
            days_since_fit += 1

            yhat = model.predict(day_rows[FEATURE_COLS])
            preds.append(pd.Series(yhat, index=day_rows.index, name="ml"))

        if not preds:
            return pd.Series(index=tgt_index, dtype="float64", name="ml")
        out = pd.concat(preds)
        out = out[~out.index.duplicated(keep="last")]
        return out.reindex(tgt_index).rename("ml")


_REGISTRY: dict[
    str, type[PerfectForesight | NaivePersistence | SeasonalNaive | MLForecaster]
] = {
    "perfect": PerfectForesight,
    "naive": NaivePersistence,
    "seasonal_naive": SeasonalNaive,
    "ml": MLForecaster,
}


def get_forecaster(name: str, **kwargs) -> Forecaster:
    """Factory: ``get_forecaster("ml", retrain_every_days=30)``."""
    try:
        return _REGISTRY[name](**kwargs)
    except KeyError as exc:
        raise KeyError(
            f"unknown forecaster {name!r}; choose from {sorted(_REGISTRY)}"
        ) from exc
