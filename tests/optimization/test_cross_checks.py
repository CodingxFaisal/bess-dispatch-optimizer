"""The MILP agrees with independent references.

1. Mode enumeration: for small horizons, every charge/discharge mode pattern
   is fixed in turn and the remaining LP is solved with SciPy's ``linprog``
   from a separately written matrix formulation. The best LP value is the
   MILP optimum.
2. Solver agreement: HiGHS and CBC find the same optimum.
(v0.1 equivalence was verified in Stage 4 before the v0.1 MILP was retired;
see docs/VALIDATION.md.)
"""

import itertools

import numpy as np
import pytest
from scipy.optimize import linprog

from gridflex.battery import BatteryModel, check_schedule
from gridflex.config import GridFlexConfig, OptimizationConfig
from gridflex.optimization import DispatchProblem, solve_dispatch


def enumerated_optimum(p: DispatchProblem) -> float:
    """Independent optimum by enumerating operating modes (small T only)."""
    b, n = p.battery, p.n_intervals
    dt, price, e0 = p.dt_hours, p.prices_eur_per_mwh, p.start_energy_mwh
    pc_max, pd_max = b.power_limits_mw(n, p.availability)
    q = dt / b.eta_discharge if b.degradation_basis == "dc_discharge" else dt
    r = dt / b.eta_discharge if b.efc_basis == "dc_discharge_per_usable" else dt
    # x = [P_ch (n), P_dis (n)];  linprog minimises.
    cost = np.concatenate(
        [price * dt, -(price * dt) + b.degradation_cost_eur_per_mwh * q]
    )
    lower = np.tril(np.ones((n, n)))
    charge_gain = lower * (b.eta_charge * dt)  # cumulative energy in
    discharge_loss = lower * (dt / b.eta_discharge)  # cumulative energy out
    energy = np.hstack([charge_gain, -discharge_loss])  # E_t - e0
    a_ub = [energy, -energy]
    b_ub = [np.full(n, b.soc_max_mwh - e0), np.full(n, e0 - b.soc_min_mwh)]
    limit = b.cycle_limit_efc(p.horizon_hours)
    if limit is not None:
        a_ub.append(np.concatenate([np.zeros(n), r])[None, :])
        b_ub.append(np.array([limit * b.efc_reference_mwh]))
    a_eq = b_eq = None
    const = 0.0
    target = b.terminal_target_mwh(e0)
    if target is not None:
        a_eq, b_eq = energy[-1:], np.array([target - e0])
    elif b.terminal_policy == "free":
        v = b.terminal_energy_value_eur_per_mwh or 0.0
        cost = cost - v * energy[-1]  # maximise + v * (E_end - e0)
    best = -np.inf
    for modes in itertools.product([0, 1], repeat=n):  # 1 = charging allowed
        m = np.array(modes)
        bounds = [(0, pc_max[t] * m[t]) for t in range(n)] + [
            (0, pd_max[t] * (1 - m[t])) for t in range(n)
        ]
        res = linprog(
            cost,
            A_ub=np.vstack(a_ub),
            b_ub=np.concatenate(b_ub),
            A_eq=a_eq,
            b_eq=b_eq,
            bounds=bounds,
            method="highs",
        )
        if res.status == 0:
            best = max(best, -res.fun + const)
    return best


