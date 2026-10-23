"""Independent feasibility checker for dispatch schedules.

This module re-derives every physical constraint from :class:`BatteryModel`
and the state equation, without reference to any optimiser formulation. It is
used to verify optimiser output (Stage 4) and to gate settlement (Stage 6):
a schedule that fails here must never be settled as an optimised result.

Violation codes:

=============================== ==============================================
NON_FINITE                      NaN or infinite power or duration
BAD_INTERVAL_DURATION           an interval duration <= 0
NEGATIVE_POWER                  charge or discharge power below zero
CHARGE_POWER_LIMIT              charge above the (availability-derated) limit
DISCHARGE_POWER_LIMIT           discharge above the (availability-derated) limit
SIMULTANEOUS_CHARGE_DISCHARGE   both directions active in one interval
INITIAL_ENERGY_OUT_OF_BOUNDS    start energy outside [soc_min, soc_max]
SOC_BELOW_MIN / SOC_ABOVE_MAX   end-of-interval energy outside the bounds
TERMINAL_ENERGY                 end energy differs from the terminal policy
CYCLE_LIMIT                     EFC above the pro-rated daily limit
=============================== ==============================================
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from gridflex.battery.model import BatteryModel, FloatArray
from gridflex.battery.physics import Schedule, Throughput, energy_trajectory, throughput

DEFAULT_TOL_MW = 1e-6
DEFAULT_TOL_MWH = 1e-6


class InfeasibleScheduleError(ValueError):
    """A schedule violates the battery's physical constraints."""

    def __init__(self, report: FeasibilityReport) -> None:
        self.report = report
        super().__init__(report.summary())


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    count: int
    worst: float
    first_interval: int | None = None


@dataclass(frozen=True)
class FeasibilityReport:
    violations: tuple[Violation, ...] = field(default_factory=tuple)
    e_start_mwh: float | None = None
    energy_mwh: FloatArray | None = None
    throughput: Throughput | None = None

    @property
    def ok(self) -> bool:
        return not self.violations

    def codes(self) -> set[str]:
        return {v.code for v in self.violations}

    def summary(self) -> str:
        if self.ok:
            return "feasible"
        lines = ["infeasible schedule:"]
        for v in self.violations:
            where = (
                "" if v.first_interval is None else f" (first at t={v.first_interval})"
            )
            lines.append(
                f"  {v.code}: {v.message}; {v.count} case(s), "
                f"worst {v.worst:.6g}{where}"
            )
        return "\n".join(lines)

    def raise_for_violations(self) -> None:
        if not self.ok:
            raise InfeasibleScheduleError(self)


def _excess(values: FloatArray, code: str, message: str) -> Violation | None:
    """Violation for positive entries of ``values`` (amount beyond tolerance)."""
    bad = np.flatnonzero(values > 0)
    if not len(bad):
        return None
    return Violation(code, message, len(bad), float(values[bad].max()), int(bad[0]))


def check_schedule(
    battery: BatteryModel,
    schedule: Schedule,
    *,
    e_start_mwh: float | None = None,
    availability: float | FloatArray | None = None,
    check_terminal: bool = True,
    check_cycles: bool = True,
    tol_mw: float = DEFAULT_TOL_MW,
    tol_mwh: float = DEFAULT_TOL_MWH,
) -> FeasibilityReport:
    """Check ``schedule`` against every physical constraint of ``battery``.

    ``e_start_mwh`` defaults to the battery's initial energy (use the carried
    state for later horizons). ``availability`` overrides the configured
    availability fraction (scalar or per-interval array).
    """
    e0 = battery.initial_energy_mwh if e_start_mwh is None else float(e_start_mwh)
    pc, pd_, dt = schedule.charge_mw, schedule.discharge_mw, schedule.dt_hours
    out: list[Violation] = []

    finite = np.isfinite(pc) & np.isfinite(pd_) & np.isfinite(dt)
    if not finite.all() or not np.isfinite(e0):
        bad = np.flatnonzero(~finite)
        first = int(bad[0]) if len(bad) else None
        out.append(
            Violation(
                "NON_FINITE",
                "non-finite power, duration or start energy",
                len(bad) or 1,
                float("nan"),
                first,
            )
        )
        return FeasibilityReport(tuple(out), e0)

    nonpositive = np.flatnonzero(dt <= 0)
    if len(nonpositive):
        out.append(
            Violation(
                "BAD_INTERVAL_DURATION",
                "interval duration must be > 0 h",
                len(nonpositive),
                float(dt[nonpositive].min()),
                int(nonpositive[0]),
            )
        )
        return FeasibilityReport(tuple(out), e0)

    for v in (
        _excess(-pc - tol_mw, "NEGATIVE_POWER", "charge power below 0 MW"),
        _excess(-pd_ - tol_mw, "NEGATIVE_POWER", "discharge power below 0 MW"),
    ):
        if v is not None:
            out.append(v)

    pc_max, pd_max = battery.power_limits_mw(schedule.n_intervals, availability)
    for v in (
        _excess(
            pc - pc_max - tol_mw, "CHARGE_POWER_LIMIT", "charge power above limit [MW]"
        ),
        _excess(
            pd_ - pd_max - tol_mw,
            "DISCHARGE_POWER_LIMIT",
            "discharge power above limit [MW]",
        ),
        _excess(
            np.minimum(pc, pd_) - tol_mw,
            "SIMULTANEOUS_CHARGE_DISCHARGE",
            "charging and discharging in the same interval [MW overlap]",
        ),
    ):
        if v is not None:
            out.append(v)

    lo, hi = battery.soc_min_mwh, battery.soc_max_mwh
    if not lo - tol_mwh <= e0 <= hi + tol_mwh:
        out.append(
            Violation(
                "INITIAL_ENERGY_OUT_OF_BOUNDS",
                f"start energy {e0:.6g} MWh outside [{lo:g}, {hi:g}]",
                1,
                max(lo - e0, e0 - hi),
                None,
            )
        )

    energy = energy_trajectory(battery, schedule, e0)
    for v in (
        _excess(lo - energy - tol_mwh, "SOC_BELOW_MIN", "energy below minimum [MWh]"),
        _excess(energy - hi - tol_mwh, "SOC_ABOVE_MAX", "energy above maximum [MWh]"),
    ):
        if v is not None:
            out.append(v)

    if check_terminal and len(energy):
        target = battery.terminal_target_mwh(e0)
        if target is not None and abs(energy[-1] - target) > tol_mwh:
            out.append(
                Violation(
                    "TERMINAL_ENERGY",
                    f"end energy {energy[-1]:.6g} MWh, policy "
                    f"'{battery.terminal_policy}' requires {target:.6g} MWh",
                    1,
                    abs(energy[-1] - target),
                    schedule.n_intervals - 1,
                )
            )

    flows = throughput(battery, schedule)
    limit = battery.cycle_limit_efc(schedule.horizon_hours)
    # Tolerance in EFC units: energy tolerance over the cycle reference energy.
    efc_tol = tol_mwh / battery.efc_reference_mwh
    if check_cycles and limit is not None and flows.efc > limit + efc_tol:
        out.append(
            Violation(
                "CYCLE_LIMIT",
                f"{flows.efc:.6g} EFC ({battery.efc_basis}) over "
                f"{schedule.horizon_hours:g} h, limit {limit:.6g}",
                1,
                flows.efc - limit,
                None,
            )
        )

    return FeasibilityReport(tuple(out), e0, energy, flows)
