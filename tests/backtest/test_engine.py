"""Backtest engine: information flow, failure handling, state carry, settlement."""

import datetime as dt
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from gridflex.backtest import (
    BacktestAbortedError,
    DayContext,
    DayPlan,
    ForecastStrategy,
    PerfectForesightStrategy,
    Strategy,
    run_backtest,
    run_strategy,
    summary_table,
)
from gridflex.battery import BatteryModel, Schedule
from gridflex.config import (
    ConfigError,
    DayPeriod,
    GridFlexConfig,
    StrategySpec,
    load_config,
)
from gridflex.forecasting import LassoForecaster, PersistenceForecaster
from gridflex.optimization import DispatchProblem, solve_dispatch
from tests_helpers import DEFAULT_CONFIG, synthetic_dataset, synthetic_market_frame

DAYS = [dt.date(2024, 3, 29) + dt.timedelta(days=i) for i in range(4)]  # incl. 23 h day


@pytest.fixture(scope="module")
def dataset():  # type: ignore[no-untyped-def]
    return synthetic_dataset(synthetic_market_frame("2024-03-01", 40, seed=11))


def _cfg(gf_cfg: GridFlexConfig, **battery: Any) -> GridFlexConfig:
    if not battery:
        return gf_cfg
    return gf_cfg.model_copy(
        update={"battery": gf_cfg.battery.model_copy(update=battery)}
    )


class _Spy(Strategy):
    """Records what it is given; plans nothing (idle)."""

    kind = "forecast"

    def __init__(self) -> None:
        super().__init__("spy")
        self.seen: list[Any] = []

    def plan(self, ctx: DayContext, realised: Any) -> DayPlan:
        self.seen.append(realised)
        n = len(ctx.index)
        return DayPlan(Schedule(np.zeros(n), np.zeros(n), ctx.dt_hours), np.zeros(n))


def _table(gf_cfg, dataset):  # type: ignore[no-untyped-def]
    from gridflex.forecasting import build_feature_table

    return build_feature_table(dataset.frame, gf_cfg)


def test_non_benchmark_strategies_never_see_realised_prices(gf_cfg, dataset) -> None:  # type: ignore[no-untyped-def]
    spy = _Spy()
    run_strategy(spy, gf_cfg, dataset, DAYS)
    assert spy.seen == [None] * len(DAYS)


def test_perfect_foresight_matches_direct_optimisation(gf_cfg, dataset) -> None:  # type: ignore[no-untyped-def]
    res = run_strategy(PerfectForesightStrategy("pf"), gf_cfg, dataset, DAYS)
    b = BatteryModel.from_config(gf_cfg.battery)
    price = dataset.frame["price"]
    for row in res.daily.itertuples():
        day_prices = res.intervals[res.intervals.delivery_day == row.delivery_day]
        direct = solve_dispatch(
            DispatchProblem(
                b,
                day_prices.price_realised_eur_per_mwh.to_numpy(),
                1.0,
                e_start_mwh=row.e_start_mwh,
            )
        )
        assert direct.objective_eur == pytest.approx(
            row.net_operating_revenue_eur, abs=1e-6
        )
        assert row.expected_net_eur == pytest.approx(row.net_operating_revenue_eur)
    assert res.daily.n_intervals.tolist() == [24, 24, 23, 24]  # 31 Mar 2024: 23 h
    assert len(res.intervals) == 95
    assert price.index.isin(pd.DatetimeIndex(res.intervals.interval_utc)).sum() == 95


def test_settlement_reconciles_with_interval_export(gf_cfg, dataset) -> None:  # type: ignore[no-untyped-def]
    res = run_strategy(
        ForecastStrategy("fp", PersistenceForecaster()),
        gf_cfg,
        dataset,
        DAYS,
        table=_table(gf_cfg, dataset),
    )
    iv = res.intervals
    gross = (
        (iv.price_realised_eur_per_mwh * (iv.discharge_mw - iv.charge_mw))
        .groupby(iv.delivery_day)
        .sum()
    )  # dt = 1 h
    np.testing.assert_allclose(
        gross.to_numpy(), res.daily.gross_revenue_eur.to_numpy(), atol=1e-9
    )
    expected = (iv.price_plan_eur_per_mwh * (iv.discharge_mw - iv.charge_mw)).groupby(
        iv.delivery_day
    ).sum().to_numpy() - res.daily.degradation_cost_eur.to_numpy()
    np.testing.assert_allclose(
        expected, res.daily.expected_net_eur.to_numpy(), atol=1e-9
    )


def test_failed_forecast_day_is_idle_and_flagged(gf_cfg, dataset) -> None:  # type: ignore[no-untyped-def]
    # Data start 1 Mar; complete feature rows (7-day lag) from 8 Mar; Lasso needs
    # 14 * 24 = 336 training rows -> first forecastable day is 22 Mar (hand).
    days = [dt.date(2024, 3, 2) + dt.timedelta(days=i) for i in range(25)]
    res = run_strategy(
        ForecastStrategy("lasso", LassoForecaster(28, 7)),
        gf_cfg,
        dataset,
        days,
        table=_table(gf_cfg, dataset),
    )
    failed = res.daily[res.daily.status == "failed"]
    ok = res.daily[res.daily.status == "ok"]
    assert failed.delivery_day.max() == dt.date(2024, 3, 21)
    assert ok.delivery_day.min() == dt.date(2024, 3, 22) and len(ok) == 5
    assert failed.reason.str.contains("forecast_error").all()
    assert (failed.net_operating_revenue_eur == 0).all()
    assert (
        failed.energy_bought_mwh == 0
    ).all() and failed.expected_net_eur.isna().all()
    assert (failed.e_end_mwh == failed.e_start_mwh).all()
    with pytest.raises(BacktestAbortedError, match="forecast_error"):
        run_strategy(
            ForecastStrategy("lasso", LassoForecaster(28, 7)),
            gf_cfg,
            dataset,
            days,
            table=_table(gf_cfg, dataset),
            on_failure="abort",
        )


