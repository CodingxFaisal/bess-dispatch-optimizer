"""Settlement of a committed schedule at a price vector.

For one horizon with AC powers ``P_ch``, ``P_dis`` (MW), durations ``dt`` (h)
and prices ``p`` (EUR/MWh)::

    purchases_eur           = sum_t p_t * P_ch_t  * dt_t   (< 0 when paid to consume)
    sales_eur               = sum_t p_t * P_dis_t * dt_t
    gross_revenue_eur       = sales_eur - purchases_eur
    degradation_cost_eur    = c_deg * throughput on the configured basis
    trading_cost_eur        = c_trade * (energy bought + energy sold)
    net_operating_revenue   = gross_revenue - degradation_cost - trading_cost

Under terminal policy ``free``, the value of the stored-energy change
``v_term * (E_end - E_start)`` is reported separately as ``terminal_value_eur``.
It is **not** part of net operating revenue, which only counts settled cash
flows and modelled costs.

The same function values a schedule at realised prices ("realised") and at the
prices it was planned on ("expected"); the backtest reports both.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from gridflex.battery.model import BatteryModel
from gridflex.battery.physics import Schedule, energy_trajectory, throughput


class SettlementError(ValueError):
    """Prices cannot settle the schedule (length mismatch, non-finite values)."""


@dataclass(frozen=True)
class Settlement:
    purchases_eur: float
    sales_eur: float
    gross_revenue_eur: float
    degradation_cost_eur: float
    trading_cost_eur: float
    net_operating_revenue_eur: float
    terminal_value_eur: float
    energy_bought_mwh: float
    energy_sold_mwh: float
    conversion_losses_mwh: float
    equivalent_full_cycles: float
    e_start_mwh: float
    e_end_mwh: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def settle(
    schedule: Schedule,
    prices_eur_per_mwh: npt.ArrayLike,
    battery: BatteryModel,
    *,
    e_start_mwh: float,
    trading_cost_eur_per_mwh: float = 0.0,
) -> Settlement:
    prices = np.asarray(prices_eur_per_mwh, dtype=np.float64)
    if prices.shape != (schedule.n_intervals,):
        raise SettlementError(
            f"{prices.shape[0] if prices.ndim else 0} prices for "
            f"{schedule.n_intervals} intervals"
        )
    if not np.all(np.isfinite(prices)):
        raise SettlementError("prices must be finite to settle (no silent filling)")
    if trading_cost_eur_per_mwh < 0:
        raise SettlementError("trading cost must be non-negative")

    dt = schedule.dt_hours
    purchases = float(np.sum(prices * schedule.charge_mw * dt))
    sales = float(np.sum(prices * schedule.discharge_mw * dt))
    flows = throughput(battery, schedule)
    trading = trading_cost_eur_per_mwh * (flows.ac_charge_mwh + flows.ac_discharge_mwh)
    gross = sales - purchases
    e_end = (
        float(energy_trajectory(battery, schedule, e_start_mwh)[-1])
        if schedule.n_intervals
        else float(e_start_mwh)
    )
    terminal = 0.0
    if battery.terminal_policy == "free":
        assert battery.terminal_energy_value_eur_per_mwh is not None
        terminal = battery.terminal_energy_value_eur_per_mwh * (e_end - e_start_mwh)
    return Settlement(
        purchases_eur=purchases,
        sales_eur=sales,
        gross_revenue_eur=gross,
        degradation_cost_eur=flows.degradation_cost_eur,
        trading_cost_eur=trading,
        net_operating_revenue_eur=gross - flows.degradation_cost_eur - trading,
        terminal_value_eur=terminal,
        energy_bought_mwh=flows.ac_charge_mwh,
        energy_sold_mwh=flows.ac_discharge_mwh,
        conversion_losses_mwh=flows.conversion_losses_mwh,
        equivalent_full_cycles=flows.efc,
        e_start_mwh=float(e_start_mwh),
        e_end_mwh=e_end,
    )
