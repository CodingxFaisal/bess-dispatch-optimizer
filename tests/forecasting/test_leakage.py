"""Leakage tests: no forecast may use information published after its origin."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from gridflex.config import GridFlexConfig
from gridflex.data.availability import available_from, decision_time
from gridflex.data.errors import InformationLeakageError
from gridflex.forecasting import (
    Forecaster,
    ForecastError,
    GbrForecaster,
    LassoForecaster,
    LearModel,
    PersistenceForecaster,
    SeasonalNaiveForecaster,
    build_feature_table,
    check_prediction_rows,
    check_training_rows,
)
from gridflex.forecasting.models import PRICE_FEATURES
from tests_helpers import synthetic_market_frame

TZ = "Europe/Berlin"
DAY = dt.date(2024, 4, 15)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return synthetic_market_frame("2024-01-01", 120)


def _local_days(index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(
        index.tz_convert(TZ).tz_localize(None).normalize(), dtype="datetime64[D]"
    )


def _corrupt_after_origin(
    frame: pd.DataFrame, cfg: GridFlexConfig, origin: pd.Timestamp
) -> pd.DataFrame:
    """Replace every value published after ``origin`` with garbage."""
    out = frame.copy()
    days = _local_days(frame.index)
    for col in out.columns:
        rule = cfg.data.series[col].availability
        late = np.array(
            [available_from(rule, d.astype(object), TZ) > origin for d in days]
        )
        out.loc[late, col] = 1e6 * (1 + np.arange(late.sum()) % 7)
    return out


MODELS = [
    PersistenceForecaster,
    SeasonalNaiveForecaster,
    lambda: LassoForecaster(56, 1),
    lambda: GbrForecaster(56, 7, max_iter=60),
]


@pytest.mark.parametrize("make", MODELS)
def test_forecast_unchanged_when_future_data_is_corrupted(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame, make
) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: corrupting everything published after the origin (prices
    of D and later, load forecasts of D+1 and later) must not change the
    forecast for D at all."""
    origin = decision_time(gf_cfg.market, DAY)
    clean = make().forecast_day(build_feature_table(frame, gf_cfg), gf_cfg.market, DAY)
    dirty_frame = _corrupt_after_origin(frame, gf_cfg, origin)
    # Corrupted values (hand count): prices for 15-29 Apr = 15 * 24 = 360,
    # load forecasts for 16-29 Apr = 14 * 24 = 336 -> 696.
    assert (dirty_frame != frame).to_numpy().sum() == 696
    dirty = make().forecast_day(
        build_feature_table(dirty_frame, gf_cfg), gf_cfg.market, DAY
    )
    np.testing.assert_array_equal(clean.values.to_numpy(), dirty.values.to_numpy())


