"""Battery physics: parameters, state equation, throughput and feasibility.

See ``docs/MATHEMATICAL_MODEL.md`` §2 for the equations and conventions.
"""

from gridflex.battery.feasibility import (
    FeasibilityReport,
    InfeasibleScheduleError,
    Violation,
    check_schedule,
)
from gridflex.battery.model import BatteryModel, TerminalPolicy
from gridflex.battery.physics import (
    Schedule,
    Throughput,
    energy_trajectory,
    interval_hours_from_index,
    throughput,
)

__all__ = [
    "BatteryModel",
    "FeasibilityReport",
    "InfeasibleScheduleError",
    "Schedule",
    "TerminalPolicy",
    "Throughput",
    "Violation",
    "check_schedule",
    "energy_trajectory",
    "interval_hours_from_index",
    "throughput",
]
