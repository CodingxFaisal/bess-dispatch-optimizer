"""Solve dispatch problems and report exactly what the solver did.

Status semantics (``SolveReport.status``):

======================== =========================================================
optimal                  solver proved optimality within ``mip_rel_gap``
time_limit_feasible      time limit hit with an incumbent (not optimal)
time_limit_no_solution   time limit hit without any feasible solution
infeasible               the problem has no feasible schedule
unbounded                the objective is unbounded (indicates a modelling error)
invalid_solution         the solver's answer failed independent verification
error                    the solver failed, or returned an unclassified status
======================== =========================================================

``success`` is true only for ``optimal``, or for ``time_limit_feasible``
when ``accept_time_limited`` is set and the proven gap is within
``max_accepted_gap``. A schedule is attached **only on success**, and only after
it passed :func:`gridflex.battery.check_schedule` and the objective was
recomputed from the schedule.

Note: PuLP maps HiGHS's ``kTimeLimit`` to ``LpStatus "Optimal"``, so PuLP's
status string must not be trusted for HiGHS. This module reads the native
HiGHS model status and primal-solution status instead (audit D24).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
import pulp

from gridflex.battery.feasibility import check_schedule
from gridflex.battery.model import FloatArray
from gridflex.battery.physics import Schedule, Throughput, energy_trajectory
from gridflex.config.models import OptimizationConfig
from gridflex.optimization.formulation import FormulationVars, build_model
from gridflex.optimization.problem import DispatchProblem

logger = logging.getLogger(__name__)

SolveStatus = Literal[
    "optimal",
    "time_limit_feasible",
    "time_limit_no_solution",
    "infeasible",
    "unbounded",
    "invalid_solution",
    "error",
]
_OBJECTIVE_RTOL = 1e-6
_OBJECTIVE_ATOL_EUR = 1e-4


class SolveFailedError(RuntimeError):
    """Raised by :meth:`DispatchSolution.require_success` for failed solves."""


@dataclass(frozen=True)
class SolveReport:
    status: SolveStatus
    success: bool
    solver: str
    solver_version: str
    termination: str
    runtime_s: float
    objective_eur: float | None = None
    objective_check_eur: float | None = None
    dual_bound_eur: float | None = None
    mip_gap: float | None = None
    n_variables: int = 0
    n_constraints: int = 0
    n_binaries: int = 0
    time_limit_s: float | None = None
    mip_rel_gap: float | None = None
    threads: int | None = None
    feasibility_codes: tuple[str, ...] = ()
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["feasibility_codes"] = list(self.feasibility_codes)
        return out


@dataclass(frozen=True)
class DispatchSolution:
    """Outcome of one solve. Schedule fields are ``None`` unless successful."""

    problem: DispatchProblem
    report: SolveReport
    schedule: Schedule | None = None
    energy_mwh: FloatArray | None = None
    throughput: Throughput | None = None
    expected_revenue_eur: float | None = None
    degradation_cost_eur: float | None = None
    terminal_value_eur: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.report.success

    @property
    def objective_eur(self) -> float | None:
        return self.report.objective_check_eur

    def require_success(self) -> DispatchSolution:
        if not self.success:
            raise SolveFailedError(
                f"dispatch solve failed: {self.report.status} "
                f"({self.report.termination}) {self.report.message}".strip()
            )
        return self


# --------------------------------------------------------------------------- #
# Solver adapters                                                             #
# --------------------------------------------------------------------------- #
def highs_version() -> str:
    import highspy

    return str(highspy.Highs().version())  # type: ignore[no-untyped-call]


def _make_solver(opts: OptimizationConfig) -> pulp.LpSolver:
    if opts.solver == "highs":
        return pulp.HiGHS(
            msg=opts.solver_log,
            timeLimit=opts.time_limit_s,
            gapRel=opts.mip_rel_gap,
            threads=opts.threads,
        )
    return pulp.PULP_CBC_CMD(
        msg=opts.solver_log,
        timeLimit=opts.time_limit_s,
        gapRel=opts.mip_rel_gap,
        threads=opts.threads,
    )


@dataclass(frozen=True)
class _RawOutcome:
    status: SolveStatus
    termination: str
    has_solution: bool
    mip_gap: float | None
    dual_bound_eur: float | None
    version: str


def _highs_outcome(prob: pulp.LpProblem) -> _RawOutcome:
    import highspy

    h = prob.solverModel
    ms = h.getModelStatus()
    info = h.getInfo()
    has_solution = int(info.primal_solution_status) == 2  # kSolutionStatusFeasible
    S = highspy.HighsModelStatus
    if ms == S.kOptimal:
        status: SolveStatus = "optimal"
    elif ms == S.kTimeLimit:
        status = "time_limit_feasible" if has_solution else "time_limit_no_solution"
    elif ms in (S.kInfeasible, S.kUnboundedOrInfeasible):
        status = "infeasible"
    elif ms == S.kUnbounded:
        status = "unbounded"
    else:
        status = "error"
    gap = float(info.mip_gap)
    # HiGHS minimises -objective (PuLP negates a maximisation); the PuLP
    # objective constant is not passed to HiGHS.
    bound = float(info.mip_dual_bound)
    constant = float(prob.objective.constant or 0.0)
    return _RawOutcome(
        status=status,
        termination=f"HiGHS {h.modelStatusToString(ms)}",
        has_solution=has_solution,
        mip_gap=gap if math.isfinite(gap) else None,
        dual_bound_eur=-bound + constant if math.isfinite(bound) else None,
        version=f"HiGHS {highs_version()}",
    )


def _cbc_outcome(prob: pulp.LpProblem, opts: OptimizationConfig) -> _RawOutcome:
    st, sol = prob.status, prob.sol_status
    if st == pulp.LpStatusOptimal and sol == pulp.LpSolutionOptimal:
        status: SolveStatus = "optimal"
    elif sol == pulp.LpSolutionIntegerFeasible:
        status = "time_limit_feasible"
    elif st == pulp.LpStatusInfeasible:
        status = "infeasible"
    elif st == pulp.LpStatusUnbounded:
        status = "unbounded"
    elif st == pulp.LpStatusNotSolved and opts.time_limit_s is not None:
        status = "time_limit_no_solution"
    else:
        status = "error"
    return _RawOutcome(
        status=status,
        termination=f"CBC {pulp.LpStatus[st]} / solution {pulp.LpSolution[sol]}",
        has_solution=status in ("optimal", "time_limit_feasible"),
        mip_gap=None,  # not reported by PuLP's CBC command interface
        dual_bound_eur=None,
        version=f"CBC (bundled with PuLP {pulp.__version__})",
    )


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #
def _values(variables: list[pulp.LpVariable]) -> FloatArray:
    raw = [v.varValue for v in variables]
    return np.array(
        [math.nan if x is None else float(x) for x in raw], dtype=np.float64
    )


def _clean_power(values: FloatArray, tol: float) -> FloatArray:
    """Snap solver round-off within ``tol`` of zero to zero; keep larger errors."""
    out = values.copy()
    out[np.abs(out) <= tol] = 0.0
    return out


def solve_dispatch(
    problem: DispatchProblem,
    options: OptimizationConfig | None = None,
    *,
    enforce_exclusivity: bool = True,
) -> DispatchSolution:
    """Optimise ``problem``; never raises for solver failures (see the report)."""
    opts = options or OptimizationConfig()
    prob, xs = build_model(problem, enforce_exclusivity=enforce_exclusivity)
    n_bin = 0 if xs.u is None else len(xs.u)
    base = dict(
        solver=opts.solver,
        n_variables=prob.numVariables(),
        n_constraints=prob.numConstraints(),
        n_binaries=n_bin,
        time_limit_s=opts.time_limit_s,
        mip_rel_gap=opts.mip_rel_gap,
        threads=opts.threads,
    )

    t0 = time.perf_counter()
    try:
        prob.solve(_make_solver(opts))
        raw = (
            _highs_outcome(prob) if opts.solver == "highs" else _cbc_outcome(prob, opts)
        )
    except Exception as exc:  # solver crashes are reported, not raised
        runtime = time.perf_counter() - t0
        report = SolveReport(
            status="error",
            success=False,
            solver_version="unknown",
            termination="exception",
            runtime_s=runtime,
            message=f"{type(exc).__name__}: {exc}",
            **base,
        )
        _log(problem, report)
        return DispatchSolution(problem=problem, report=report)
    runtime = time.perf_counter() - t0

    common = dict(
        solver_version=raw.version,
        termination=raw.termination,
        runtime_s=runtime,
        mip_gap=raw.mip_gap,
        dual_bound_eur=raw.dual_bound_eur,
        **base,
    )
    if raw.status not in ("optimal", "time_limit_feasible") or not raw.has_solution:
        report = SolveReport(status=raw.status, success=False, **common)
        _log(problem, report)
        return DispatchSolution(problem=problem, report=report)

    solution = _verify(problem, prob, xs, raw, opts, common)
    _log(problem, solution.report)
    return solution


def _verify(
    problem: DispatchProblem,
    prob: pulp.LpProblem,
    xs: FormulationVars,
    raw: _RawOutcome,
    opts: OptimizationConfig,
    common: dict[str, Any],
) -> DispatchSolution:
    b = problem.battery
    solver_obj = pulp.value(prob.objective)
    pc = _clean_power(_values(xs.p_ch), opts.feasibility_tol_mw)
    pd_ = _clean_power(_values(xs.p_dis), opts.feasibility_tol_mw)
    schedule = Schedule(pc, pd_, problem.dt_hours, problem.index)
    check = check_schedule(
        b,
        schedule,
        e_start_mwh=problem.start_energy_mwh,
        availability=problem.availability,
        tol_mw=opts.feasibility_tol_mw,
        tol_mwh=opts.feasibility_tol_mwh,
    )
    if not check.ok or check.throughput is None or check.energy_mwh is None:
        report = SolveReport(
            status="invalid_solution",
            success=False,
            objective_eur=_finite(solver_obj),
            feasibility_codes=tuple(sorted(check.codes())),
            message=check.summary(),
            **common,
        )
        return DispatchSolution(problem=problem, report=report)

    revenue = float(
        np.sum(problem.prices_eur_per_mwh * schedule.net_export_mw * schedule.dt_hours)
    )
    deg = check.throughput.degradation_cost_eur
    terminal = 0.0
    if b.terminal_policy == "free":
        assert b.terminal_energy_value_eur_per_mwh is not None
        terminal = b.terminal_energy_value_eur_per_mwh * (
            float(check.energy_mwh[-1]) - problem.start_energy_mwh
        )
    recomputed = revenue - deg + terminal
    obj = _finite(solver_obj)
    if obj is None or not math.isclose(
        obj, recomputed, rel_tol=_OBJECTIVE_RTOL, abs_tol=_OBJECTIVE_ATOL_EUR
    ):
        report = SolveReport(
            status="invalid_solution",
            success=False,
            objective_eur=obj,
            objective_check_eur=recomputed,
            message="solver objective disagrees with the "
            "objective recomputed from the schedule",
            **common,
        )
        return DispatchSolution(problem=problem, report=report)

    gap_ok = raw.mip_gap is not None and raw.mip_gap <= opts.max_accepted_gap
    success = raw.status == "optimal" or (opts.accept_time_limited and gap_ok)
    report = SolveReport(
        status=raw.status,
        success=success,
        objective_eur=obj,
        objective_check_eur=recomputed,
        **common,
    )
    if not success:
        return DispatchSolution(problem=problem, report=report)
    return DispatchSolution(
        problem=problem,
        report=report,
        schedule=schedule,
        energy_mwh=energy_trajectory(b, schedule, problem.start_energy_mwh),
        throughput=check.throughput,
        expected_revenue_eur=revenue,
        degradation_cost_eur=deg,
        terminal_value_eur=terminal,
    )


def _finite(x: object) -> float | None:
    if x is None:
        return None
    v = float(x)  # type: ignore[arg-type]
    return v if math.isfinite(v) else None


def _log(problem: DispatchProblem, report: SolveReport) -> None:
    level = logging.INFO if report.success else logging.WARNING
    logger.log(
        level,
        "event=dispatch_solve label=%s intervals=%d status=%s success=%s "
        "termination=%r objective_eur=%s gap=%s runtime_s=%.3f",
        problem.label,
        problem.n_intervals,
        report.status,
        report.success,
        report.termination,
        report.objective_check_eur,
        report.mip_gap,
        report.runtime_s,
    )
