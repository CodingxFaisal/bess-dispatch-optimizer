"""Feature construction (hand-checked), model behaviour and evaluation metrics."""

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from gridflex.config import DayPeriod, GridFlexConfig
from gridflex.data.availability import decision_time
from gridflex.forecasting import (
    ForecastError,
    GbrForecaster,
    LassoForecaster,
    PersistenceForecaster,
    PriceForecast,
    SeasonalNaiveForecaster,
    accuracy,
    build_feature_table,
    days_in,
    dm_test,
    forecasts_frame,
    rolling_forecasts,
    wall_clock_lag,
)
from tests_helpers import synthetic_market_frame

TZ = "Europe/Berlin"


def utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


@pytest.fixture(scope="module")
def counter() -> pd.Series:
    """Hourly series whose value is the hour count since 2024-03-25 00:00 local."""
    start = pd.Timestamp("2024-03-25").tz_localize(TZ)
    idx = pd.date_range(start, start + pd.offsets.Day(14), freq="h", inclusive="left")
    return pd.Series(np.arange(len(idx), dtype=float), index=idx.tz_convert("UTC"))


# --------------------------------------------------------------------------- #
# Wall-clock lags across DST (hand-checked)                                   #
# --------------------------------------------------------------------------- #
def test_lag_on_ordinary_day(counter: pd.Series) -> None:
    # 2024-03-27 05:00 CET (04:00 UTC) <- 2024-03-26 05:00 CET (04:00 UTC).
    target = pd.DatetimeIndex([utc("2024-03-27 04:00")])
    assert (
        wall_clock_lag(counter, target, 1, TZ).iloc[0]
        == counter[utc("2024-03-26 04:00")]
    )


def test_lag_into_spring_gap_falls_back_to_24h(counter: pd.Series) -> None:
    # 2024-04-01 02:00 CEST (00:00 UTC): 02:00 does not exist on 31 Mar, so the
    # value from exactly 24 h earlier (2024-03-31 00:00 UTC = 01:00 CET) is used.
    target = pd.DatetimeIndex([utc("2024-04-01 00:00")])
    assert (
        wall_clock_lag(counter, target, 1, TZ).iloc[0]
        == counter[utc("2024-03-31 00:00")]
    )


def test_lag_from_spring_day_uses_wall_clock(counter: pd.Series) -> None:
    # 2024-03-31 03:00 CEST (01:00 UTC) <- 2024-03-30 03:00 CET (02:00 UTC).
    target = pd.DatetimeIndex([utc("2024-03-31 01:00")])
    assert (
        wall_clock_lag(counter, target, 1, TZ).iloc[0]
        == counter[utc("2024-03-30 02:00")]
    )


def test_lag_on_autumn_day_maps_both_0200_to_same_value() -> None:
    start = pd.Timestamp("2024-10-20").tz_localize(TZ)
    idx = pd.date_range(start, start + pd.offsets.Day(14), freq="h", inclusive="left")
    s = pd.Series(np.arange(len(idx), dtype=float), index=idx.tz_convert("UTC"))
    # 27 Oct 02:00 CEST (00:00 UTC) and 02:00 CET (01:00 UTC) <- 26 Oct 02:00 CEST.
    target = pd.DatetimeIndex([utc("2024-10-27 00:00"), utc("2024-10-27 01:00")])
    lag = wall_clock_lag(s, target, 1, TZ)
    assert lag.tolist() == [s[utc("2024-10-26 00:00")]] * 2
    # 28 Oct 02:00 CET (01:00 UTC) <- 27 Oct 02:00, first occurrence (CEST, 00:00 UTC).
    lag28 = wall_clock_lag(s, pd.DatetimeIndex([utc("2024-10-28 01:00")]), 1, TZ)
    assert lag28.iloc[0] == s[utc("2024-10-27 00:00")]