class _Infeasible(_Spy):
    def plan(self, ctx: DayContext, realised: Any) -> DayPlan:
        n = len(ctx.index)
        charge = np.zeros(n)
        charge[0] = 50.0  # far above the 10 MW limit
        return DayPlan(Schedule(charge, np.zeros(n), ctx.dt_hours), np.zeros(n))


def test_physically_invalid_schedule_is_rejected_not_settled(gf_cfg, dataset) -> None:  # type: ignore[no-untyped-def]
    res = run_strategy(_Infeasible(), gf_cfg, dataset, DAYS[:2])
    assert (res.daily.status == "rejected").all()
    assert res.daily.reason.str.contains("CHARGE_POWER_LIMIT").all()
    assert (res.daily.energy_bought_mwh == 0).all()
    assert (res.intervals.charge_mw == 0).all()


class _ChargeOneHour(_Spy):
    def plan(self, ctx: DayContext, realised: Any) -> DayPlan:
        n = len(ctx.index)
        charge = np.zeros(n)
        charge[3] = 1.0
        return DayPlan(Schedule(charge, np.zeros(n), ctx.dt_hours), np.zeros(n))


def test_physical_state_is_carried_between_days(gf_cfg, dataset) -> None:  # type: ignore[no-untyped-def]
    cfg = _cfg(
        gf_cfg, terminal_soc_policy="free", terminal_energy_value_eur_per_mwh=0.0
    )
    res = run_strategy(_ChargeOneHour(), cfg, dataset, DAYS)
    eta = cfg.battery.efficiency.charge
    # 10 MWh start; each day stores 1 MWh AC * 0.92736 (hand: sqrt(0.86)).
    assert res.daily.e_start_mwh.tolist() == pytest.approx(
        [10 + k * eta for k in range(4)]
    )
    assert (
        res.daily.e_end_mwh.iloc[:-1].tolist()
        == res.daily.e_start_mwh.iloc[1:].tolist()
    )


def test_run_backtest_is_deterministic_with_metadata(gf_cfg, dataset) -> None:  # type: ignore[no-untyped-def]
    bt = gf_cfg.backtest
    assert bt is not None
    cfg = gf_cfg.model_copy(
        update={
            "backtest": bt.model_copy(
                update={
                    "period": DayPeriod(
                        start_day=DAYS[0], end_day=DAYS[-1] + dt.timedelta(days=1)
                    ),
                    "strategies": [s for s in bt.strategies if s.model != "lasso"],
                }
            )
        }
    )
    a, meta = run_backtest(cfg, dataset)
    b, _ = run_backtest(cfg, dataset)
    for name in a:  # everything but wall-clock timing must be identical
        pd.testing.assert_frame_equal(
            a[name].daily.drop(columns="solve_runtime_s"),
            b[name].daily.drop(columns="solve_runtime_s"),
        )
        pd.testing.assert_frame_equal(a[name].intervals, b[name].intervals)
    assert set(a) == {
        "perfect_foresight",
        "forecast_gbr",
        "forecast_persistence",
        "heuristic_persistence",
    }
    assert meta["config_fingerprint"] and "commit" in meta["git"]
    assert meta["policy"]["commitment"] == "full delivery day, fixed physical schedule"
    assert meta["packages"]["highspy"] is not None
    table = summary_table(a)
    assert (
        table.set_index("strategy").loc[
            "perfect_foresight", "share_of_perfect_foresight_net"
        ]
        == 1.0
    )
    pf = a["perfect_foresight"].daily.net_operating_revenue_eur.to_numpy()
    for name in a:  # PF is an upper bound day by day (same start energy, cyclic)
        assert (a[name].daily.net_operating_revenue_eur.to_numpy() <= pf + 1e-6).all()


def test_unknown_strategy_name(gf_cfg, dataset) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(KeyError, match="nope"):
        run_backtest(gf_cfg, dataset, strategy_names=["nope"])


@pytest.mark.parametrize(
    "spec",
    [
        {"name": "x", "kind": "perfect_foresight", "model": "gbr"},
        {"name": "x", "kind": "forecast"},
        {"name": "x", "kind": "forecast", "model": "lasso"},
        {"name": "x", "kind": "forecast", "model": "persistence", "window_days": 30},
        {
            "name": "x",
            "kind": "forecast",
            "model": "lasso",
            "window_days": 60,
            "max_depth": 3,
        },
        {"name": "Bad Name", "kind": "perfect_foresight"},
    ],
)
def test_invalid_strategy_specs(spec: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        StrategySpec.model_validate(spec)


def test_heuristic_requires_cyclic_policy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import yaml

    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["battery"].update(
        terminal_soc_policy="free", terminal_energy_value_eur_per_mwh=1.0
    )
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="heuristic"):
        load_config(path)
    raw["backtest"]["strategies"] = [{"name": "pf", "kind": "perfect_foresight"}]
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_config(path).backtest is not None


def test_battery_model_and_engine_agree_on_derating(gf_cfg, dataset) -> None:  # type: ignore[no-untyped-def]
    cfg = _cfg(gf_cfg, availability_fraction=0.5)
    res = run_strategy(PerfectForesightStrategy("pf"), cfg, dataset, DAYS[:2])
    assert res.intervals.charge_mw.max() <= 5.0 + 1e-9
    assert replace(BatteryModel.from_config(cfg.battery)).availability_fraction == 0.5
