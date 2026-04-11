"""Rolling-horizon backtest: bid on a forecast, settle on reality.

The engine walks the backtest window one delivery day at a time. For each day
it optimizes the dispatch schedule against the *forecast* prices (the operator's
day-ahead bid, decided at gate closure), then values that committed schedule
against the *realised* prices. Because the power schedule and the battery
physics are deterministic, the realised SoC trajectory equals the planned one --
forecast error hits revenue, not feasibility, exactly as in the real auction.

Each day is optimized cyclically (end SoC == start SoC). This keeps every
strategy on identical footing so the headline "foresight capture ratio"
(realistic net / perfect-foresight net) is a clean apples-to-apples number.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from bess.config import BatteryConfig
from bess.forecast.models import Forecaster
from bess.optimize.dispatch import optimize_dispatch, settle


@dataclass
class BacktestResult:
    """Outcome of a rolling-horizon backtest.

    ``daily`` is indexed by delivery date with per-day economics; ``schedule``
    is the concatenated hourly dispatch valued at realised prices.
    """

    forecaster_name: str
    daily: pd.DataFrame
    schedule: pd.DataFrame
    predicted: pd.Series
    actual: pd.Series

    @property
    def net_revenue_eur(self) -> float:
        return float(self.daily["net_settled_eur"].sum())

    @property
    def planned_revenue_eur(self) -> float:
        """What the operator *expected* given the forecast (optimism gap)."""
        return float(self.daily["net_planned_eur"].sum())

    @property
    def equivalent_full_cycles(self) -> float:
        return float(self.daily["cycles"].sum())

    @property
    def n_days(self) -> int:
        return int(len(self.daily))


def _fill_forecast(fc: pd.Series) -> pd.Series:
    """Patch any residual NaNs (warmup edges) with the day's mean forecast."""
    if fc.isna().any():
        fill = fc.mean()
        if np.isnan(fill):
            fill = 0.0
        fc = fc.fillna(fill)
    return fc


def run_backtest(
    dataset: pd.DataFrame,
    cfg: BatteryConfig,
    forecaster: Forecaster,
    start: str,
    end: str,
    keep_schedule: bool = True,
) -> BacktestResult:
    """Run the rolling-horizon backtest for one forecaster over ``[start, end)``."""
    tz = dataset.index.tz
    start_ts = pd.Timestamp(start, tz=tz)
    end_ts = pd.Timestamp(end, tz=tz)

    predicted = forecaster.forecast(dataset, start_ts, end_ts)
    actual = dataset.loc[
        (dataset.index >= start_ts) & (dataset.index < end_ts), "price"
    ]

    day_keys = actual.index.normalize()
    e_start = cfg.e_init

    rows = []
    sched_parts = []
    for day, day_idx in actual.groupby(day_keys).groups.items():
        day_actual = actual.loc[day_idx]
        day_fc = _fill_forecast(predicted.reindex(day_idx))

        result = optimize_dispatch(
            day_fc.to_numpy(), cfg, e_start=e_start, e_end="cyclic",
        )
        settled = settle(result, day_actual.to_numpy(), cfg)   # valued at reality
        planned = settle(result, day_fc.to_numpy(), cfg)       # operator's expectation

        rows.append({
            "date": day,
            "net_settled_eur": settled.net_revenue_eur,
            "net_planned_eur": planned.net_revenue_eur,
            "gross_settled_eur": settled.gross_revenue_eur,
            "degradation_eur": settled.degradation_cost_eur,
            "cycles": settled.equivalent_full_cycles,
            "energy_discharged_mwh": settled.energy_discharged_mwh,
            "energy_charged_mwh": settled.energy_charged_mwh,
            "status": result.status,
        })

        if keep_schedule:
            sched_parts.append(pd.DataFrame({
                "price_actual": day_actual.to_numpy(),
                "price_forecast": day_fc.to_numpy(),
                "p_charge_mw": result.p_ch,
                "p_discharge_mw": result.p_dis,
                "soc_mwh": result.e,
            }, index=day_idx))

        # Cyclic days -> SoC returns to start; carry it forward for generality.
        e_start = float(result.e[-1]) if result.solved else e_start

    daily = pd.DataFrame(rows).set_index("date").sort_index()
    schedule = (
        pd.concat(sched_parts).sort_index()
        if sched_parts else pd.DataFrame()
    )
    return BacktestResult(
        forecaster_name=forecaster.name,
        daily=daily,
        schedule=schedule,
        predicted=predicted,
        actual=actual,
    )