def test_feature_table_calendar_and_daily_stats(gf_cfg: GridFlexConfig) -> None:
    frame = synthetic_market_frame("2024-03-25", 14)
    t = build_feature_table(frame, gf_cfg)
    row = t.X.loc[utc("2024-04-01 08:00")]  # 10:00 CEST, Monday
    assert (row.hour, row.day_of_week, row.month, row.is_weekend) == (10, 0, 4, 0)
    assert row.interval_of_day == 10
    # Mean/min/max of the previous local day (31 Mar: a 23-hour day).
    prev = frame.loc[
        (frame.index >= utc("2024-03-30 23:00"))
        & (frame.index < utc("2024-03-31 22:00")),
        "price",
    ]
    assert len(prev) == 23
    assert row.price_mean_lag_1d == pytest.approx(prev.mean())
    assert (row.price_min_lag_1d, row.price_max_lag_1d) == (prev.min(), prev.max())
    # Publication instants (hand): price of 31 Mar public 30 Mar 13:00 CET.
    assert t.feature_published_at.loc[utc("2024-04-01 08:00"), "price_mean_lag_1d"] == (
        utc("2024-03-30 12:00")
    )
    assert t.feature_published_at.loc[utc("2024-04-01 08:00"), "load_forecast"] == (
        utc("2024-03-31 08:00")
    )  # 10:00 CEST on 31 Mar
    assert pd.isna(t.feature_published_at.loc[utc("2024-04-01 08:00"), "hour"])


def test_incomplete_previous_day_gives_nan_stats(gf_cfg: GridFlexConfig) -> None:
    frame = synthetic_market_frame("2024-05-01", 10)
    frame.loc[utc("2024-05-03 10:00"), "price"] = np.nan
    t = build_feature_table(frame, gf_cfg)
    assert (
        t.X.loc[utc("2024-05-04 10:00"), "price_mean_lag_1d"]
        != t.X.loc[utc("2024-05-04 10:00"), "price_mean_lag_1d"]
    )  # NaN
    assert not math.isnan(t.X.loc[utc("2024-05-05 10:00"), "price_mean_lag_1d"])


def test_quarter_hour_interval_of_day(gf_cfg: GridFlexConfig) -> None:
    frame = synthetic_market_frame("2025-10-01", 3, freq="15min")
    t = build_feature_table(frame, gf_cfg)
    day = t.X.iloc[t.rows_for_day(dt.date(2025, 10, 2))]
    assert day.interval_of_day.tolist() == list(range(96))


# --------------------------------------------------------------------------- #
# Models                                                                      #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def table_and_frame(gf_cfg: GridFlexConfig) -> tuple:
    frame = synthetic_market_frame("2024-01-01", 150, seed=3)
    return build_feature_table(frame, gf_cfg), frame


def test_naive_forecasts_equal_lagged_prices(gf_cfg, table_and_frame) -> None:  # type: ignore[no-untyped-def]
    table, frame = table_and_frame
    day = dt.date(2024, 4, 10)
    p = PersistenceForecaster().forecast_day(table, gf_cfg.market, day)
    s = SeasonalNaiveForecaster().forecast_day(table, gf_cfg.market, day)
    # 10 Apr is not a DST day: same wall-clock time on D-1 is exactly 24 h earlier.
    np.testing.assert_array_equal(
        p.values.to_numpy(),
        frame["price"].reindex(p.values.index - pd.Timedelta(hours=24)).to_numpy(),
    )
    np.testing.assert_array_equal(
        s.values.to_numpy(),
        frame["price"].reindex(s.values.index - pd.Timedelta(days=7)).to_numpy(),
    )
    assert p.origin_utc == decision_time(gf_cfg.market, day)
    assert p.training_data_cutoff_utc is None and p.n_training_rows == 0


def test_trained_models_beat_naive_on_structured_data(gf_cfg, table_and_frame) -> None:  # type: ignore[no-untyped-def]
    table, frame = table_and_frame
    days = days_in(
        DayPeriod(start_day=dt.date(2024, 4, 1), end_day=dt.date(2024, 5, 1))
    )
    actual = frame["price"]
    naive = forecasts_frame(
        rolling_forecasts(SeasonalNaiveForecaster(), table, gf_cfg.market, days), actual
    )
    # The synthetic price has N(0, 4) noise, so no model can beat an MAE of
    # 4 * sqrt(2 / pi) = 3.19 EUR/MWh. Both models must come within 30 % of
    # that floor and clearly beat the weekly naive benchmark.
    floor = 4.0 * math.sqrt(2.0 / math.pi)
    for model in (LassoForecaster(56, 1), GbrForecaster(56, 7, max_iter=100)):
        fr = forecasts_frame(
            rolling_forecasts(model, table, gf_cfg.market, days), actual
        )
        acc = accuracy(fr, naive)
        assert acc.mae < 1.3 * floor, (model.model_id, acc)
        assert acc.rmae is not None and acc.rmae < 0.85, (model.model_id, acc)


