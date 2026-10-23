"""Battery asset parameters in solver-ready units.

:class:`BatteryModel` is the single physical description used by simulation,
feasibility checking and (from Stage 4) optimisation. It is derived from the
validated :class:`~gridflex.config.BatteryConfig` and repeats its invariant
checks so that hand-built models used in tests and sensitivity studies are
held to the same rules.

Units: power MW (AC, at the grid connection), energy MWh, efficiency one-way
fractions, cost EUR/MWh. See ``docs/MATHEMATICAL_MODEL.md`` §2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from gridflex.config.models import BatteryConfig, DegradationBasis, EfcBasis

TerminalPolicy = Literal["cyclic", "fixed", "free"]
FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class BatteryModel:
    charge_power_mw: float
    discharge_power_mw: float
    energy_capacity_mwh: float
    soc_min_mwh: float
    soc_max_mwh: float
    initial_energy_mwh: float
    eta_charge: float
    eta_discharge: float
    availability_fraction: float = 1.0
    max_efc_per_day: float | None = None
    efc_basis: EfcBasis = "dc_discharge_per_usable"
    terminal_policy: TerminalPolicy = "cyclic"
    terminal_energy_mwh: float | None = None
    terminal_energy_value_eur_per_mwh: float | None = None
    degradation_cost_eur_per_mwh: float = 0.0
    degradation_basis: DegradationBasis = "dc_discharge"

    def __post_init__(self) -> None:
        for name in ("charge_power_mw", "discharge_power_mw", "energy_capacity_mwh"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")
        for name in ("eta_charge", "eta_discharge", "availability_fraction"):
            if not 0.0 < getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in (0, 1]")
        if not 0.0 <= self.soc_min_mwh < self.soc_max_mwh <= self.energy_capacity_mwh:
            raise ValueError(
                "require 0 <= soc_min_mwh < soc_max_mwh <= energy_capacity_mwh"
            )
        if not self.soc_min_mwh <= self.initial_energy_mwh <= self.soc_max_mwh:
            raise ValueError("initial_energy_mwh must lie within the SoC bounds")
        if self.max_efc_per_day is not None and not self.max_efc_per_day > 0:
            raise ValueError("max_efc_per_day must be positive or None")
        if self.degradation_cost_eur_per_mwh < 0:
            raise ValueError("degradation_cost_eur_per_mwh must be non-negative")
        if self.terminal_policy == "fixed":
            if self.terminal_energy_mwh is None or not (
                self.soc_min_mwh <= self.terminal_energy_mwh <= self.soc_max_mwh
            ):
                raise ValueError(
                    "terminal policy 'fixed' needs terminal_energy_mwh within bounds"
                )
        elif self.terminal_energy_mwh is not None:
            raise ValueError("terminal_energy_mwh is only valid with policy 'fixed'")
        if self.terminal_policy == "free":
            if self.terminal_energy_value_eur_per_mwh is None:
                raise ValueError(
                    "terminal policy 'free' needs terminal_energy_value_eur_per_mwh"
                )
        elif self.terminal_energy_value_eur_per_mwh is not None:
            raise ValueError(
                "terminal_energy_value_eur_per_mwh is only valid with policy 'free'"
            )

    @classmethod
    def from_config(cls, cfg: BatteryConfig) -> BatteryModel:
        cap = cfg.energy_capacity_mwh
        return cls(
            charge_power_mw=cfg.charge_power_mw,
            discharge_power_mw=cfg.discharge_power_mw,
            energy_capacity_mwh=cap,
            soc_min_mwh=cfg.soc_min_mwh,
            soc_max_mwh=cfg.soc_max_mwh,
            initial_energy_mwh=cfg.soc_initial_mwh,
            eta_charge=cfg.efficiency.charge,
            eta_discharge=cfg.efficiency.discharge,
            availability_fraction=cfg.availability_fraction,
            max_efc_per_day=cfg.max_equivalent_full_cycles_per_day,
            efc_basis=cfg.efc_basis,
            terminal_policy=cfg.terminal_soc_policy,
            terminal_energy_mwh=(
                None
                if cfg.terminal_soc_fraction is None
                else cfg.terminal_soc_fraction * cap
            ),
            terminal_energy_value_eur_per_mwh=cfg.terminal_energy_value_eur_per_mwh,
            degradation_cost_eur_per_mwh=cfg.degradation.cost_eur_per_mwh,
            degradation_basis=cfg.degradation.cost_basis,
        )

    # ------------------------------------------------------------------ #
    # Derived quantities                                                  #
    # ------------------------------------------------------------------ #
    @property
    def usable_energy_mwh(self) -> float:
        return self.soc_max_mwh - self.soc_min_mwh

    @property
    def round_trip_efficiency(self) -> float:
        return self.eta_charge * self.eta_discharge

    @property
    def efc_reference_mwh(self) -> float:
        """Energy that counts as one full cycle under :attr:`efc_basis`."""
        if self.efc_basis == "dc_discharge_per_usable":
            return self.usable_energy_mwh
        return self.energy_capacity_mwh

    def power_limits_mw(
        self, n: int, availability: float | FloatArray | None = None
    ) -> tuple[FloatArray, FloatArray]:
        """Per-interval (charge, discharge) power limits in MW.

        ``availability`` (scalar or length-``n`` array in [0, 1]) replaces the
        configured ``availability_fraction`` when given.
        """
        a = self.availability_fraction if availability is None else availability
        avail = np.broadcast_to(np.asarray(a, dtype=np.float64), (n,)).copy()
        if not np.all(np.isfinite(avail)) or np.any((avail < 0) | (avail > 1)):
            raise ValueError("availability must lie in [0, 1]")
        return self.charge_power_mw * avail, self.discharge_power_mw * avail

    def cycle_limit_efc(self, horizon_hours: float) -> float | None:
        """EFC allowed over a horizon: the daily limit pro-rated to its length."""
        if self.max_efc_per_day is None:
            return None
        return self.max_efc_per_day * horizon_hours / 24.0

    def terminal_target_mwh(self, e_start_mwh: float) -> float | None:
        """Required end energy for a horizon starting at ``e_start_mwh``."""
        if self.terminal_policy == "cyclic":
            return e_start_mwh
        if self.terminal_policy == "fixed":
            return self.terminal_energy_mwh
        return None
