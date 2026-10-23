"""Two-window heuristic: one full cycle per day, no optimiser.

Given a price forecast for the day, the rule considers two orders:

* **charge-then-discharge**: store ``Q`` MWh, then return it;
* **discharge-then-recharge**: release ``Q`` MWh, then restore it.

``Q`` (energy in storage terms) is the largest amount that fits the SoC window
from the start energy and the horizon's cycle limit. Each leg runs at constant
power over the shortest block of consecutive intervals that can move its
energy at rated (availability-derated) power. Among all non-overlapping
ordered block pairs, the one with the highest *forecast* value (revenue minus
degradation and trading cost) is chosen. If no pair has positive forecast
value, the battery idles.

The schedule ends at the start energy (cyclic by construction), never
charges and discharges in the same interval, and stays inside the SoC and
power limits. The engine still verifies every schedule independently.
"""

from __future__ import annotations

import math

import numpy as np

from gridflex.battery.model import BatteryModel, FloatArray
from gridflex.battery.physics import Schedule

_EPS_MWH = 1e-9


def _blocks(
    dt: FloatArray, energy_mwh: float, power_mw: float
) -> list[tuple[int, int]]:
    """Shortest [start, end) blocks per start index able to move ``energy_mwh``."""
    need_h = energy_mwh / power_mw
    out: list[tuple[int, int]] = []
    n = len(dt)
    cum = np.concatenate([[0.0], np.cumsum(dt)])
    for i in range(n):
        j = int(np.searchsorted(cum, cum[i] + need_h - 1e-12, side="left"))
        if j <= n:
            out.append((i, max(j, i + 1)))
    return out


def two_window_schedule(
    battery: BatteryModel,
    prices_eur_per_mwh: FloatArray,
    dt_hours: FloatArray,
    e_start_mwh: float,
    *,
    trading_cost_eur_per_mwh: float = 0.0,
) -> tuple[Schedule, float]:
    """Return the heuristic schedule and its forecast value (EUR)."""
    n = len(prices_eur_per_mwh)
    pc = battery.charge_power_mw * battery.availability_fraction
    pd_ = battery.discharge_power_mw * battery.availability_fraction
    ec, ed = battery.eta_charge, battery.eta_discharge
    idle = Schedule(np.zeros(n), np.zeros(n), dt_hours)

    horizon = float(np.sum(dt_hours))
    limit = battery.cycle_limit_efc(horizon)
    if limit is None:
        budget_dc = math.inf
    elif battery.efc_basis == "dc_discharge_per_usable":
        budget_dc = limit * battery.efc_reference_mwh
    else:  # AC discharge per nameplate: Q_dc * eta_dis <= limit * nameplate
        budget_dc = limit * battery.efc_reference_mwh / ed

    pdt = prices_eur_per_mwh * dt_hours
    cum_pdt = np.concatenate([[0.0], np.cumsum(pdt)])
    cum_dt = np.concatenate([[0.0], np.cumsum(dt_hours)])
    best_value, best = 0.0, None

    orders = (
        ("charge_first", min(battery.soc_max_mwh - e_start_mwh, budget_dc)),
        ("discharge_first", min(e_start_mwh - battery.soc_min_mwh, budget_dc)),
    )
    for order, q_dc in orders:
        if q_dc <= _EPS_MWH:
            continue
        x_ac, y_ac = q_dc / ec, q_dc * ed  # AC bought / AC sold
        deg_mwh = q_dc if battery.degradation_basis == "dc_discharge" else y_ac
        fixed_cost = (
            battery.degradation_cost_eur_per_mwh * deg_mwh
            + trading_cost_eur_per_mwh * (x_ac + y_ac)
        )
        ch_blocks = _blocks(dt_hours, x_ac, pc)
        dis_blocks = _blocks(dt_hours, y_ac, pd_)
        first, second = (
            (ch_blocks, dis_blocks)
            if order == "charge_first"
            else (dis_blocks, ch_blocks)
        )
        for a0, a1 in first:
            for b0, b1 in second:
                if b0 < a1:
                    continue
                ch = (a0, a1) if order == "charge_first" else (b0, b1)
                dis = (b0, b1) if order == "charge_first" else (a0, a1)
                # Constant power over a block: P = E / T, revenue = P * sum(p dt).
                buy = (
                    x_ac
                    / (cum_dt[ch[1]] - cum_dt[ch[0]])
                    * (cum_pdt[ch[1]] - cum_pdt[ch[0]])
                )
                sell = (
                    y_ac
                    / (cum_dt[dis[1]] - cum_dt[dis[0]])
                    * (cum_pdt[dis[1]] - cum_pdt[dis[0]])
                )
                value = sell - buy - fixed_cost
                if value > best_value + 1e-9:
                    best_value, best = value, (ch, dis, x_ac, y_ac)

    if best is None:
        return idle, 0.0
    (c0, c1), (d0, d1), x_ac, y_ac = best
    charge = np.zeros(n)
    discharge = np.zeros(n)
    charge[c0:c1] = x_ac / (cum_dt[c1] - cum_dt[c0])
    discharge[d0:d1] = y_ac / (cum_dt[d1] - cum_dt[d0])
    return Schedule(charge, discharge, dt_hours), best_value
