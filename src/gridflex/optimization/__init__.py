"""Dispatch optimisation (PuLP formulation, HiGHS by default) with diagnostics.

See ``docs/MATHEMATICAL_MODEL.md`` §3.
"""

from gridflex.optimization.formulation import FormulationVars, build_model
from gridflex.optimization.problem import DispatchProblem, ProblemError
from gridflex.optimization.solve import (
    DispatchSolution,
    SolveFailedError,
    SolveReport,
    SolveStatus,
    highs_version,
    solve_dispatch,
)

__all__ = [
    "DispatchProblem",
    "DispatchSolution",
    "FormulationVars",
    "ProblemError",
    "SolveFailedError",
    "SolveReport",
    "SolveStatus",
    "build_model",
    "highs_version",
    "solve_dispatch",
]
