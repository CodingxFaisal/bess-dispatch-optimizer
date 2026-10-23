"""Forecast records and errors.

A :class:`PriceForecast` is the only thing GridFlex's dispatch receives from a
forecaster. It carries the values *and* the information-set metadata required
to audit it (spec §9):

* ``origin_utc``: the decision instant the forecast was issued for (D-1
  12:00 Europe/Berlin for day-ahead delivery day D).
* ``values``: EUR/MWh per delivery interval (UTC interval starts) of D.
* ``model_id`` / ``model_version`` / ``config``: what produced it.
* ``training_data_cutoff_utc``: the latest *publication* instant of any
  training target or feature. It is always <= origin. The training delivery
  intervals themselves may end after the origin: day-ahead prices for D-1
  were published on D-2 and are legitimately known at D-1 12:00.
* ``training_delivery_end_utc``: end of the last training delivery interval.
* ``feature_cutoff_utc``: the latest publication instant of any input used for
  the forecast rows. It is always <= origin.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


class ForecastError(RuntimeError):
    """A forecast could not be produced (for example, missing inputs)."""


@dataclass(frozen=True)
class PriceForecast:
    delivery_day: dt.date
    origin_utc: pd.Timestamp
    values: pd.Series
    model_id: str
    model_version: str
    feature_cutoff_utc: pd.Timestamp
    features: tuple[str, ...]
    training_data_cutoff_utc: pd.Timestamp | None = None
    training_delivery_end_utc: pd.Timestamp | None = None
    fitted_at_origin_utc: pd.Timestamp | None = None
    n_training_rows: int = 0
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        idx = self.values.index
        if not isinstance(idx, pd.DatetimeIndex) or str(idx.tz) != "UTC":
            raise ValueError("forecast values need a UTC DatetimeIndex")
        if not idx.is_unique or not idx.is_monotonic_increasing:
            raise ValueError("forecast index must be unique and increasing")
        arr = self.values.to_numpy(dtype=np.float64)
        if len(arr) == 0 or not np.all(np.isfinite(arr)):
            raise ForecastError(
                f"{self.model_id}: forecast for {self.delivery_day} has missing or "
                "non-finite values (no silent filling)"
            )
        if self.feature_cutoff_utc > self.origin_utc:
            raise ValueError("feature_cutoff_utc must not be after the origin")
        if (
            self.training_data_cutoff_utc is not None
            and self.training_data_cutoff_utc > self.origin_utc
        ):
            raise ValueError("training_data_cutoff_utc must not be after the origin")

    def to_frame(self) -> pd.DataFrame:
        """One row per delivery interval, metadata repeated (for CSV export)."""
        return pd.DataFrame(
            {
                "delivery_interval_utc": self.values.index,
                "delivery_day": self.delivery_day,
                "price_forecast_eur_per_mwh": self.values.to_numpy(),
                "origin_utc": self.origin_utc,
                "model_id": self.model_id,
                "model_version": self.model_version,
                "feature_cutoff_utc": self.feature_cutoff_utc,
                "training_data_cutoff_utc": self.training_data_cutoff_utc,
                "training_delivery_end_utc": self.training_delivery_end_utc,
                "fitted_at_origin_utc": self.fitted_at_origin_utc,
                "n_training_rows": self.n_training_rows,
            }
        )
