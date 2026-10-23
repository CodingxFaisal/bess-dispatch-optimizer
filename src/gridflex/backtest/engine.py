"""Rolling day-ahead backtest engine.

Policy (docs/BACKTEST_METHODOLOGY.md §2), for each local delivery day D:

1. **Decide** at D-1 12:00 Europe/Berlin: the strategy plans a full-day
   schedule from what it may know (a forecast; realised prices only for the
   perfect-foresight benchmark), starting from the carried energy ``E_start``.
2. **Commit** the whole day's schedule. If planning failed (forecast error,
   solver failure), the committed schedule is *idle* and the day is flagged
   ``failed``; with ``on_failure: abort`` the run stops instead. A failed
   plan is never settled as an optimised result.
3. **Verify** the committed schedule with the independent physics checker.
   A schedule that fails is replaced by idle and the day is flagged
   ``rejected``.
4. **Settle** at realised day-ahead prices ("realised") and at the plan
   prices ("expected").
5. **Carry** the physically simulated end energy (state equation applied to
   the committed schedule) to D+1 (audit D23).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from gridflex.backtest.strategies import DayContext, DayPlan, Strategy, make_strategy
from gridflex.battery.feasibility import check_schedule
from gridflex.battery.model import BatteryModel
from gridflex.battery.physics import (
    Schedule,
    energy_trajectory,
    interval_hours_from_index,
)
from gridflex.config.models import DayPeriod, GridFlexConfig
from gridflex.data.availability import decision_time
from gridflex.data.dataset import MarketDataset
from gridflex.data.timeutils import local_window_utc
from gridflex.forecasting.evaluate import days_in
from gridflex.forecasting.features import FeatureTable, build_feature_table
from gridflex.reporting.metadata import run_metadata
from gridflex.settlement.settle import Settlement, settle

logger = logging.getLogger(__name__)


class BacktestAbortedError(RuntimeError):
    """Raised when a day fails and ``on_failure`` is ``abort``."""


@dataclass
class BacktestResult:
    strategy: str
    kind: str
    description: dict[str, Any]
    daily: pd.DataFrame
    intervals: pd.DataFrame
    runtime_s: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    def totals(self) -> dict[str, Any]:
        d = self.daily
        return {
            "strategy": self.strategy,
            "kind": self.kind,
            "days": len(d),
            "failed_days": int((d["status"] != "ok").sum()),
            "expected_net_eur": float(d["expected_net_eur"].sum()),
            "gross_revenue_eur": float(d["gross_revenue_eur"].sum()),
            "degradation_cost_eur": float(d["degradation_cost_eur"].sum()),
            "trading_cost_eur": float(d["trading_cost_eur"].sum()),
            "net_operating_revenue_eur": float(d["net_operating_revenue_eur"].sum()),
            "energy_bought_mwh": float(d["energy_bought_mwh"].sum()),
            "energy_sold_mwh": float(d["energy_sold_mwh"].sum()),
            "equivalent_full_cycles": float(d["equivalent_full_cycles"].sum()),
            "runtime_s": self.runtime_s,
        }


def _settle_rows(prefix: str, s: Settlement) -> dict[str, float]:
    keep = (
        "purchases_eur",
        "sales_eur",
        "gross_revenue_eur",
        "degradation_cost_eur",
        "trading_cost_eur",
        "net_operating_revenue_eur",
        "terminal_value_eur",
        "energy_bought_mwh",
        "energy_sold_mwh",
        "conversion_losses_mwh",
        "equivalent_full_cycles",
    )
    return {f"{prefix}{k}": float(getattr(s, k)) for k in keep}


def run_strategy(
    strategy: Strategy,
    cfg: GridFlexConfig,
    dataset: MarketDataset,
    days: list[dt.date],
    *,
    table: FeatureTable | None = None,
    on_failure: str = "idle",
) -> BacktestResult:
    """Run one strategy over ``days`` (consecutive local delivery days)."""
    t0 = time.perf_counter()
    battery = BatteryModel.from_config(cfg.battery)
    tz = cfg.market.timezone
    price = dataset.frame["price"]
    fee = cfg.economics.trading_cost_eur_per_mwh
    opts = cfg.optimization
    e_start = battery.initial_energy_mwh
    rows: list[dict[str, Any]] = []
    parts: list[pd.DataFrame] = []

    for day in days:
        start, end = local_window_utc(day, day + dt.timedelta(days=1), tz)
        idx = price.index[(price.index >= start) & (price.index < end)]
        realised = price.loc[idx].to_numpy(dtype=np.float64)
        if len(idx) == 0 or not np.all(np.isfinite(realised)):
            raise BacktestAbortedError(f"realised prices incomplete for {day}")
        dt_h = interval_hours_from_index(pd.DatetimeIndex(idx), end)
        ctx = DayContext(day, idx, dt_h, e_start, battery, table, cfg)
        plan: DayPlan = strategy.plan(
            ctx, realised if strategy.uses_future_information else None
        )

        status, reason = "ok", ""
        committed = plan.schedule
        if committed is None:
            status, reason = "failed", plan.failure or "no schedule"
        else:
            check = check_schedule(
                battery,
                committed,
                e_start_mwh=e_start,
                tol_mw=opts.feasibility_tol_mw,
                tol_mwh=opts.feasibility_tol_mwh,
            )
            if not check.ok:
                status, reason = "rejected", check.summary()
                committed = None
        if committed is None:
            if on_failure == "abort":
                raise BacktestAbortedError(f"{strategy.name} {day}: {reason}")
            logger.warning(
                "event=day_failed strategy=%s day=%s reason=%r",
                strategy.name,
                day,
                reason,
            )
            committed = Schedule(np.zeros(len(idx)), np.zeros(len(idx)), dt_h)

        realised_s = settle(
            committed,
            realised,
            battery,
            e_start_mwh=e_start,
            trading_cost_eur_per_mwh=fee,
        )
        expected_net = float("nan")
        if status == "ok" and plan.plan_prices is not None:
            expected_net = settle(
                committed,
                plan.plan_prices,
                battery,
                e_start_mwh=e_start,
                trading_cost_eur_per_mwh=fee,
            ).net_operating_revenue_eur
        energy = energy_trajectory(battery, committed, e_start)
        fc, rep = plan.forecast, plan.solve
        rows.append(
            {
                "delivery_day": day,
                "strategy": strategy.name,
                "status": status,
                "reason": reason,
                "decision_utc": decision_time(cfg.market, day),
                "n_intervals": len(idx),
                "horizon_h": float(dt_h.sum()),
                "e_start_mwh": e_start,
                "e_end_mwh": float(energy[-1]),
                "expected_net_eur": expected_net,
                **{k: v for k, v in _settle_rows("", realised_s).items()},
                "solve_status": rep.status if rep else None,
                "mip_gap": rep.mip_gap if rep else None,
                "solve_runtime_s": rep.runtime_s if rep else None,
                "forecast_model_version": fc.model_version if fc else None,
                "forecast_feature_cutoff_utc": fc.feature_cutoff_utc if fc else None,
                "forecast_training_cutoff_utc": fc.training_data_cutoff_utc
                if fc
                else None,
                "forecast_mae_eur_per_mwh": (
                    float(np.mean(np.abs(plan.plan_prices - realised)))
                    if plan.plan_prices is not None and fc is not None
                    else None
                ),
            }
        )
        parts.append(
            pd.DataFrame(
                {
                    "interval_utc": idx,
                    "interval_local": pd.DatetimeIndex(idx)
                    .tz_convert(tz)
                    .strftime("%Y-%m-%d %H:%M%z"),
                    "delivery_day": day,
                    "strategy": strategy.name,
                    "price_realised_eur_per_mwh": realised,
                    "price_plan_eur_per_mwh": (
                        plan.plan_prices
                        if plan.plan_prices is not None
                        else np.full(len(idx), np.nan)
                    ),
                    "charge_mw": committed.charge_mw,
                    "discharge_mw": committed.discharge_mw,
                    "energy_end_mwh": energy,
                }
            )
        )
        e_start = float(energy[-1])  # physically simulated carry (audit D23)

    return BacktestResult(
        strategy=strategy.name,
        kind=strategy.kind,
        description=strategy.describe(),
        daily=pd.DataFrame(rows),
        intervals=pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(),
        runtime_s=time.perf_counter() - t0,
    )


def run_backtest(
    cfg: GridFlexConfig,
    dataset: MarketDataset,
    *,
    period: DayPeriod | None = None,
    strategy_names: list[str] | None = None,
) -> tuple[dict[str, BacktestResult], dict[str, Any]]:
    """Run the configured strategies; return results and run metadata."""
    if cfg.backtest is None:
        raise ValueError("configuration has no backtest section")
    bt = cfg.backtest
    per = period or bt.period
    specs = [
        s for s in bt.strategies if strategy_names is None or s.name in strategy_names
    ]
    if strategy_names is not None:
        missing = set(strategy_names) - {s.name for s in specs}
        if missing:
            raise KeyError(f"unknown strategies {sorted(missing)}")
    days = days_in(per)
    table = None
    if any(s.kind != "perfect_foresight" for s in specs):
        assert cfg.forecasting is not None
        fc = cfg.forecasting
        exog = [(k, lag) for k in fc.exogenous_series for lag in (0, 1)]
        table = build_feature_table(
            dataset.frame, cfg, price_lags_days=fc.price_lags_days, exogenous=exog
        )
    results = {}
    for spec in specs:
        results[spec.name] = run_strategy(
            make_strategy(spec, cfg),
            cfg,
            dataset,
            days,
            table=table,
            on_failure=bt.on_failure,
        )
    meta = run_metadata(
        cfg,
        backtest_period=per.model_dump(mode="json"),
        strategies=[r.description for r in results.values()],
        policy={
            "market": "DE-LU day-ahead (SDAC), price taker",
            "decision": "D-1 at gate closure, Europe/Berlin",
            "commitment": "full delivery day, fixed physical schedule",
            "re_optimisation": "none (day-ahead only)",
            "state_carry": "state-equation energy of the committed schedule",
            "on_failure": bt.on_failure,
        },
        data_provenance=dataset.provenance(),
    )
    return results, meta


def summary_table(results: dict[str, BacktestResult]) -> pd.DataFrame:
    """Totals per strategy and net relative to perfect foresight (if present)."""
    table = pd.DataFrame([r.totals() for r in results.values()])
    pf = [r for r in results.values() if r.kind == "perfect_foresight"]
    if pf:
        pf_net = pf[0].totals()["net_operating_revenue_eur"]
        table["share_of_perfect_foresight_net"] = (
            table["net_operating_revenue_eur"] / pf_net if pf_net else np.nan
        )
    return table