def random_problem(rng: np.random.Generator, n: int) -> DispatchProblem:
    cap = float(rng.uniform(2, 8))
    lo, hi = sorted(rng.uniform(0, cap, 2))
    policy = rng.choice(["cyclic", "fixed", "free"])
    kwargs: dict[str, object] = {}
    if policy == "fixed":
        kwargs["terminal_energy_mwh"] = float(rng.uniform(lo, hi))
    if policy == "free":
        kwargs["terminal_energy_value_eur_per_mwh"] = float(rng.uniform(0, 80))
    b = BatteryModel(
        charge_power_mw=float(rng.uniform(0.5, 3)),
        discharge_power_mw=float(rng.uniform(0.5, 3)),
        energy_capacity_mwh=cap,
        soc_min_mwh=float(lo),
        soc_max_mwh=float(hi),
        initial_energy_mwh=float(rng.uniform(lo, hi)),
        eta_charge=float(rng.uniform(0.8, 1.0)),
        eta_discharge=float(rng.uniform(0.8, 1.0)),
        max_efc_per_day=float(rng.uniform(1, 6)) if rng.random() < 0.6 else None,
        efc_basis=rng.choice(["dc_discharge_per_usable", "ac_discharge_per_nameplate"]),
        terminal_policy=policy,  # type: ignore[arg-type]
        degradation_cost_eur_per_mwh=float(rng.uniform(0, 30)),
        degradation_basis=rng.choice(["dc_discharge", "ac_discharge"]),
        **kwargs,  # type: ignore[arg-type]
    )
    avail = (
        rng.choice([1.0, 0.5, 0.0], size=n, p=[0.7, 0.2, 0.1])
        if rng.random() < 0.3
        else None
    )
    return DispatchProblem(
        b,
        rng.normal(40, 120, n),
        rng.choice([0.25, 0.5, 1.0], size=n),
        availability=avail,
    )


@pytest.mark.parametrize("seed", range(40))
def test_milp_matches_mode_enumeration(seed: int) -> None:
    rng = np.random.default_rng(seed)
    p = random_problem(rng, n=int(rng.integers(2, 7)))
    reference = enumerated_optimum(p)
    sol = solve_dispatch(p)
    if not np.isfinite(reference):
        assert sol.report.status == "infeasible"
        return
    assert sol.success, sol.report
    assert sol.objective_eur == pytest.approx(reference, rel=1e-6, abs=1e-5)


@pytest.mark.parametrize("seed", range(8))
def test_highs_and_cbc_agree(gf_cfg: GridFlexConfig, seed: int) -> None:
    b = BatteryModel.from_config(gf_cfg.battery)
    rng = np.random.default_rng(100 + seed)
    p = DispatchProblem(
        b, rng.normal(60, 120, 24 if seed % 2 else 96), 1.0 if seed % 2 else 0.25
    )
    highs = solve_dispatch(p, OptimizationConfig(solver="highs"))
    cbc = solve_dispatch(p, OptimizationConfig(solver="cbc"))
    assert highs.success and cbc.success
    assert highs.objective_eur == pytest.approx(cbc.objective_eur, rel=1e-6, abs=1e-4)


def test_deterministic_repeat_solves(gf_cfg: GridFlexConfig) -> None:
    b = BatteryModel.from_config(gf_cfg.battery)
    p = DispatchProblem(b, np.random.default_rng(7).normal(60, 120, 96), 0.25)
    a, c = solve_dispatch(p), solve_dispatch(p)
    assert a.schedule is not None and c.schedule is not None
    assert np.array_equal(a.schedule.charge_mw, c.schedule.charge_mw)
    assert np.array_equal(a.schedule.discharge_mw, c.schedule.discharge_mw)
    assert a.objective_eur == c.objective_eur


@pytest.mark.parametrize("seed", range(20))
def test_successful_schedules_are_feasible_and_consistent(seed: int) -> None:
    rng = np.random.default_rng(300 + seed)
    p = random_problem(rng, n=int(rng.integers(4, 48)))
    sol = solve_dispatch(p)
    if not sol.success:
        assert sol.report.status == "infeasible" and sol.schedule is None
        return
    assert sol.schedule is not None and sol.throughput is not None
    # Independent re-check (same as built-in verification, called directly).
    assert check_schedule(
        p.battery,
        sol.schedule,
        e_start_mwh=p.start_energy_mwh,
        availability=p.availability,
    ).ok
    # Objective decomposition: revenue - degradation + terminal value.
    assert sol.objective_eur == pytest.approx(
        sol.expected_revenue_eur - sol.degradation_cost_eur + sol.terminal_value_eur,
        abs=1e-9,
    )
    # Reported solver diagnostics are populated.
    r = sol.report
    assert r.status == "optimal" and r.solver_version.startswith("HiGHS")
    assert r.mip_gap is not None and r.mip_gap <= 1e-6
    assert r.dual_bound_eur is not None and r.dual_bound_eur >= sol.objective_eur - 1e-6
    assert r.n_binaries == p.n_intervals and r.runtime_s >= 0
