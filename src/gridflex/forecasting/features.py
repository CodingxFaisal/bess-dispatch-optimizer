"""Feature table with per-value publication instants, and leakage guards.

Every feature value records *when the data behind it became public*, using
the availability registry (``data.series.*.availability``). Training and
prediction rows are only admitted if all their inputs, and for training also
the target, were public at the forecast origin. Violations raise
:class:`~gridflex.data.errors.InformationLeakageError`; they are never
silently dropped or masked.

Lag alignment (audit D10). ``price_lag_{k}d`` for an interval at local
wall-clock time ``hh:mm`` on day D is the price at ``hh:mm`` on day D-k:

* On a 25 h day, both occurrences of 02:00 map to the same wall-clock time on
  D-k. If D-k is itself the 25 h day, its first (CEST) occurrence is used.
* Where ``hh:mm`` does not exist on D-k (02:00-02:59 on the 23 h spring day),
  the value from exactly ``k * 24`` hours earlier (UTC) is used.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from gridflex.config.models import GridFlexConfig
from gridflex.data.availability import available_from
from gridflex.data.errors import InformationLeakageError

CALENDAR_FEATURES = ("interval_of_day", "hour", "day_of_week", "month", "is_weekend")


def _day_index(index: pd.DatetimeIndex, timezone: str) -> np.ndarray:
    """Local delivery day of each UTC interval start, as numpy datetime64[D]."""
    return np.asarray(
        index.tz_convert(timezone).tz_localize(None).normalize(), dtype="datetime64[D]"
    )


def wall_clock_lag(
    series: pd.Series, target: pd.DatetimeIndex, days: int, timezone: str
) -> pd.Series:
    """Values of ``series`` at the same local wall-clock time ``days`` earlier."""
    wall = target.tz_convert(timezone).tz_localize(None) - pd.Timedelta(days=days)
    lagged = wall.tz_localize(
        timezone, ambiguous=np.ones(len(wall), dtype=bool), nonexistent="NaT"
    ).tz_convert("UTC")
    fallback = target - pd.Timedelta(hours=24 * days)
    lag_index = lagged.where(~lagged.isna(), fallback)
    return pd.Series(series.reindex(lag_index).to_numpy(), index=target)


@dataclass(frozen=True)
class FeatureTable:
    """Features ``X``, target ``y`` and publication instants, on a UTC grid."""

    X: pd.DataFrame
    y: pd.Series
    feature_published_at: pd.DataFrame  # same shape as X (UTC instants; NaT = n/a)
    target_published_at: pd.Series
    delivery_day: np.ndarray  # datetime64[D], local
    timezone: str

    @property
    def row_published_at(self) -> pd.Series:
        """Latest publication instant over each row's features."""
        return self.feature_published_at.max(axis=1)

    def rows_for_day(self, day: dt.date) -> np.ndarray:
        return np.flatnonzero(self.delivery_day == np.datetime64(day, "D"))

    def subset(self, columns: Sequence[str]) -> FeatureTable:
        cols = list(columns)
        return FeatureTable(
            self.X[cols],
            self.y,
            self.feature_published_at[cols],
            self.target_published_at,
            self.delivery_day,
            self.timezone,
        )


def _publication_instants(
    cfg: GridFlexConfig, key: str, days: np.ndarray, lag_days: int
) -> np.ndarray:
    """Publication instant of ``key`` for delivery day ``day - lag_days`` per row."""
    rule = cfg.data.series[key].availability
    tz = cfg.market.timezone
    ref = days - np.timedelta64(lag_days, "D")
    uniq, inverse = np.unique(ref, return_inverse=True)
    inst = pd.DatetimeIndex([available_from(rule, d.astype(object), tz) for d in uniq])
    naive_utc: np.ndarray = (
        inst.tz_convert("UTC").tz_localize(None).to_numpy("datetime64[ns]")
    )
    return naive_utc[inverse]


