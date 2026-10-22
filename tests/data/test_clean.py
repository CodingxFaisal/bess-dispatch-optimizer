"""Cleaning: duplicates, sorting, unit conversion; NaN is never filled."""

import math

import numpy as np
import pandas as pd
import pytest

from gridflex.config import GridFlexConfig
from gridflex.data.cache import CacheEntryMeta
from gridflex.data.clean import clean_series, collapse_duplicates
from gridflex.data.errors import DataIntegrityError
from gridflex.data.smard import RawSeries

T = pd.Timestamp("2025-10-01 00:00", tz="UTC")


def _raw(
    series_id: int, resolution: str, stamps: list[pd.Timestamp], values: list[float]
) -> RawSeries:
    idx = pd.DatetimeIndex(stamps, name="timestamp_utc")
    meta = CacheEntryMeta("u", "2026-01-01T00:00:00+00:00", "h", 200, 1)
    return RawSeries(
        series_id,
        "DE",
        resolution,
        stamps[0],
        stamps[-1],
        pd.Series(values, index=idx, dtype="float64"),  # type: ignore[arg-type]
        meta,
        (),
    )


def test_identical_duplicates_collapsed_and_counted() -> None:
    idx = pd.DatetimeIndex([T, T, T + pd.Timedelta(hours=1), T + pd.Timedelta(hours=1)])
    s = pd.Series([1.0, 1.0, math.nan, math.nan], index=idx)
    out, dropped = collapse_duplicates(s, "x")
    assert dropped == 2
    assert out.index.is_unique and len(out) == 2
    assert math.isnan(out.iloc[1])  # NaN duplicates stay NaN


def test_conflicting_duplicates_raise() -> None:
    idx = pd.DatetimeIndex([T, T])
    with pytest.raises(DataIntegrityError, match="conflicting"):
        collapse_duplicates(pd.Series([1.0, 2.0], index=idx), "x")
    with pytest.raises(DataIntegrityError):
        collapse_duplicates(pd.Series([1.0, math.nan], index=idx), "x")


def test_price_unchanged_and_sorted(gf_cfg: GridFlexConfig) -> None:
    stamps = [T + pd.Timedelta(hours=1), T]
    out = clean_series(
        _raw(4169, "hour", stamps, [-5.0, 100.0]), "price", gf_cfg.data.series["price"]
    )
    assert out.unit == "EUR/MWh"
    assert out.values.tolist() == [100.0, -5.0]
    assert out.values.index.is_monotonic_increasing


def test_energy_per_interval_converted_to_mean_mw(gf_cfg: GridFlexConfig) -> None:
    spec = gf_cfg.data.series["load_forecast"]
    # Hourly: 45 000 MWh in one hour = 45 000 MW mean.
    hourly = clean_series(_raw(411, "hour", [T], [45_000.0]), "load_forecast", spec)
    assert hourly.values.iloc[0] == pytest.approx(45_000.0)
    assert hourly.unit == "MW"
    # Quarter-hour: 11 250 MWh in 15 min = 45 000 MW mean (x4), NaN stays NaN.
    stamps = [T, T + pd.Timedelta(minutes=15)]
    qh = clean_series(
        _raw(411, "quarterhour", stamps, [11_250.0, math.nan]), "load_forecast", spec
    )
    assert qh.values.iloc[0] == pytest.approx(45_000.0)
    assert np.isnan(qh.values.iloc[1])


def test_series_id_mismatch_rejected(gf_cfg: GridFlexConfig) -> None:
    with pytest.raises(ValueError, match="SMARD 4169"):
        clean_series(
            _raw(411, "hour", [T], [1.0]), "price", gf_cfg.data.series["price"]
        )
