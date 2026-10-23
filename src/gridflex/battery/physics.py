"""Schedules, the state equation, and throughput accounting.

For interval ``t`` of duration ``dt[t]`` hours, with AC powers held constant
over the interval::

    E[t] = E[t-1] + eta_ch * P_ch[t] * dt[t] - P_dis[t] * dt[t] / eta_dis
    E[-1] = E_start

``E[t]`` is the stored energy (MWh) at the *end* of interval ``t``. Because
powers are constant within an interval, energy moves linearly between the
end-of-interval values, so checking SoC bounds at interval ends (and at the
start) is sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from gridflex.battery.model import BatteryModel, FloatArray


def _as_vector(values: object, name: str) -> FloatArray:
    arr = np.array(values, dtype=np.float64, copy=True)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    arr.setflags(write=False)
    return arr


def interval_hours_from_index(index: pd.DatetimeIndex, end: pd.Timestamp) -> FloatArray:
    """Durations (h) of intervals starting at ``index``; the last ends at ``end``."""
    if index.tz is None or pd.Timestamp(end).tzinfo is None:
        raise ValueError("index and end must be timezone-aware")
    if len(index) == 0:
        raise ValueError("index must not be empty")
    bounds = index.append(pd.DatetimeIndex([pd.Timestamp(end)]).tz_convert(index.tz))
    # Timedelta arithmetic is independent of the index's storage unit (pandas 3
    # may store datetimes in us rather than ns, so raw integers are unsafe).
    hours = (bounds[1:] - bounds[:-1]) / pd.Timedelta(hours=1)
    if np.any(hours <= 0):
        raise ValueError("interval starts must be strictly increasing and before end")
    return np.asarray(hours, dtype=np.float64)


@dataclass(frozen=True)
class Schedule:
    """A dispatch schedule: AC charge and discharge power per interval."""

    charge_mw: FloatArray
    discharge_mw: FloatArray
    dt_hours: FloatArray
    index: pd.DatetimeIndex | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "charge_mw", _as_vector(self.charge_mw, "charge_mw"))
        object.__setattr__(
            self, "discharge_mw", _as_vector(self.discharge_mw, "discharge_mw")
        )
        dt = self.dt_hours
        if np.ndim(dt) == 0:
            dt = np.full(len(self.charge_mw), float(dt))
        object.__setattr__(self, "dt_hours", _as_vector(dt, "dt_hours"))
        n = len(self.charge_mw)
        if len(self.discharge_mw) != n or len(self.dt_hours) != n:
            raise ValueError(
                "charge_mw, discharge_mw and dt_hours must have the same length"
            )
        if self.index is not None and len(self.index) != n:
            raise ValueError("index length must match the schedule length")

    @classmethod
    def from_index(
        cls,
        index: pd.DatetimeIndex,
        end: pd.Timestamp,
        charge_mw: object,
        discharge_mw: object,
    ) -> Schedule:
        """Schedule whose interval durations follow a tz-aware index."""
        return cls(
            charge_mw=_as_vector(charge_mw, "charge_mw"),
            discharge_mw=_as_vector(discharge_mw, "discharge_mw"),
            dt_hours=interval_hours_from_index(index, end),
            index=index,
        )

    @property
    def n_intervals(self) -> int:
        return len(self.charge_mw)

    @property
    def horizon_hours(self) -> float:
        return float(self.dt_hours.sum())

    @property
    def net_export_mw(self) -> FloatArray:
        """Discharge minus charge (positive = export to the grid)."""
        return self.discharge_mw - self.charge_mw


def energy_trajectory(
    battery: BatteryModel, schedule: Schedule, e_start_mwh: float | None = None
) -> FloatArray:
    """Stored energy (MWh) at the end of each interval, from the state equation."""
    e0 = battery.initial_energy_mwh if e_start_mwh is None else float(e_start_mwh)
    delta = (
        battery.eta_charge * schedule.charge_mw * schedule.dt_hours
        - schedule.discharge_mw * schedule.dt_hours / battery.eta_discharge
    )
    return np.asarray(e0 + np.cumsum(delta), dtype=np.float64)


@dataclass(frozen=True)
class Throughput:
    """Energy flows over a schedule (all MWh) and derived cycle/degradation figures.

    * ``ac_*``: measured at the grid connection (``P * dt``).
    * ``dc_charge_mwh``: energy entering storage (``eta_ch * P_ch * dt``).
    * ``dc_discharge_mwh``: energy leaving storage (``P_dis * dt / eta_dis``).
    * ``conversion_losses_mwh``:
      ``(ac_charge - dc_charge) + (dc_discharge - ac_discharge)``.
    """

    ac_charge_mwh: float
    ac_discharge_mwh: float
    dc_charge_mwh: float
    dc_discharge_mwh: float
    conversion_losses_mwh: float
    efc: float
    efc_dc_per_usable: float
    efc_ac_per_nameplate: float
    degradation_throughput_mwh: float
    degradation_cost_eur: float

    @property
    def stored_energy_change_mwh(self) -> float:
        return self.dc_charge_mwh - self.dc_discharge_mwh


def throughput(battery: BatteryModel, schedule: Schedule) -> Throughput:
    dt = schedule.dt_hours
    ac_ch = float(np.sum(schedule.charge_mw * dt))
    ac_dis = float(np.sum(schedule.discharge_mw * dt))
    dc_ch = battery.eta_charge * ac_ch
    dc_dis = ac_dis / battery.eta_discharge
    efc_dc = dc_dis / battery.usable_energy_mwh
    efc_ac = ac_dis / battery.energy_capacity_mwh
    efc = efc_dc if battery.efc_basis == "dc_discharge_per_usable" else efc_ac
    deg_mwh = dc_dis if battery.degradation_basis == "dc_discharge" else ac_dis
    return Throughput(
        ac_charge_mwh=ac_ch,
        ac_discharge_mwh=ac_dis,
        dc_charge_mwh=dc_ch,
        dc_discharge_mwh=dc_dis,
        conversion_losses_mwh=(ac_ch - dc_ch) + (dc_dis - ac_dis),
        efc=efc,
        efc_dc_per_usable=efc_dc,
        efc_ac_per_nameplate=efc_ac,
        degradation_throughput_mwh=deg_mwh,
        degradation_cost_eur=battery.degradation_cost_eur_per_mwh * deg_mwh,
    )