def test_corrupting_public_data_does_change_forecasts(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    """Control for the test above: the D-1 prices *are* used."""
    origin = decision_time(gf_cfg.market, DAY)
    dirty = _corrupt_after_origin(frame, gf_cfg, origin - pd.Timedelta(days=1))
    a = PersistenceForecaster().forecast_day(
        build_feature_table(frame, gf_cfg), gf_cfg.market, DAY
    )
    b = PersistenceForecaster().forecast_day(
        build_feature_table(dirty, gf_cfg), gf_cfg.market, DAY
    )
    assert not np.array_equal(a.values.to_numpy(), b.values.to_numpy())


class _SolarForecaster(PersistenceForecaster):
    model_id = "uses_solar"

    @property
    def features(self) -> tuple[str, ...]:
        return ("price_lag_1d", "solar_forecast")


def test_feature_published_after_origin_raises(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    # PV forecast for D is published D-1 18:00, after the 12:00 decision.
    f = frame.assign(solar_forecast=1000.0)
    table = build_feature_table(f, gf_cfg, exogenous=[("solar_forecast", 0)])
    with pytest.raises(InformationLeakageError, match="solar_forecast"):
        _SolarForecaster().forecast_day(table, gf_cfg.market, DAY)
    # The same feature lagged one day (published D-2 18:00) is fine.
    table1 = build_feature_table(f, gf_cfg, exogenous=[("solar_forecast", 1)])
    rows = table1.rows_for_day(DAY)
    check_prediction_rows(table1, rows, decision_time(gf_cfg.market, DAY))


def test_same_day_price_feature_raises(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    table = build_feature_table(frame, gf_cfg, price_lags_days=(0, 1))
    rows = table.rows_for_day(DAY)
    with pytest.raises(InformationLeakageError, match="price_lag_0d"):
        check_prediction_rows(
            table.subset(["price_lag_0d"]), rows, decision_time(gf_cfg.market, DAY)
        )


def test_publication_boundary_is_exact(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    table = build_feature_table(frame, gf_cfg).subset(["load_forecast"])
    rows = table.rows_for_day(DAY)
    published = available_from(
        gf_cfg.data.series["load_forecast"].availability, DAY, TZ
    )
    check_prediction_rows(table, rows, published)  # exactly at publication: allowed
    with pytest.raises(InformationLeakageError):
        check_prediction_rows(table, rows, published - pd.Timedelta(minutes=1))


def test_training_row_on_or_after_target_day_raises(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    table = build_feature_table(frame, gf_cfg)
    origin = decision_time(gf_cfg.market, DAY)
    prev = table.rows_for_day(DAY - dt.timedelta(days=1))
    check_training_rows(table, prev, origin, DAY)  # D-1 targets: public, allowed
    with pytest.raises(InformationLeakageError, match=">= 2024-04-15"):
        check_training_rows(table, np.r_[prev, table.rows_for_day(DAY)], origin, DAY)
    with pytest.raises(InformationLeakageError, match=">= 2024-04-15"):
        check_training_rows(
            table, table.rows_for_day(DAY + dt.timedelta(days=3)), origin, DAY
        )


def test_training_target_published_after_origin_raises(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    # Pretend the decision is a day earlier: D-1 prices (published D-2 13:00)
    # are not yet public at D-2 12:00.
    table = build_feature_table(frame, gf_cfg)
    early = decision_time(gf_cfg.market, DAY) - pd.Timedelta(days=1)
    with pytest.raises(InformationLeakageError, match="targets published after"):
        check_training_rows(
            table, table.rows_for_day(DAY - dt.timedelta(days=1)), early, DAY
        )


class _CheatingLasso(LassoForecaster):
    def training_rows(self, table, origin, delivery_day):  # type: ignore[no-untyped-def]
        honest = super().training_rows(table, origin, delivery_day)
        return np.r_[honest, table.rows_for_day(delivery_day)]


def test_model_with_leaky_training_selection_is_stopped(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    with pytest.raises(InformationLeakageError):
        _CheatingLasso(56, 1).forecast_day(
            build_feature_table(frame, gf_cfg), gf_cfg.market, DAY
        )


@pytest.mark.parametrize("make", MODELS[2:])
def test_default_training_rows_are_public_and_earlier(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame, make
) -> None:  # type: ignore[no-untyped-def]
    table = build_feature_table(frame, gf_cfg)
    model: Forecaster = make()
    for day in (dt.date(2024, 3, 1), dt.date(2024, 3, 31), dt.date(2024, 4, 1), DAY):
        origin = decision_time(gf_cfg.market, day)
        sub = table.subset(model.features)
        rows = model.training_rows(sub, origin, day)  # type: ignore[attr-defined]
        assert len(rows) > 0
        check_training_rows(sub, rows, origin, day)
        fc = model.forecast_day(table, gf_cfg.market, day)
        assert fc.training_data_cutoff_utc is not None
        assert fc.training_data_cutoff_utc <= fc.origin_utc
        assert fc.feature_cutoff_utc <= fc.origin_utc


def test_lear_preprocessing_fitted_on_training_rows_only(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    table = build_feature_table(frame, gf_cfg)
    model = LassoForecaster(56, 1)
    origin = decision_time(gf_cfg.market, DAY)
    sub = table.subset(model.features)
    train = model.training_rows(sub, origin, DAY)
    X_train, y_train = sub.X.iloc[train], sub.y.iloc[train]
    lear = LearModel(PRICE_FEATURES, ("load_forecast", "load_forecast_lag_1d")).fit(
        X_train, y_train
    )
    # Scaling statistics come from the training target alone (hand formulas).
    median = float(np.median(y_train))
    assert lear.median_ == pytest.approx(median)
    assert lear.mad_ == pytest.approx(float(np.median(np.abs(y_train - median))))
    design = lear._design(X_train)
    np.testing.assert_allclose(lear.scaler_.mean_, design.mean(axis=0))
    # Predicting on extreme unseen rows must not alter the fitted parameters.
    before = (
        lear.median_,
        lear.mad_,
        lear.scaler_.mean_.copy(),
        lear.lasso_.coef_.copy(),
    )
    lear.predict(sub.X.iloc[sub.X.index.get_indexer(sub.X.index[-48:])] * 50)
    assert (lear.median_, lear.mad_) == before[:2]
    np.testing.assert_array_equal(lear.scaler_.mean_, before[2])
    np.testing.assert_array_equal(lear.lasso_.coef_, before[3])


def test_missing_input_is_an_error_not_filled(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    f = frame.copy()
    rows = _local_days(f.index) == np.datetime64(DAY, "D")
    f.iloc[np.flatnonzero(rows)[3:5], f.columns.get_loc("load_forecast")] = np.nan
    table = build_feature_table(f, gf_cfg)
    PersistenceForecaster().forecast_day(table, gf_cfg.market, DAY)  # load unused: ok
    with pytest.raises(ForecastError, match="load_forecast"):
        LassoForecaster(56, 1).forecast_day(table, gf_cfg.market, DAY)


def test_insufficient_history_is_an_error(
    gf_cfg: GridFlexConfig, frame: pd.DataFrame
) -> None:
    table = build_feature_table(frame, gf_cfg)
    with pytest.raises(ForecastError, match="training rows"):
        LassoForecaster(56, 1).forecast_day(table, gf_cfg.market, dt.date(2024, 1, 12))