def build_feature_table(
    frame: pd.DataFrame,
    cfg: GridFlexConfig,
    *,
    price_lags_days: Sequence[int] = (1, 2, 7),
    exogenous: Sequence[tuple[str, int]] = (("load_forecast", 0), ("load_forecast", 1)),
) -> FeatureTable:
    """Build features for every interval of ``frame`` (UTC index, cleaned units).

    ``exogenous`` lists ``(series_key, lag_days)`` pairs; lag 0 means the value
    for the delivery interval itself (e.g. the day-ahead load forecast for D).
    """
    if "price" not in frame:
        raise KeyError("frame needs a 'price' column")
    idx = pd.DatetimeIndex(frame.index)
    if str(idx.tz) != "UTC":
        raise ValueError("frame index must be UTC")
    tz = cfg.market.timezone
    local = idx.tz_convert(tz)
    days = _day_index(idx, tz)
    price = frame["price"].astype("float64")
    # Interval length from Timedelta arithmetic (unit-safe under pandas 3).
    step_min = (
        float(pd.Series(idx).diff().mode().iloc[0] / pd.Timedelta(minutes=1))
        if len(idx) > 1
        else 60.0
    )

    cols: dict[str, np.ndarray] = {}
    pub: dict[str, np.ndarray] = {}
    never = np.full(len(idx), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")

    minutes = local.hour * 60 + local.minute
    cols["interval_of_day"] = np.asarray(
        minutes // max(round(step_min), 1), dtype=np.float64
    )
    cols["hour"] = np.asarray(local.hour, dtype=np.float64)
    cols["day_of_week"] = np.asarray(local.day_of_week, dtype=np.float64)
    cols["month"] = np.asarray(local.month, dtype=np.float64)
    cols["is_weekend"] = np.asarray(local.day_of_week >= 5, dtype=np.float64)
    for c in CALENDAR_FEATURES:
        pub[c] = never  # deterministic calendar: no publication constraint

    for k in price_lags_days:
        name = f"price_lag_{k}d"
        cols[name] = wall_clock_lag(price, idx, k, tz).to_numpy()
        pub[name] = _publication_instants(cfg, "price", days, k)

    # Daily statistics of the previous local day's prices (NaN if incomplete).
    by_day = pd.Series(price.to_numpy(), index=days)
    stats = by_day.groupby(level=0).agg(["mean", "min", "max"])
    incomplete = by_day.isna().groupby(level=0).any()
    stats[incomplete] = np.nan
    prev = days - np.timedelta64(1, "D")
    for stat in ("mean", "min", "max"):
        name = f"price_{stat}_lag_1d"
        cols[name] = stats[stat].reindex(prev).to_numpy()
        pub[name] = _publication_instants(cfg, "price", days, 1)

    for key, lag in exogenous:
        if key not in frame:
            raise KeyError(f"frame has no column {key!r} for exogenous feature")
        name = key if lag == 0 else f"{key}_lag_{lag}d"
        values = frame[key].astype("float64")
        cols[name] = (
            values.to_numpy()
            if lag == 0
            else wall_clock_lag(values, idx, lag, tz).to_numpy()
        )
        pub[name] = _publication_instants(cfg, key, days, lag)

    X = pd.DataFrame(cols, index=idx)
    published = pd.DataFrame(
        {c: pd.DatetimeIndex(v).tz_localize("UTC") for c, v in pub.items()}, index=idx
    )
    return FeatureTable(
        X=X,
        y=price.rename("price"),
        feature_published_at=published,
        target_published_at=pd.Series(
            pd.DatetimeIndex(_publication_instants(cfg, "price", days, 0)).tz_localize(
                "UTC"
            ),
            index=idx,
        ),
        delivery_day=days,
        timezone=tz,
    )


# --------------------------------------------------------------------------- #
# Guards                                                                      #
# --------------------------------------------------------------------------- #
def check_prediction_rows(
    table: FeatureTable, rows: np.ndarray, origin: pd.Timestamp
) -> None:
    """Raise if any feature value of the forecast rows postdates ``origin``."""
    pub = table.feature_published_at.iloc[rows]
    late = (pub > origin).any(axis=0)
    if late.any():
        bad = list(pub.columns[late.to_numpy()])
        first = pub[bad[0]].max()
        raise InformationLeakageError(
            f"features {bad} are published after the origin {origin.isoformat()} "
            f"(e.g. {bad[0]} at {first.isoformat()})"
        )


def check_training_rows(
    table: FeatureTable, rows: np.ndarray, origin: pd.Timestamp, delivery_day: dt.date
) -> None:
    """Raise unless all training rows precede the target day and were public."""
    if len(rows) == 0:
        return
    target_day = np.datetime64(delivery_day, "D")
    days = table.delivery_day[rows]
    if np.any(days >= target_day):
        raise InformationLeakageError(
            f"training rows include delivery day(s) >= {delivery_day} "
            "(the forecast target or later)"
        )
    if (table.target_published_at.iloc[rows] > origin).any():
        raise InformationLeakageError(
            f"training targets published after the origin {origin.isoformat()}"
        )
    if (table.feature_published_at.iloc[rows] > origin).to_numpy().any():
        raise InformationLeakageError(
            f"training features published after the origin {origin.isoformat()}"
        )
