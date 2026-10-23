"""Sensitivity runner: variant construction, caching and monotone properties."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from gridflex.config import DayPeriod, GridFlexConfig
from gridflex.finance import CachedForecaster, run_sensitivity, variants
from gridflex.forecasting import PersistenceForecaster, build_feature_table
from tests_helpers import synthetic_dataset, synthetic_market_frame

PERIOD = DayPeriod(start_day=dt.date(2024, 3, 10), end_day=dt.date(2024, 3, 17))
STRATS = ["perfect_foresight", "forecast_persistence"]


@pytest.fixture(scope="module")
def table(gf_cfg: GridFlexConfig) -> pd.DataFrame:
    ds = synthetic_dataset(synthetic_market_frame("2024-03-01", 20, seed=4))
    return run_sensitivity(gf_cfg, ds, period=PERIOD, strategy_names=STRATS)


def test_variant_construction(gf_cfg: GridFlexConfig) -> None:
    v = variants(gf_cfg)
    assert len(v) == 3 + 3 + 3 + 4 + 4
    by = {(p, val): c.battery for p, val, _, c in v}
    assert by[("power_mw", 20.0)].charge_power_mw == 20.0
    assert by[("power_mw", 20.0)].energy_capacity_mwh == 20.0  # energy fixed
    assert by[("duration_h", 4.0)].energy_capacity_mwh == 40.0  # 10 MW * 4 h
    eff = by[("round_trip_efficiency", 0.92)].efficiency
    assert eff.charge * eff.discharge == pytest.approx(0.92)
    deg = by[("degradation_cost_factor", 2.0)].degradation.cost_eur_per_mwh
    assert deg == pytest.approx(2 * 120_000 / 7_200)
    assert by[("max_efc_per_day", None)].max_equivalent_full_cycles_per_day is None
    assert sum(base for _, _, base, _ in v) == 5  # one base value per parameter


def test_rows_and_base_consistency(table: pd.DataFrame) -> None:
    assert len(table) == 17 * 2 and (table.failed_days == 0).all()
    base = table[table.is_base]
    for name, grp in base.groupby("strategy"):
        assert len(grp) == 5
        assert grp.net_operating_revenue_eur.nunique() == 1, name  # same config
    pf = table[table.strategy == "perfect_foresight"]
    assert (pf.capture_ratio == 1.0).all()


def _pf(table: pd.DataFrame, parameter: str) -> pd.Series:
    rows = table[
        (table.parameter == parameter) & (table.strategy == "perfect_foresight")
    ]
    return rows.set_index("value").net_operating_revenue_eur


def test_monotone_properties_of_the_benchmark(table: pd.DataFrame) -> None:
    # Higher degradation cost lowers every schedule's value: non-increasing.
    deg = _pf(table, "degradation_cost_factor").sort_index()
    assert np.all(np.diff(deg.to_numpy()) <= 1e-6)
    # A looser cycle cap enlarges the feasible set: non-decreasing (None = no cap).
    cap = _pf(table, "max_efc_per_day")
    order = [1.0, 1.5, 2.0]
    vals = [cap[k] for k in order] + [cap[cap.index.isna()].iloc[0]]
    assert np.all(np.diff(vals) >= -1e-6)
    # More power at fixed energy enlarges the feasible set: non-decreasing.
    pw = _pf(table, "power_mw").sort_index()
    assert np.all(np.diff(pw.to_numpy()) >= -1e-6)
    # Zero degradation cost: net equals gross.
    zero = table[(table.parameter == "degradation_cost_factor") & (table.value == 0.0)]
    np.testing.assert_allclose(zero.net_operating_revenue_eur, zero.gross_revenue_eur)


def test_cached_forecaster_computes_each_day_once(gf_cfg: GridFlexConfig) -> None:
    ds = synthetic_dataset(synthetic_market_frame("2024-03-01", 20, seed=4))
    tbl = build_feature_table(ds.frame, gf_cfg)
    calls: list[dt.date] = []

    class Counting(PersistenceForecaster):
        def forecast_day(self, table, market, delivery_day):  # type: ignore[no-untyped-def]
            calls.append(delivery_day)
            return super().forecast_day(table, market, delivery_day)

    cached = CachedForecaster(Counting())
    day = dt.date(2024, 3, 12)
    a = cached.forecast_day(tbl, gf_cfg.market, day)
    b = cached.forecast_day(tbl, gf_cfg.market, day)
    assert a is b and calls == [day]
    assert cached.model_version == PersistenceForecaster().model_version


def test_unknown_strategy(gf_cfg: GridFlexConfig) -> None:
    ds = synthetic_dataset(synthetic_market_frame("2024-03-01", 20, seed=4))
    with pytest.raises(KeyError, match="nope"):
        run_sensitivity(gf_cfg, ds, period=PERIOD, strategy_names=["nope"])
