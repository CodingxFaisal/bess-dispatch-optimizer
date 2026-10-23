"""PuLP formulation of the single-market, price-taking dispatch MILP.

See ``docs/MATHEMATICAL_MODEL.md`` §3. For interval ``t`` (duration ``dt_t``)::

    max  sum_t pi_t (P_dis_t - P_ch_t) dt_t            expected market revenue
         - c_deg * sum_t q_t                           degradation cost
         - c_trade * sum_t (P_ch_t + P_dis_t) dt_t     trading cost (if configured)
         + v_term * (E_{T-1} - E_start)                only for terminal 'free'

    E_t   = E_{t-1} + eta_ch P_ch_t dt_t - P_dis_t dt_t / eta_dis,  E_{-1} = E_start
    E_min <= E_t <= E_max
    0 <= P_ch_t  <= a_t Pbar_ch  u_t          (u_t binary: 1 = charging allowed)
    0 <= P_dis_t <= a_t Pbar_dis (1 - u_t)
    sum_t r_t <= N_day * H / 24 * E_ref       (if a cycle limit is configured)
    E_{T-1} = E_start (cyclic) | E_term (fixed)

with ``q_t`` the degradation throughput (DC: ``P_dis dt / eta_dis``; AC:
``P_dis dt``) and ``r_t``/``E_ref`` the EFC numerator/reference of the
configured basis. Charging is a purchase (negative revenue when the price is
positive, positive when it is negative); discharging is a sale.
"""

from __future__ import annotations

from dataclasses import dataclass

import pulp

from gridflex.optimization.problem import DispatchProblem


@dataclass(frozen=True)
class FormulationVars:
    p_ch: list[pulp.LpVariable]
    p_dis: list[pulp.LpVariable]
    e: list[pulp.LpVariable]
    u: list[pulp.LpVariable] | None


def build_model(
    problem: DispatchProblem, *, enforce_exclusivity: bool = True
) -> tuple[pulp.LpProblem, FormulationVars]:
    """Build the MILP. ``enforce_exclusivity=False`` drops the binary and is
    only meant for demonstrating why it is needed."""
    b = problem.battery
    n = problem.n_intervals
    dt = problem.dt_hours
    price = problem.prices_eur_per_mwh
    pc_max, pd_max = b.power_limits_mw(n, problem.availability)
    e0 = problem.start_energy_mwh

    prob = pulp.LpProblem(problem.label or "gridflex_dispatch", pulp.LpMaximize)
    p_ch = [prob.add_variable(f"p_ch_{t}", 0.0, float(pc_max[t])) for t in range(n)]
    p_dis = [prob.add_variable(f"p_dis_{t}", 0.0, float(pd_max[t])) for t in range(n)]
    e = [prob.add_variable(f"e_{t}", b.soc_min_mwh, b.soc_max_mwh) for t in range(n)]
    u = (
        [prob.add_variable(f"u_{t}", 0, 1, cat=pulp.LpBinary) for t in range(n)]
        if enforce_exclusivity
        else None
    )

    if b.degradation_basis == "dc_discharge":
        deg_coef = [float(dt[t]) / b.eta_discharge for t in range(n)]
    else:
        deg_coef = [float(dt[t]) for t in range(n)]

    objective = pulp.lpSum(
        float(price[t] * dt[t]) * (p_dis[t] - p_ch[t]) for t in range(n)
    ) - b.degradation_cost_eur_per_mwh * pulp.lpSum(
        deg_coef[t] * p_dis[t] for t in range(n)
    )
    fee = problem.trading_cost_eur_per_mwh
    if fee > 0:
        objective -= fee * pulp.lpSum(
            float(dt[t]) * (p_ch[t] + p_dis[t]) for t in range(n)
        )
    if b.terminal_policy == "free":
        assert b.terminal_energy_value_eur_per_mwh is not None
        objective += b.terminal_energy_value_eur_per_mwh * (e[n - 1] - e0)
    prob += objective

    for t in range(n):
        prev = e0 if t == 0 else e[t - 1]
        prob += (
            e[t]
            == prev
            + b.eta_charge * float(dt[t]) * p_ch[t]
            - float(dt[t]) / b.eta_discharge * p_dis[t],
            f"soc_balance_{t}",
        )
        if u is not None:
            prob += p_ch[t] <= float(pc_max[t]) * u[t], f"charge_mode_{t}"
            prob += p_dis[t] <= float(pd_max[t]) * (1 - u[t]), f"discharge_mode_{t}"

    limit = b.cycle_limit_efc(problem.horizon_hours)
    if limit is not None:
        if b.efc_basis == "dc_discharge_per_usable":
            efc_coef = [float(dt[t]) / b.eta_discharge for t in range(n)]
        else:
            efc_coef = [float(dt[t]) for t in range(n)]
        prob += (
            pulp.lpSum(efc_coef[t] * p_dis[t] for t in range(n))
            <= limit * b.efc_reference_mwh,
            "cycle_limit",
        )

    target = b.terminal_target_mwh(e0)
    if target is not None:
        prob += e[n - 1] == target, "terminal_energy"

    return prob, FormulationVars(p_ch=p_ch, p_dis=p_dis, e=e, u=u)
