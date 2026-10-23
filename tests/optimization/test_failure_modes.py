"""Failed, limited or invalid solves are never presented as schedules."""

import math

import numpy as np
import pulp
import pytest

import gridflex.optimization.solve as solve_mod
from gridflex.battery import BatteryModel
from gridflex.config import GridFlexConfig, OptimizationConfig
from gridflex.optimization import (
    DispatchProblem,
    ProblemError,
    SolveFailedError,
    solve_dispatch,
)
from tests_helpers import toy_battery


def _big_problem(gf_cfg: GridFlexConfig, n: int = 24 * 90) -> DispatchProblem:
    b = BatteryModel.from_config(gf_cfg.battery)
    return DispatchProblem(b, np.random.default_rng(1).normal(60, 120, n), 1.0)


def _assert_no_schedule(sol: solve_mod.DispatchSolution) -> None:
    assert not sol.success
    assert sol.schedule is None and sol.energy_mwh is None and sol.throughput is None
    assert sol.expected_revenue_eur is None
    with pytest.raises(SolveFailedError, match=sol.report.status):
        sol.require_success()


@pytest.mark.parametrize("solver", ["highs", "cbc"])
def test_infeasible_problem(solver: str) -> None:
    # From empty, reaching 9 MWh in one hour at 1 MW is impossible.
    b = toy_battery(terminal_policy="fixed", terminal_energy_mwh=9.0)
    sol = solve_dispatch(
        DispatchProblem(b, [10.0], 1.0), OptimizationConfig(solver=solver)
    )
    assert sol.report.status == "infeasible"
    _assert_no_schedule(sol)


def test_time_limit_is_not_success(gf_cfg: GridFlexConfig) -> None:
    sol = solve_dispatch(_big_problem(gf_cfg), OptimizationConfig(time_limit_s=1e-6))
    assert sol.report.status in ("time_limit_no_solution", "time_limit_feasible")
    assert sol.report.termination == "HiGHS Time limit reached"
    _assert_no_schedule(sol)


def test_pulp_reports_time_limited_incumbent_as_optimal(gf_cfg: GridFlexConfig) -> None:
    """Documents audit D24 and checks GridFlex does not inherit it.

    When HiGHS stops on its time limit *with* an incumbent, PuLP reports
    ``LpStatus "Optimal"``. Whether an incumbent exists by the deadline depends
    on machine speed, so a few limits are tried; the test skips if none yields
    one.
    """
    from gridflex.optimization import build_model

    problem = _big_problem(gf_cfg, n=8760)
    for limit in (0.3, 0.6, 1.0, 2.0, 3.0):
        prob, _ = build_model(problem)
        prob.solve(pulp.HiGHS(msg=False, threads=1, timeLimit=limit))
        h = prob.solverModel
        timed_out = h.modelStatusToString(h.getModelStatus()) == "Time limit reached"
        if timed_out and int(h.getInfo().primal_solution_status) == 2:
            break
    else:
        pytest.skip("no time-limited incumbent obtained on this machine")
    assert pulp.LpStatus[prob.status] == "Optimal"  # PuLP's misleading view
    sol = solve_dispatch(problem, OptimizationConfig(time_limit_s=limit))
    if sol.report.status == "optimal":  # this run happened to finish in time
        pytest.skip("solver finished within the limit on the second run")
    assert sol.report.status.startswith("time_limit")
    _assert_no_schedule(sol)


def test_unmapped_highs_status_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """PuLP raises KeyError for HiGHS kSolutionLimit; it must surface as 'error'."""
    monkeypatch.setattr(
        solve_mod,
        "_make_solver",
        lambda opts: pulp.HiGHS(msg=False, threads=1, mip_max_improving_sols=1),
    )
    b = BatteryModel(10, 10, 20, 1, 19, 10, 0.93, 0.93, max_efc_per_day=1.5)
    prices = np.random.default_rng(1).normal(60, 120, 24 * 30)
    sol = solve_dispatch(DispatchProblem(b, prices, 1.0))
    assert sol.report.status == "error"
    assert "KeyError" in sol.report.message
    _assert_no_schedule(sol)


def test_cbc_time_limit_is_not_success(gf_cfg: GridFlexConfig) -> None:
    sol = solve_dispatch(
        _big_problem(gf_cfg), OptimizationConfig(solver="cbc", time_limit_s=0.01)
    )
    assert sol.report.status.startswith("time_limit")
    _assert_no_schedule(sol)


def _fake_time_limited(gap: float | None):  # type: ignore[no-untyped-def]
    real = solve_mod._highs_outcome

    def fake(prob: pulp.LpProblem) -> solve_mod._RawOutcome:
        out = real(prob)
        return solve_mod._RawOutcome(
            "time_limit_feasible",
            "HiGHS Time limit reached",
            True,
            gap,
            out.dual_bound_eur,
            out.version,
        )

    return fake


