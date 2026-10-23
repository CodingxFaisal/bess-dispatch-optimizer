"""One-at-a-time sensitivity of backtest results to battery parameters.

For each parameter in ``SensitivityConfig``, each listed value replaces the
base value while everything else stays at the base configuration. Each
variant is a complete rolling backtest (same days, same policy).

* ``power_mw``: charge and discharge power; energy capacity fixed.
* ``duration_h``: energy capacity = base power * duration; power fixed.
* ``round_trip_efficiency``: split symmetrically into one-way efficiencies.
* ``degradation_cost_factor``: multiplies the replacement-cost input, and
  therefore the per-MWh degradation cost.
* ``max_efc_per_day``: daily cycle cap (``None`` = no cap).

Price forecasts do not depend on the battery, so each forecast strategy's
forecasts are computed once and reused across variants (``CachedForecaster``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import pandas as pd

from gridflex.backtest.engine import run_strategy
from gridflex.backtest.strategies import ForecastStrategy, Strategy, make_strategy
from gridflex.config.models import (
    BatteryConfig,
    DayPeriod,
    GridFlexConfig,
    MarketConfig,
    RoundTripEfficiency,
)
from gridflex.data.dataset import MarketDataset
from gridflex.finance.metrics import summarise
from gridflex.forecasting.base import PriceForecast
from gridflex.forecasting.evaluate import days_in
from gridflex.forecasting.features import FeatureTable, build_feature_table
from gridflex.forecasting.models import Forecaster


class CachedForecaster(Forecaster):
    """Memoises another forecaster's ``forecast_day`` by delivery day."""

    def __init__(self, inner: Forecaster) -> None:
        self.inner = inner
        self.model_id = inner.model_id
        self._cache: dict[dt.date, PriceForecast] = {}

    @property
    def features(self) -> tuple[str, ...]:
        return self.inner.features

    def config(self) -> dict[str, Any]:
        return self.inner.config()

    @property
    def model_version(self) -> str:
        return self.inner.model_version

    def forecast_day(
        self, table: FeatureTable, market: MarketConfig, delivery_day: dt.date
    ) -> PriceForecast:
        if delivery_day not in self._cache:
            self._cache[delivery_day] = self.inner.forecast_day(
                table, market, delivery_day
            )
        return self._cache[delivery_day]

    def _predict(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


def _battery_variant(base: BatteryConfig, parameter: str, value: Any) -> BatteryConfig:
    if parameter == "power_mw":
        upd: dict[str, Any] = {"charge_power_mw": value, "discharge_power_mw": value}
    elif parameter == "duration_h":
        upd = {"energy_capacity_mwh": base.discharge_power_mw * value}
    elif parameter == "round_trip_efficiency":
        upd = {
            "efficiency": RoundTripEfficiency(
                mode="round_trip_symmetric", round_trip=value
            )
        }
    elif parameter == "degradation_cost_factor":
        deg = base.degradation
        upd = {
            "degradation": deg.model_copy(
                update={
                    "replacement_cost_eur_per_kwh": deg.replacement_cost_eur_per_kwh
                    * value
                }
            )
        }
    elif parameter == "max_efc_per_day":
        upd = {"max_equivalent_full_cycles_per_day": value}
    else:
        raise KeyError(parameter)
    # Re-validate the variant (model_copy alone skips validation).
    return BatteryConfig.model_validate(
        {
            **base.model_dump(),
            **{
                k: (v.model_dump() if hasattr(v, "model_dump") else v)
                for k, v in upd.items()
            },
        }
    )


def variants(cfg: GridFlexConfig) -> list[tuple[str, Any, bool, GridFlexConfig]]:
    """(parameter, value, is_base, config) for every one-at-a-time variant."""
    sens = cfg.sensitivity
    if sens is None:
        raise ValueError("configuration has no sensitivity section")
    b = cfg.battery
    base_values = {
        "power_mw": b.discharge_power_mw,
        "duration_h": b.energy_capacity_mwh / b.discharge_power_mw,
        "round_trip_efficiency": b.efficiency.charge * b.efficiency.discharge,
        "degradation_cost_factor": 1.0,
        "max_efc_per_day": b.max_equivalent_full_cycles_per_day,
    }
    out = []
    for parameter, base_value in base_values.items():
        for value in getattr(sens, parameter):
            battery = _battery_variant(b, parameter, value)
            is_base = (value == base_value) if value is not None else base_value is None
            if isinstance(value, float) and isinstance(base_value, float):
                is_base = abs(value - base_value) < 1e-9
            out.append(
                (parameter, value, is_base, cfg.model_copy(update={"battery": battery}))
            )
    return out


def run_sensitivity(
    cfg: GridFlexConfig,
    dataset: MarketDataset,
    *,
    period: DayPeriod | None = None,
    strategy_names: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Run every variant for the selected strategies; one row per variant x strategy."""
    if cfg.backtest is None or cfg.sensitivity is None:
        raise ValueError("configuration needs backtest and sensitivity sections")
    per = period or cfg.sensitivity.period or cfg.backtest.period
    names = strategy_names or cfg.sensitivity.strategies
    specs = {s.name: s for s in cfg.backtest.strategies}
    unknown = set(names) - set(specs)
    if unknown:
        raise KeyError(f"unknown strategies {sorted(unknown)}")
    days = days_in(per)
    table = None
    strategies: dict[str, Strategy] = {}
    for name in names:
        strat = make_strategy(specs[name], cfg)
        if isinstance(strat, ForecastStrategy):
            strat.forecaster = CachedForecaster(strat.forecaster)
            if table is None:
                assert cfg.forecasting is not None
                fc = cfg.forecasting
                table = build_feature_table(
                    dataset.frame,
                    cfg,
                    price_lags_days=fc.price_lags_days,
                    exogenous=[(k, lag) for k in fc.exogenous_series for lag in (0, 1)],
                )
        strategies[name] = strat

    rows = []
    for parameter, value, is_base, vcfg in variants(cfg):
        results = {}
        for name, strat in strategies.items():
            if progress:
                progress(f"{parameter}={value} {name}")
            results[name] = run_strategy(
                strat,
                vcfg,
                dataset,
                days,
                table=table,
                on_failure=cfg.backtest.on_failure,
            )
        pf = next((r for r in results.values() if r.kind == "perfect_foresight"), None)
        vb = vcfg.battery
        for name, res in results.items():
            s = summarise(
                res.daily, vcfg.battery, benchmark=pf.daily if pf is not None else None
            )
            rows.append(
                {
                    "parameter": parameter,
                    "value": value,
                    "is_base": is_base,
                    "strategy": name,
                    "power_mw": vcfg.battery.discharge_power_mw,
                    "energy_mwh": vcfg.battery.energy_capacity_mwh,
                    "round_trip_efficiency": (
                        vcfg.battery.efficiency.charge
                        * vcfg.battery.efficiency.discharge
                    ),
                    "degradation_eur_per_mwh": vb.degradation.cost_eur_per_mwh,
                    "max_efc_per_day": vb.max_equivalent_full_cycles_per_day,
                    **{
                        k: v
                        for k, v in s.as_dict().items()
                        if k
                        in (
                            "days",
                            "failed_days",
                            "net_operating_revenue_eur",
                            "gross_revenue_eur",
                            "degradation_cost_eur",
                            "equivalent_full_cycles",
                            "efc_per_day",
                            "net_per_mw_year_eur",
                            "net_per_mwh_year_eur",
                            "capture_ratio",
                            "partial_year",
                        )
                    },
                }
            )
    return pd.DataFrame(rows)
