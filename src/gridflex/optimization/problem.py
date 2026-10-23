"""Dispatch problem definition and input validation.

A :class:`DispatchProblem` is one optimisation horizon: a battery, a price
vector (EUR/MWh) and the duration of each interval (h). Invalid inputs raise
:class:`ProblemError` *before* any solver is called. In particular, missing
(NaN) prices are refused: gaps must be handled explicitly upstream, never by
the optimiser.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from gridflex.battery.model import BatteryModel, FloatArray
from gridflex.battery.physics import interval_hours_from_index

_E_START_TOL_MWH = 1e-9


class ProblemError(ValueError):
    """The dispatch problem is malformed (bad prices, durations or state)."""


def _vector(values: object, name: str) -> FloatArray:
    arr = np.array(values, dtype=np.float64, copy=True)
    if arr.ndim != 1:
        raise ProblemError(f"{name} must be one-dimensional")
    arr.setflags(write=False)
    return arr


@dataclass(frozen=True)
class DispatchProblem:
    """One horizon of the dispatch problem.

    ``prices_eur_per_mwh`` are the prices the schedule is optimised against
    (a forecast, or realised prices for the perfect-foresight benchmark).
    ``e_start_mwh`` defaults to the battery's initial energy; in a rolling
    backtest it must be the physically simulated carried state.
    ``availability`` (per interval, in [0, 1]) overrides the battery's
    availability fraction. ``trading_cost_eur_per_mwh`` is charged on every
    MWh bought and sold at the grid connection.
    """

    battery: BatteryModel
    prices_eur_per_mwh: FloatArray
    dt_hours: FloatArray
    e_start_mwh: float | None = None
    availability: FloatArray | None = None
    index: pd.DatetimeIndex | None = None
    label: str = ""
    trading_cost_eur_per_mwh: float = 0.0

    def __post_init__(self) -> None:
        prices = _vector(self.prices_eur_per_mwh, "prices_eur_per_mwh")
        n = len(prices)
        if n == 0:
            raise ProblemError("the horizon must contain at least one interval")
        dt = self.dt_hours
        if np.ndim(dt) == 0:
            dt = np.full(n, float(dt))
        dt = _vector(dt, "dt_hours")
        if len(dt) != n:
            raise ProblemError("dt_hours must have one entry per price")
        bad_price = np.flatnonzero(~np.isfinite(prices))
        if len(bad_price):
            raise ProblemError(
                f"{len(bad_price)} non-finite price(s), first at interval "
                f"{int(bad_price[0])}; missing prices must be resolved before "
                "optimisation (no silent filling)"
            )
        if not np.all(np.isfinite(dt)) or np.any(dt <= 0):
            raise ProblemError("interval durations must be finite and > 0 h")
        object.__setattr__(self, "prices_eur_per_mwh", prices)
        object.__setattr__(self, "dt_hours", dt)

        b = self.battery
        e0 = (
            b.initial_energy_mwh
            if self.e_start_mwh is None
            else float(self.e_start_mwh)
        )
        if not np.isfinite(e0) or not (
            b.soc_min_mwh - _E_START_TOL_MWH <= e0 <= b.soc_max_mwh + _E_START_TOL_MWH
        ):
            raise ProblemError(
                f"start energy {e0} MWh outside [{b.soc_min_mwh}, {b.soc_max_mwh}]"
            )
        object.__setattr__(
            self, "e_start_mwh", min(max(e0, b.soc_min_mwh), b.soc_max_mwh)
        )

        if self.availability is not None:
            avail = _vector(self.availability, "availability")
            if len(avail) != n:
                raise ProblemError("availability must have one entry per price")
            if not np.all(np.isfinite(avail)) or np.any((avail < 0) | (avail > 1)):
                raise ProblemError("availability must lie in [0, 1]")
            object.__setattr__(self, "availability", avail)
        if self.index is not None and len(self.index) != n:
            raise ProblemError("index length must match the number of prices")
        fee = float(self.trading_cost_eur_per_mwh)
        if not np.isfinite(fee) or fee < 0:
            raise ProblemError("trading_cost_eur_per_mwh must be finite and >= 0")
        object.__setattr__(self, "trading_cost_eur_per_mwh", fee)

    @classmethod
    def from_index(
        cls,
        battery: BatteryModel,
        index: pd.DatetimeIndex,
        end: pd.Timestamp,
        prices_eur_per_mwh: object,
        **kwargs: object,
    ) -> DispatchProblem:
        """Problem whose interval durations follow a tz-aware index."""
        try:
            dt = interval_hours_from_index(index, end)
        except ValueError as exc:
            raise ProblemError(str(exc)) from exc
        return cls(
            battery=battery,
            prices_eur_per_mwh=_vector(prices_eur_per_mwh, "prices_eur_per_mwh"),
            dt_hours=dt,
            index=index,
            **kwargs,  # type: ignore[arg-type]
        )

    @property
    def n_intervals(self) -> int:
        return len(self.prices_eur_per_mwh)

    @property
    def horizon_hours(self) -> float:
        return float(self.dt_hours.sum())

    @property
    def start_energy_mwh(self) -> float:
        assert self.e_start_mwh is not None  # resolved in __post_init__
        return self.e_start_mwh
