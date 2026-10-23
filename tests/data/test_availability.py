"""Information availability at the day-ahead decision (hand-computed instants)."""

import datetime as dt

import pandas as pd
import pytest

from gridflex.config import GridFlexConfig
from gridflex.data.availability import (
    assert_available,
    available_from,
    day_ahead_usable_series,
    decision_time,
    latest_available_delivery_day,
)
from gridflex.data.errors import InformationLeakageError

TZ = "Europe/Berlin"


def utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


@pytest.mark.parametrize(
    ("delivery_day", "expected_utc"),
    [
        (dt.date(2024, 7, 2), "2024-07-01 10:00"),  # CEST: 12:00 local = 10:00 UTC
        (dt.date(2024, 1, 2), "2024-01-01 11:00"),  # CET:  12:00 local = 11:00 UTC
        (dt.date(2024, 3, 31), "2024-03-30 11:00"),  # decision on Sat, still CET
        (dt.date(2024, 4, 1), "2024-03-31 10:00"),  # decision on DST day, now CEST
        (dt.date(2024, 10, 28), "2024-10-27 11:00"),  # decision after fall-back, CET
    ],
)
def test_decision_time(
    gf_cfg: GridFlexConfig, delivery_day: dt.date, expected_utc: str
) -> None:
    assert decision_time(gf_cfg.market, delivery_day) == utc(expected_utc)


def test_publication_instants(gf_cfg: GridFlexConfig) -> None:
    s = gf_cfg.data.series
    d = dt.date(2024, 7, 2)
    assert available_from(s["price"].availability, d, TZ) == utc(
        "2024-07-01 11:00"
    )  # 13:00 CEST
    assert available_from(s["load_forecast"].availability, d, TZ) == utc(
        "2024-07-01 08:00"
    )
    assert available_from(s["solar_forecast"].availability, d, TZ) == utc(
        "2024-07-01 16:00"
    )


def test_day_ahead_usable_series_excludes_pv_wind_and_target_price(
    gf_cfg: GridFlexConfig,
) -> None:
    for day in (dt.date(2024, 1, 15), dt.date(2024, 7, 15), dt.date(2025, 10, 15)):
        assert day_ahead_usable_series(gf_cfg, day) == ["load_forecast"]


def test_latest_available_delivery_day(gf_cfg: GridFlexConfig) -> None:
    s = gf_cfg.data.series
    d = dt.date(2024, 7, 2)
    at = decision_time(gf_cfg.market, d)
    assert latest_available_delivery_day(s["price"].availability, at, TZ) == dt.date(
        2024, 7, 1
    )
    assert latest_available_delivery_day(s["load_forecast"].availability, at, TZ) == d
    assert latest_available_delivery_day(
        s["solar_forecast"].availability, at, TZ
    ) == dt.date(2024, 7, 1)
    # One minute after the solar publication instant the D forecast is public.
    later = available_from(s["solar_forecast"].availability, d, TZ) + pd.Timedelta(
        minutes=1
    )
    assert (
        latest_available_delivery_day(s["solar_forecast"].availability, later, TZ) == d
    )


def test_assert_available_raises_on_leakage(gf_cfg: GridFlexConfig) -> None:
    d = dt.date(2024, 7, 2)
    at = decision_time(gf_cfg.market, d)
    day_d = pd.date_range("2024-07-02", periods=24, freq="h", tz=TZ)
    day_prev = pd.date_range("2024-07-01", periods=24, freq="h", tz=TZ)
    assert_available(gf_cfg, "load_forecast", day_d, at)
    assert_available(gf_cfg, "solar_forecast", day_prev, at)
    assert_available(gf_cfg, "price", day_prev, at)
    with pytest.raises(InformationLeakageError, match="solar_forecast"):
        assert_available(gf_cfg, "solar_forecast", day_d, at)
    with pytest.raises(InformationLeakageError, match="price"):
        assert_available(gf_cfg, "price", day_d, at)
    with pytest.raises(ValueError):
        assert_available(gf_cfg, "price", day_d.tz_localize(None), at)


def test_configured_forecast_inputs_are_public_at_decision(
    gf_cfg: GridFlexConfig,
) -> None:
    """Guard for audit D1: every configured exogenous forecast input must be
    day-ahead usable."""
    assert gf_cfg.forecasting is not None
    usable = set(day_ahead_usable_series(gf_cfg, dt.date(2024, 7, 2)))
    assert set(gf_cfg.forecasting.exogenous_series) <= usable