def test_recalibration_cadence(gf_cfg, table_and_frame) -> None:  # type: ignore[no-untyped-def]
    table, _ = table_and_frame
    days = [dt.date(2024, 4, 1) + dt.timedelta(days=i) for i in range(15)]
    fcs = rolling_forecasts(
        GbrForecaster(56, 7, max_iter=30), table, gf_cfg.market, days
    )
    fits = [f.fitted_at_origin_utc for f in fcs]
    assert len(set(fits)) == 3  # refits on day 0, 7, 14
    assert fits[0] == fits[6] != fits[7] == fits[13] != fits[14]
    assert all(f.fitted_at_origin_utc <= f.origin_utc for f in fcs)  # type: ignore[operator]


def test_gbr_is_deterministic(gf_cfg, table_and_frame) -> None:  # type: ignore[no-untyped-def]
    table, _ = table_and_frame
    day = dt.date(2024, 4, 20)
    a = GbrForecaster(56, 7, max_iter=50).forecast_day(table, gf_cfg.market, day)
    b = GbrForecaster(56, 7, max_iter=50).forecast_day(table, gf_cfg.market, day)
    np.testing.assert_array_equal(a.values.to_numpy(), b.values.to_numpy())


def test_model_version_tracks_configuration() -> None:
    assert LassoForecaster(56, 1).model_version != LassoForecaster(364, 1).model_version
    assert LassoForecaster(56, 1).model_version == LassoForecaster(56, 1).model_version
    assert LassoForecaster(56, 1).model_version.startswith("gridflex-0.2.0.dev0+")
    with pytest.raises(ValueError):
        LassoForecaster(0, 1)


def test_forecast_record_validation() -> None:
    idx = pd.date_range("2024-04-01", periods=2, freq="h", tz="UTC")
    origin = utc("2024-03-31 10:00")
    kw = dict(
        delivery_day=dt.date(2024, 4, 1),
        origin_utc=origin,
        model_id="m",
        model_version="v",
        features=("x",),
    )
    ok = PriceForecast(
        values=pd.Series([1.0, 2.0], index=idx), feature_cutoff_utc=origin, **kw
    )  # type: ignore[arg-type]
    assert list(ok.to_frame().columns)[:3] == [
        "delivery_interval_utc",
        "delivery_day",
        "price_forecast_eur_per_mwh",
    ]
    with pytest.raises(ForecastError, match="no silent filling"):
        PriceForecast(
            values=pd.Series([1.0, np.nan], index=idx), feature_cutoff_utc=origin, **kw
        )  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="feature_cutoff"):
        PriceForecast(
            values=pd.Series([1.0, 2.0], index=idx),
            feature_cutoff_utc=origin + pd.Timedelta(seconds=1),
            **kw,
        )  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="UTC"):
        PriceForecast(
            values=pd.Series([1.0, 2.0], index=idx.tz_convert(TZ)),
            feature_cutoff_utc=origin,
            **kw,
        )  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Metrics                                                                     #
# --------------------------------------------------------------------------- #
def _frame(model: str, errors: list[float], day_of: list[int]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(errors), freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "delivery_interval_utc": idx,
            "model_id": model,
            "delivery_day": [dt.date(2024, 1, 1 + d) for d in day_of],
            "error_eur_per_mwh": errors,
        }
    )


def test_accuracy_hand_values() -> None:
    a = accuracy(_frame("a", [1.0, -3.0], [0, 0]), _frame("b", [2.0, 2.0], [0, 0]))
    assert (a.mae, a.rmse, a.bias) == (2.0, pytest.approx(math.sqrt(5.0)), -1.0)
    assert a.rmae == pytest.approx(1.0)  # 2 / 2
    assert (a.n_intervals, a.n_days) == (2, 1)


def test_dm_test_direction() -> None:
    days = [d for d in range(20) for _ in range(2)]
    rng = np.random.default_rng(0)
    big = _frame("a", list(rng.normal(0, 10, 40)), days)
    small = _frame("b", list(rng.normal(0, 1, 40)), days)
    res = dm_test(big, small)
    assert res["dm_stat"] > 3 and res["p_value_b_better"] < 0.01 and res["n_days"] == 20
    assert dm_test(small, big)["p_value_b_better"] > 0.99
    assert math.isnan(dm_test(small, small)["dm_stat"])  # zero differential


def test_days_in_period() -> None:
    d = days_in(DayPeriod(start_day=dt.date(2024, 2, 27), end_day=dt.date(2024, 3, 2)))
    assert d == [
        dt.date(2024, 2, 27),
        dt.date(2024, 2, 28),
        dt.date(2024, 2, 29),
        dt.date(2024, 3, 1),
    ]
