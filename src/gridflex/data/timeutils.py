"""Time-axis conventions.

* Identity: every timestamp is a timezone-aware UTC instant marking the
  *start* of a market time unit.
* Display / calendar: local delivery days and clock times are in the market
  timezone (Europe/Berlin), so a delivery day lasts 23, 24 or 25 hours.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from gridflex.config.models import RESOLUTION_MINUTES

UTC = "UTC"


def require_utc(
    ts: pd.Timestamp | dt.datetime | str, name: str = "timestamp"
) -> pd.Timestamp:
    """Return ``ts`` as a UTC :class:`pandas.Timestamp`; reject naive inputs."""
    out = pd.Timestamp(ts)
    if out.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware, got naive {out}")
    return out.tz_convert(UTC)


def local_day_start_utc(day: dt.date, timezone: str) -> pd.Timestamp:
    """UTC instant of local midnight starting ``day``.

    Midnight exists exactly once in Europe/Berlin (DST shifts at 02:00/03:00).
    """
    return pd.Timestamp(day).tz_localize(timezone).tz_convert(UTC)


def local_window_utc(
    start_day: dt.date, end_day: dt.date, timezone: str
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """UTC bounds of the local delivery days ``[start_day, end_day)``."""
    if not start_day < end_day:
        raise ValueError(f"start_day {start_day} must be before end_day {end_day}")
    return local_day_start_utc(start_day, timezone), local_day_start_utc(
        end_day, timezone
    )


def interval_minutes(resolution: str) -> int:
    return RESOLUTION_MINUTES[resolution]


def interval_hours(resolution: str) -> float:
    return RESOLUTION_MINUTES[resolution] / 60.0


def expected_index(
    start_utc: pd.Timestamp, end_utc: pd.Timestamp, resolution: str
) -> pd.DatetimeIndex:
    """Complete grid of interval starts in ``[start_utc, end_utc)``."""
    start = require_utc(start_utc, "start_utc")
    end = require_utc(end_utc, "end_utc")
    step = pd.Timedelta(minutes=interval_minutes(resolution))
    if (start - start.normalize()) % step or (end - end.normalize()) % step:
        raise ValueError(f"window bounds must be aligned to {resolution} intervals")
    return pd.date_range(start, end, freq=step, inclusive="left", name="timestamp_utc")


def local_delivery_days(index: pd.DatetimeIndex, timezone: str) -> pd.Index:
    """Local calendar delivery day of each interval start."""
    return pd.Index(index.tz_convert(timezone).date, name="delivery_day")


def to_local(index: pd.DatetimeIndex, timezone: str) -> pd.DatetimeIndex:
    """Display view of a UTC index in the market timezone."""
    return index.tz_convert(timezone)
