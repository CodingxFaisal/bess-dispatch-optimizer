"""The dispatch MILP: maximise arbitrage revenue over a price horizon.

Decision variables per timestep t (Delta_t hours long):
    p_ch[t]   charge power (MW), >= 0
    p_dis[t]  discharge power (MW), >= 0
    e[t]      state of charge after step t (MWh)
    u[t]      binary, 1 = charging window, 0 = discharging window

Objective (maximise):
    sum_t  price[t] * (p_dis[t] - p_ch[t]) * dt   - c_deg * p_dis[t] * dt

Constraints:
    e[t] = e[t-1] + eta_ch*p_ch[t]*dt - p_dis[t]*dt/eta_dis   (SoC dynamics)
    E_min <= e[t] <= E_max                                     (SoC bounds)
    0 <= p_ch[t]  <= P_max * u[t]                              (charge limit)
    0 <= p_dis[t] <= P_max * (1 - u[t])                        (discharge limit)
    sum_t p_dis[t]*dt <= cycle_limit                           (throughput cap)
    e[last] = e_end                                            (terminal SoC)

WHY THE BINARY (u) IS NOT OPTIONAL
----------------------------------
Textbooks often drop u and argue efficiency losses make simultaneous
charge+discharge unprofitable, so an LP "self-enforces" it. That argument
fails the moment price[t] < 0 -- and Germany now has hundreds of negative-price
hours a year. With a negative price the solver is *paid to consume*, so it will
happily run a charge/discharge loop that burns energy through the round-trip
losses purely to increase net consumption. Physically absurd, numerically
optimal. The binary forbids it. Set ``allow_binary=False`` to reproduce the
pathology -- see ``tests/`` and the README.

WHY THE TERMINAL CONSTRAINT MATTERS
-----------------------------------
Stored energy has no value at the end of a finite horizon, so an unconstrained
model drains the battery at the last hour, inflating revenue by selling the
initial charge for free. ``e_end="cyclic"`` pins the end SoC to the start SoC
(no free energy in or out); pass a float to force a specific terminal level, or
``None`` to deliberately expose the trap.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pulp

from gridflex.optimize.params import DispatchParams


@dataclass
class DispatchResult:
    """A solved (or attempted) dispatch schedule.

    Powers are in MW, energy in MWh, all arrays length T (the horizon).
    """

    p_ch: np.ndarray
    p_dis: np.ndarray
    e: np.ndarray
    u: np.ndarray
    status: str
    e_start: float

    @property
    def solved(self) -> bool:
        return self.status == "Optimal"


@dataclass
class RevenueBreakdown:
    """Revenue accounting for a schedule settled against a price vector."""

    gross_revenue_eur: float  # price * net export, before degradation
    degradation_cost_eur: float  # c_deg * energy discharged
    net_revenue_eur: float  # gross - degradation (the bottom line)
    energy_charged_mwh: float
    energy_discharged_mwh: float
    equivalent_full_cycles: float


def _make_solver(msg: bool = False, time_limit: float | None = None):
    """CBC ships with PuLP -- zero-install, deterministic, good enough here."""
    return pulp.PULP_CBC_CMD(msg=msg, timeLimit=time_limit)


def optimize_dispatch(
    prices: np.ndarray,
    cfg: DispatchParams,
    e_start: float | None = None,
    e_end: float | str | None = "cyclic",
    cycle_limit_mwh: float | None = None,
    allow_binary: bool = True,
    msg: bool = False,
    time_limit: float | None = None,
) -> DispatchResult:
    """Optimize battery dispatch against a price vector.

    Parameters
    ----------
    prices : array of EUR/MWh, one per timestep. Length defines the horizon.
    cfg : battery + market configuration.
    e_start : starting SoC in MWh. Defaults to ``cfg.e_init``.
    e_end : terminal SoC. ``"cyclic"`` (default) pins it to ``e_start``; a float
        forces that level; ``None`` leaves the end SoC free (exposes Trap 2).
    cycle_limit_mwh : max discharged energy over the horizon. Defaults to the
        daily cycle limit scaled to the horizon length. Pass ``float("inf")``
        to disable.
    allow_binary : keep the charge/discharge exclusivity binary (default True).
        ``False`` drops it -- only for demonstrating the negative-price trap.

    Returns
    -------
    DispatchResult with the schedule and solver status.
    """
    prices = np.asarray(prices, dtype=float)
    T = len(prices)
    if T == 0:
        raise ValueError("prices must be non-empty.")

    dt = cfg.delta_t_hours
    p_max = cfg.power_mw
    e_start = cfg.e_init if e_start is None else float(e_start)

    if cycle_limit_mwh is None:
        n_days = T * dt / 24.0
        cycle_limit_mwh = cfg.daily_cycle_energy_mwh * n_days

    prob = pulp.LpProblem("gridflex_dispatch", pulp.LpMaximize)

    p_ch = [pulp.LpVariable(f"p_ch_{t}", 0, p_max) for t in range(T)]
    p_dis = [pulp.LpVariable(f"p_dis_{t}", 0, p_max) for t in range(T)]
    e = [pulp.LpVariable(f"e_{t}", cfg.e_min, cfg.e_max) for t in range(T)]
    if allow_binary:
        u = [pulp.LpVariable(f"u_{t}", cat="Binary") for t in range(T)]
    else:
        u = [None] * T

    # Objective: arbitrage margin net of degradation.
    prob += pulp.lpSum(
        prices[t] * (p_dis[t] - p_ch[t]) * dt - cfg.c_deg_eur_per_mwh * p_dis[t] * dt
        for t in range(T)
    )

    # SoC dynamics + charge/discharge exclusivity.
    for t in range(T):
        prev = e_start if t == 0 else e[t - 1]
        prob += (
            (e[t] == prev + cfg.eta_ch * p_ch[t] * dt - p_dis[t] * dt / cfg.eta_dis),
            f"soc_dynamics_{t}",
        )
        if allow_binary:
            prob += p_ch[t] <= p_max * u[t], f"charge_gate_{t}"
            prob += p_dis[t] <= p_max * (1 - u[t]), f"discharge_gate_{t}"

    # Throughput / cycle cap. An infinite cap means "no limit" -- skip it
    # entirely (PuLP rejects an inf constraint RHS).
    if np.isfinite(cycle_limit_mwh):
        prob += (
            (pulp.lpSum(p_dis[t] * dt for t in range(T)) <= cycle_limit_mwh),
            "cycle_limit",
        )

    # Terminal condition.
    if e_end == "cyclic":
        prob += e[T - 1] == e_start, "terminal_soc"
    elif e_end is not None:
        prob += e[T - 1] == float(e_end), "terminal_soc"

    prob.solve(_make_solver(msg=msg, time_limit=time_limit))
    status = pulp.LpStatus[prob.status]

    def _vals(vars_):
        out = np.array([pulp.value(v) if v is not None else 0.0 for v in vars_])
        return np.nan_to_num(out, nan=0.0)

    p_ch_v = np.clip(_vals(p_ch), 0.0, None)
    p_dis_v = np.clip(_vals(p_dis), 0.0, None)
    e_v = _vals(e)
    u_v = _vals(u) if allow_binary else np.zeros(T)

    return DispatchResult(
        p_ch=p_ch_v,
        p_dis=p_dis_v,
        e=e_v,
        u=u_v,
        status=status,
        e_start=e_start,
    )


def settle(
    result: DispatchResult,
    prices: np.ndarray,
    cfg: DispatchParams,
) -> RevenueBreakdown:
    """Value a (possibly forecast-driven) schedule against a price vector.

    This is what makes the backtest honest: a schedule optimized on forecast
    prices is *settled* against the realised day-ahead prices here. Grid import
    (charging) is a cost, export (discharging) is revenue, both at ``prices``;
    degradation is charged on discharged energy regardless of price.
    """
    prices = np.asarray(prices, dtype=float)
    dt = cfg.delta_t_hours

    net_export = result.p_dis - result.p_ch
    gross = float(np.sum(prices * net_export) * dt)
    energy_dis = float(np.sum(result.p_dis) * dt)
    energy_ch = float(np.sum(result.p_ch) * dt)
    degradation = cfg.c_deg_eur_per_mwh * energy_dis

    return RevenueBreakdown(
        gross_revenue_eur=gross,
        degradation_cost_eur=degradation,
        net_revenue_eur=gross - degradation,
        energy_charged_mwh=energy_ch,
        energy_discharged_mwh=energy_dis,
        equivalent_full_cycles=energy_dis / cfg.energy_mwh,
    )
