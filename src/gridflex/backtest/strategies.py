"""Backtest strategies: what each one is allowed to know, and how it plans a day.

Only :class:`PerfectForesightStrategy` receives realised prices of the
delivery day (``uses_future_information = True``). It is an upper benchmark
under identical physical and market assumptions, not a deployable strategy.
Forecast-driven and heuristic strategies receive a :class:`PriceForecast`,
which the forecasting guards guarantee was built from data public at D-1
12:00.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from gridflex.backtest.heuristic import two_window_schedule
from gridflex.battery.model import BatteryModel, FloatArray
from gridflex.battery.physics import Schedule
from gridflex.config.models import (
    ForecastingConfig,
    GridFlexConfig,
    OptimizationConfig,
    StrategySpec,
)
from gridflex.forecasting.base import ForecastError, PriceForecast
from gridflex.forecasting.features import FeatureTable
from gridflex.forecasting.models import (
    Forecaster,
    GbrForecaster,
    LassoForecaster,
    PersistenceForecaster,
    SeasonalNaiveForecaster,
)
from gridflex.optimization.problem import DispatchProblem, ProblemError
from gridflex.optimization.solve import SolveReport, solve_dispatch

StrategyKind = Literal["perfect_foresight", "forecast", "heuristic"]


@dataclass(frozen=True)
class DayContext:
    """Everything a strategy may use for delivery day D (no realised prices)."""

    delivery_day: dt.date
    index: pd.DatetimeIndex  # UTC interval starts of D
    dt_hours: FloatArray
    e_start_mwh: float
    battery: BatteryModel
    table: FeatureTable | None
    cfg: GridFlexConfig


@dataclass(frozen=True)
class DayPlan:
    schedule: Schedule | None
    plan_prices: FloatArray | None
    forecast: PriceForecast | None = None
    solve: SolveReport | None = None
    failure: str | None = None


class Strategy(ABC):
    kind: StrategyKind
    uses_future_information: bool = False

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def plan(self, ctx: DayContext, realised: FloatArray | None) -> DayPlan: ...

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "uses_future_information": self.uses_future_information,
        }


def _optimise(
    ctx: DayContext,
    prices: FloatArray,
    opts: OptimizationConfig,
    forecast: PriceForecast | None,
) -> DayPlan:
    try:
        problem = DispatchProblem(
            ctx.battery,
            prices,
            ctx.dt_hours,
            e_start_mwh=ctx.e_start_mwh,
            label=f"{ctx.delivery_day}",
            trading_cost_eur_per_mwh=ctx.cfg.economics.trading_cost_eur_per_mwh,
        )
    except ProblemError as exc:
        return DayPlan(None, prices, forecast, None, f"problem_error: {exc}")
    sol = solve_dispatch(problem, opts)
    if not sol.success:
        return DayPlan(
            None,
            prices,
            forecast,
            sol.report,
            f"solve_{sol.report.status}: {sol.report.termination}",
        )
    return DayPlan(sol.schedule, prices, forecast, sol.report)


def _forecast(model: Forecaster, ctx: DayContext) -> PriceForecast | str:
    if ctx.table is None:
        return "no feature table"
    try:
        fc = model.forecast_day(ctx.table, ctx.cfg.market, ctx.delivery_day)
    except ForecastError as exc:
        return f"forecast_error: {exc}"
    if not fc.values.index.equals(ctx.index):
        return "forecast intervals do not match the delivery day"
    return fc


class PerfectForesightStrategy(Strategy):
    kind: StrategyKind = "perfect_foresight"
    uses_future_information = True

    def plan(self, ctx: DayContext, realised: FloatArray | None) -> DayPlan:
        if realised is None:
            raise ValueError("perfect foresight needs realised prices")
        return _optimise(ctx, realised, ctx.cfg.optimization, None)


class ForecastStrategy(Strategy):
    kind: StrategyKind = "forecast"

    def __init__(self, name: str, forecaster: Forecaster) -> None:
        super().__init__(name)
        self.forecaster = forecaster

    def plan(self, ctx: DayContext, realised: FloatArray | None) -> DayPlan:
        assert realised is None, "forecast strategies never see realised prices"
        fc = _forecast(self.forecaster, ctx)
        if isinstance(fc, str):
            return DayPlan(None, None, None, None, fc)
        return _optimise(
            ctx, fc.values.to_numpy(dtype=np.float64), ctx.cfg.optimization, fc
        )

    def describe(self) -> dict[str, object]:
        return {**super().describe(), "model": self.forecaster.config()}


class HeuristicStrategy(ForecastStrategy):
    kind: StrategyKind = "heuristic"

    def plan(self, ctx: DayContext, realised: FloatArray | None) -> DayPlan:
        assert realised is None, "heuristic strategies never see realised prices"
        fc = _forecast(self.forecaster, ctx)
        if isinstance(fc, str):
            return DayPlan(None, None, None, None, fc)
        prices = fc.values.to_numpy(dtype=np.float64)
        schedule, _ = two_window_schedule(
            ctx.battery,
            prices,
            ctx.dt_hours,
            ctx.e_start_mwh,
            trading_cost_eur_per_mwh=ctx.cfg.economics.trading_cost_eur_per_mwh,
        )
        return DayPlan(schedule, prices, fc)


def make_forecaster(spec: StrategySpec, fc: ForecastingConfig) -> Forecaster:
    if spec.model == "persistence":
        return PersistenceForecaster()
    if spec.model == "seasonal_naive":
        return SeasonalNaiveForecaster()
    assert spec.window_days is not None
    if spec.model == "lasso":
        return LassoForecaster(spec.window_days, fc.lasso.recalibrate_every_days)
    if spec.model == "gbr":
        return GbrForecaster(
            spec.window_days,
            fc.gbr.recalibrate_every_days,
            max_depth=spec.max_depth or 6,
            learning_rate=fc.gbr.learning_rate,
            max_iter=fc.gbr.max_iter,
            l2_regularization=fc.gbr.l2_regularization,
            random_state=fc.random_state,
        )
    raise ValueError(f"unknown model {spec.model!r}")


def make_strategy(spec: StrategySpec, cfg: GridFlexConfig) -> Strategy:
    if spec.kind == "perfect_foresight":
        return PerfectForesightStrategy(spec.name)
    assert cfg.forecasting is not None
    forecaster = make_forecaster(spec, cfg.forecasting)
    if spec.kind == "forecast":
        return ForecastStrategy(spec.name, forecaster)
    return HeuristicStrategy(spec.name, forecaster)
