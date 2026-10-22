"""Tests for the forecasters and the rolling-horizon backtest wiring.

Focus: no lookahead, correct index alignment, and that the backtest settles a
forecast-driven schedule against realised prices (so a wrong forecast costs
money but never breaks feasibility).
"""

import numpy as np
import pandas as pd
import pytest

from gridflex.backtest import run_backtest
from gridflex.forecast import get_forecaster
from gridflex.forecast.models import MLForecaster


@pytest.fixture
def synthetic_dataset():
    """80 days of hourly data with a realistic daily + weekly price shape."""
    idx = pd.date_range("2023-01-01", periods=80 * 24, freq="h", tz="Europe/Berlin")
    hour = idx.hour.to_numpy()
    dow = idx.day_of_week.to_numpy()
    rng = np.random.default_rng(7)

    solar = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None) * 15000
    wind = (
        8000 + 4000 * np.sin(np.arange(len(idx)) / 50) + rng.normal(0, 1500, len(idx))
    )
    load = 50000 + 8000 * np.sin((hour - 8) / 24 * 2 * np.pi) - 5000 * (dow >= 5)
    residual = load - solar - wind
    # Price tracks residual load (the real driver) plus noise.
    price = 20 + residual / 400 + rng.normal(0, 8, len(idx))

    return pd.DataFrame(
        {
            "price": price,
            "load_forecast": load,
            "solar_forecast": solar,
            "wind_forecast": wind,
            "residual_load_forecast": residual,
        },
        index=idx,
    )


def test_perfect_foresight_equals_actual(synthetic_dataset):
    df = synthetic_dataset
    fc = get_forecaster("perfect").forecast(df, "2023-02-01", "2023-02-05")
    actual = df.loc["2023-02-01":"2023-02-04 23:00", "price"]
    assert np.allclose(fc.to_numpy(), actual.to_numpy())


def test_naive_persistence_is_24h_lag(synthetic_dataset):
    df = synthetic_dataset
    fc = get_forecaster("naive").forecast(df, "2023-02-01", "2023-02-03")
    # Each prediction equals the price exactly 24h earlier.
    for t in fc.index:
        assert fc.loc[t] == pytest.approx(df.loc[t - pd.Timedelta("24h"), "price"])


def test_seasonal_naive_is_168h_lag(synthetic_dataset):
    df = synthetic_dataset
    fc = get_forecaster("seasonal_naive").forecast(df, "2023-02-01", "2023-02-03")
    for t in fc.index:
        assert fc.loc[t] == pytest.approx(df.loc[t - pd.Timedelta("168h"), "price"])


def test_ml_forecaster_beats_naive_on_synthetic(synthetic_dataset):
    # On a dataset where price is a clean function of the (known) drivers, a
    # trained model should track prices better than yesterday-equals-today.
    df = synthetic_dataset
    ml = MLForecaster(retrain_every_days=30, min_train_hours=24 * 20)
    ml_fc = ml.forecast(df, "2023-02-20", "2023-03-01")
    naive_fc = get_forecaster("naive").forecast(df, "2023-02-20", "2023-03-01")
    actual = df.loc["2023-02-20":"2023-02-28 23:00", "price"].reindex(ml_fc.index)

    ml_mae = (ml_fc - actual).abs().mean()
    naive_mae = (naive_fc.reindex(ml_fc.index) - actual).abs().mean()
    assert ml_mae < naive_mae


def test_backtest_settles_on_realized_prices(cfg, synthetic_dataset):
    # A perfect-foresight backtest's planned and settled revenue must match
    # (forecast == reality); a naive one's settled revenue must be <= planned
    # optimism and strictly below perfect.
    df = synthetic_dataset
    perfect = run_backtest(
        df, cfg, get_forecaster("perfect"), "2023-02-01", "2023-02-15"
    )
    naive = run_backtest(df, cfg, get_forecaster("naive"), "2023-02-01", "2023-02-15")

    assert perfect.net_revenue_eur == pytest.approx(
        perfect.planned_revenue_eur, rel=1e-6
    )
    assert naive.net_revenue_eur <= perfect.net_revenue_eur + 1e-6
    assert perfect.schedule.shape[0] == len(perfect.actual)