@pytest.mark.parametrize(
    ("accept", "gap", "success"),
    [
        (False, 1e-5, False),
        (True, 1e-5, True),
        (True, 0.01, False),
        (True, None, False),
    ],
)
def test_time_limited_acceptance_policy(
    monkeypatch: pytest.MonkeyPatch, accept: bool, gap: float | None, success: bool
) -> None:
    monkeypatch.setattr(solve_mod, "_highs_outcome", _fake_time_limited(gap))
    opts = OptimizationConfig(accept_time_limited=accept, max_accepted_gap=1e-3)
    sol = solve_dispatch(DispatchProblem(toy_battery(), [10.0, 100.0], 1.0), opts)
    assert sol.report.status == "time_limit_feasible"
    assert sol.success is success
    assert (sol.schedule is not None) is success


def test_solver_exception_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: pulp.LpProblem, solver: object = None) -> int:
        raise pulp.PulpSolverError("solver binary missing")

    monkeypatch.setattr(pulp.LpProblem, "solve", boom)
    sol = solve_dispatch(DispatchProblem(toy_battery(), [10.0, 100.0], 1.0))
    assert sol.report.status == "error"
    assert "solver binary missing" in sol.report.message
    _assert_no_schedule(sol)


def test_solution_failing_physics_check_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Corrupt the extracted charge power: 1 MW above the limit.
    real = solve_mod._values

    def corrupt(variables: list[pulp.LpVariable]) -> np.ndarray:
        vals = real(variables)
        if variables and variables[0].name.startswith("p_ch"):
            vals = vals + 1.0
        return vals

    monkeypatch.setattr(solve_mod, "_values", corrupt)
    sol = solve_dispatch(DispatchProblem(toy_battery(), [10.0, 100.0], 1.0))
    assert sol.report.status == "invalid_solution"
    assert "CHARGE_POWER_LIMIT" in sol.report.feasibility_codes
    _assert_no_schedule(sol)


def test_objective_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    real = pulp.value
    monkeypatch.setattr(solve_mod.pulp, "value", lambda x: real(x) + 1.0)
    sol = solve_dispatch(DispatchProblem(toy_battery(), [10.0, 100.0], 1.0))
    assert sol.report.status == "invalid_solution"
    assert "disagrees" in sol.report.message
    _assert_no_schedule(sol)


def test_dropping_the_binary_yields_a_rejected_loop() -> None:
    # One interval at -100 EUR/MWh, cyclic, eta 0.9/0.8. The LP relaxation
    # charges 1 MW and discharges 0.72 MW at once (paid 100 * 0.28 = 28), which
    # is physically impossible: the checker rejects it.
    b = toy_battery(eta_charge=0.9, eta_discharge=0.8, initial_energy_mwh=5.0)
    lp = solve_dispatch(DispatchProblem(b, [-100.0], 1.0), enforce_exclusivity=False)
    assert lp.report.status == "invalid_solution"
    assert lp.report.objective_eur == pytest.approx(28.0, abs=1e-6)
    assert "SIMULTANEOUS_CHARGE_DISCHARGE" in lp.report.feasibility_codes
    milp = solve_dispatch(DispatchProblem(b, [-100.0], 1.0))
    assert milp.success and milp.objective_eur == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(prices_eur_per_mwh=[10.0, math.nan], dt_hours=1.0),
        dict(prices_eur_per_mwh=[10.0, math.inf], dt_hours=1.0),
        dict(prices_eur_per_mwh=[], dt_hours=1.0),
        dict(prices_eur_per_mwh=[[1.0]], dt_hours=1.0),
        dict(prices_eur_per_mwh=[10.0, 20.0], dt_hours=[1.0]),
        dict(prices_eur_per_mwh=[10.0], dt_hours=0.0),
        dict(prices_eur_per_mwh=[10.0], dt_hours=-1.0),
        dict(prices_eur_per_mwh=[10.0], dt_hours=1.0, e_start_mwh=11.0),
        dict(prices_eur_per_mwh=[10.0], dt_hours=1.0, e_start_mwh=math.nan),
        dict(prices_eur_per_mwh=[10.0], dt_hours=1.0, availability=[1.5]),
        dict(prices_eur_per_mwh=[10.0], dt_hours=1.0, availability=[1.0, 1.0]),
    ],
)
def test_invalid_problems_rejected_before_solving(kwargs: dict[str, object]) -> None:
    with pytest.raises(ProblemError):
        DispatchProblem(toy_battery(), **kwargs)  # type: ignore[arg-type]


def test_nan_price_message_is_explicit() -> None:
    with pytest.raises(ProblemError, match="no silent filling"):
        DispatchProblem(toy_battery(), [1.0, math.nan, 2.0], 1.0)
