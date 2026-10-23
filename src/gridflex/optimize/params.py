"""Flat parameter set consumed by the v0.1 dispatch optimiser and backtest.

This is the former ``bess.config.BatteryConfig``, kept as a thin bridge so the
existing optimiser keeps working while configuration moves to the validated
``gridflex.config`` models. It is built from a :class:`GridFlexConfig` via
:meth:`DispatchParams.from_config`, and will be retired when the optimiser is
rewritten (Stages 3-4).

Efficiencies are one-way: ``eta_charge * eta_discharge`` is the round trip.

The v0.1 MILP charges degradation and caps cycling on *AC discharge* and
counts cycles against *nameplate* energy. :meth:`from_config` converts the
configured conventions exactly into that form:

* degradation on ``dc_discharge``: DC discharge = AC discharge / eta_dis, so
  ``c_deg_ac = c_deg_dc / eta_dis``;
* cycles on ``dc_discharge_per_usable``: ``N`` EFC per day allows
  ``N * usable * eta_dis`` MWh of AC discharge, i.e.
  ``max_cycles_per_day = N * usable * eta_dis / nameplate`` in v0.1 units;
* ``availability_fraction`` derates the single v0.1 power limit.

Fields therefore carry v0.1 semantics (AC discharge, nameplate cycles); the
independent checker in :mod:`gridflex.battery` verifies the equivalence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from gridflex.config import ConfigError, GridFlexConfig


@dataclass(frozen=True)
class DispatchParams:
    """Immutable battery parameters for the v0.1 MILP. Powers MW, energy MWh."""

    power_mw: float
    energy_mwh: float
    eta_charge: float
    eta_discharge: float
    soc_min_frac: float
    soc_max_frac: float
    soc_init_frac: float
    max_cycles_per_day: float
    c_deg_eur_per_mwh: float
    delta_t_hours: float = 1.0
    capex_eur_per_kwh: float | None = None

    def __post_init__(self) -> None:
        for name in ("eta_charge", "eta_discharge"):
            if not 0.0 < getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in (0, 1].")
        if not 0.0 <= self.soc_min_frac < self.soc_max_frac <= 1.0:
            raise ValueError("require 0 <= soc_min_frac < soc_max_frac <= 1.")
        if not self.soc_min_frac <= self.soc_init_frac <= self.soc_max_frac:
            raise ValueError("soc_init_frac must lie within the SoC bounds.")
        if self.power_mw <= 0 or self.energy_mwh <= 0:
            raise ValueError("power_mw and energy_mwh must be positive.")
        if self.c_deg_eur_per_mwh < 0:
            raise ValueError("c_deg_eur_per_mwh must be non-negative.")
        if self.max_cycles_per_day <= 0:
            raise ValueError("max_cycles_per_day must be positive (use inf for none).")
        if self.delta_t_hours <= 0:
            raise ValueError("delta_t_hours must be positive.")

    @classmethod
    def from_config(
        cls, cfg: GridFlexConfig, delta_t_hours: float = 1.0
    ) -> DispatchParams:
        """Translate the validated configuration into v0.1 optimiser inputs.

        Raises :class:`ConfigError` for settings the v0.1 optimiser cannot
        represent (asymmetric power, non-cyclic terminal policy).
        """
        b = cfg.battery
        eta_dis = b.efficiency.discharge
        if b.charge_power_mw != b.discharge_power_mw:
            raise ConfigError(
                "the v0.1 optimiser needs charge_power_mw == discharge_power_mw; "
                "asymmetric limits arrive with the Stage 4 optimiser"
            )
        if b.terminal_soc_policy != "cyclic":
            raise ConfigError(
                "the v0.1 backtest supports terminal_soc_policy 'cyclic' only"
            )
        cycles = b.max_equivalent_full_cycles_per_day
        if cycles is None:
            max_cycles = math.inf
        elif b.efc_basis == "dc_discharge_per_usable":
            usable = b.soc_max_mwh - b.soc_min_mwh
            max_cycles = cycles * usable * eta_dis / b.energy_capacity_mwh
        else:
            max_cycles = cycles
        c_deg = b.degradation.cost_eur_per_mwh
        if b.degradation.cost_basis == "dc_discharge":
            c_deg = c_deg / eta_dis
        return cls(
            power_mw=b.charge_power_mw * b.availability_fraction,
            energy_mwh=b.energy_capacity_mwh,
            eta_charge=b.efficiency.charge,
            eta_discharge=eta_dis,
            soc_min_frac=b.soc_min_fraction,
            soc_max_frac=b.soc_max_fraction,
            soc_init_frac=b.soc_initial_fraction,
            max_cycles_per_day=max_cycles,
            c_deg_eur_per_mwh=c_deg,
            delta_t_hours=delta_t_hours,
            capex_eur_per_kwh=cfg.economics.capex_eur_per_kwh,
        )

    # ------------------------------------------------------------------ #
    # Derived quantities (names kept from v0.1)                           #
    # ------------------------------------------------------------------ #
    @property
    def eta_ch(self) -> float:
        return self.eta_charge

    @property
    def eta_dis(self) -> float:
        return self.eta_discharge

    @property
    def round_trip_efficiency(self) -> float:
        return self.eta_charge * self.eta_discharge

    @property
    def e_min(self) -> float:
        """Lower state-of-charge bound in MWh."""
        return self.soc_min_frac * self.energy_mwh

    @property
    def e_max(self) -> float:
        """Upper state-of-charge bound in MWh."""
        return self.soc_max_frac * self.energy_mwh

    @property
    def e_init(self) -> float:
        """Initial state of charge in MWh."""
        return self.soc_init_frac * self.energy_mwh

    @property
    def usable_energy_mwh(self) -> float:
        return self.e_max - self.e_min

    @property
    def daily_cycle_energy_mwh(self) -> float:
        """Max discharge energy per day from the cycle limit (MWh)."""
        return self.max_cycles_per_day * self.energy_mwh

    @property
    def duration_hours(self) -> float:
        return self.energy_mwh / self.power_mw
