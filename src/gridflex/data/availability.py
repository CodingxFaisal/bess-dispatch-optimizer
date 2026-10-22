"""Information-availability registry: what is public at a decision time.

Each configured series carries an :class:`~gridflex.config.AvailabilityRule`:
values for local delivery day ``D`` are public from ``(D + day_offset)`` at
``local_time`` (market timezone). The day-ahead decision for delivery day
``D`` is taken at ``D - 1`` at the gate-closure time (12:00 Europe/Berlin).

With the default configuration, at the decision for day D:

* prices are known up to and including day D-1 (D's prices clear afterwards);
* the load forecast for D is known (published ~10:00 on D-1);
* PV and wind forecasts for D are **not** known (published ~18:00 on D-1);
  the latest known PV/wind forecast is for day D-1.

These rules describe *publication time*. SMARD keeps only one (final) vintage
of each forecast, so even a correctly-timed value may differ from what was
visible at the time; that limitation is documented, not modelled.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from gridflex.config.models import AvailabilityRule, GridFlexConfig, MarketConfig
from gridflex.data.errors import InformationLeakageError
from gridflex.data.timeutils import UTC, require_utc


def _local_instant(day: dt.date, clock: dt.time, timezone: str) -> pd.Timestamp:
    naive = pd.Timestamp(dt.datetime.combine(day, clock))
    # Publication / gate-closure clock times are daytime, never inside the
    # 02:00-03:00 DST gap or fold, so localisation is unambiguous.
    return naive.tz_localize(timezone).tz_convert(UTC)


def decision_time(market: MarketConfig, delivery_day: dt.date) -> pd.Timestamp:
    """UTC instant of day-ahead gate closure for ``delivery_day`` (D-1, 12:00 local)."""
    return _local_instant(
        delivery_day - dt.timedelta(days=1),
        market.day_ahead_gate_closure_local,
        market.timezone,
    )


def available_from(
    rule: AvailabilityRule, delivery_day: dt.date, timezone: str
) -> pd.Timestamp:
    """UTC instant from which values for ``delivery_day`` are public."""
    return _local_instant(
        delivery_day + dt.timedelta(days=rule.day_offset), rule.local_time, timezone
    )


def latest_available_delivery_day(
    rule: AvailabilityRule, at: pd.Timestamp | dt.datetime, timezone: str
) -> dt.date:
    """Most recent local delivery day whose values are public at instant ``at``."""
    at_utc = require_utc(at, "at")
    local_day: dt.date = at_utc.tz_convert(timezone).date()
    # Values for day D are public on local day D + day_offset, so the answer is
    # local_day - day_offset or the day before; start one day later to be safe.
    candidate = local_day - dt.timedelta(days=rule.day_offset) + dt.timedelta(days=1)
    for _ in range(4):
        if available_from(rule, candidate, timezone) <= at_utc:
            return candidate
        candidate -= dt.timedelta(days=1)
    raise AssertionError("unreachable: availability search did not converge")


def assert_available(
    cfg: GridFlexConfig,
    series_key: str,
    delivery_index: pd.DatetimeIndex,
    at: pd.Timestamp | dt.datetime,
) -> None:
    """Raise :class:`InformationLeakageError` if any interval in ``delivery_index``
    of ``series_key`` was not yet public at instant ``at``."""
    rule = cfg.data.series[series_key].availability
    at_utc = require_utc(at, "at")
    tz = cfg.market.timezone
    if delivery_index.tz is None:
        raise ValueError("delivery_index must be timezone-aware")
    days = sorted(set(delivery_index.tz_convert(tz).date))
    late = [d for d in days if available_from(rule, d, tz) > at_utc]
    if late:
        first = late[0]
        raise InformationLeakageError(
            f"{series_key!r} for delivery day {first} is public from "
            f"{available_from(rule, first, tz).isoformat()}, after the decision "
            f"instant {at_utc.isoformat()} ({len(late)} day(s) affected; rule: "
            f"{rule.source})"
        )


def day_ahead_usable_series(cfg: GridFlexConfig, delivery_day: dt.date) -> list[str]:
    """Series whose *delivery-day* values are public at the day-ahead decision."""
    at = decision_time(cfg.market, delivery_day)
    tz = cfg.market.timezone
    return [
        key
        for key, spec in cfg.data.series.items()
        if available_from(spec.availability, delivery_day, tz) <= at
    ]
