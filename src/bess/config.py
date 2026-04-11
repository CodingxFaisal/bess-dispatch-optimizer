"""Typed configuration loaded from ``config/battery.yaml``.

Every physical and economic parameter the model uses is resolved here once,
so the optimizer, backtester and analysis code never see a raw magic number.
Derived quantities (charge/discharge efficiency, energy bounds, degradation
cost) are computed in one place and reused everywhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class BatteryConfig:
    """Immutable, fully-derived battery + market configuration.

    Attributes are grouped as: raw battery spec, derived quantities, market
    settings and economic assumptions. Construct via :func:`load_config`.
    """

    # --- raw battery spec -------------------------------------------------
    power_mw: float
    energy_mwh: float
    round_trip_efficiency: float
    soc_min_frac: float
    soc_max_frac: float
    soc_init_frac: float
    max_cycles_per_day: float

    # --- degradation inputs ----------------------------------------------
    replacement_cost_eur_per_kwh: float
    cycle_life_cycles: float
    depth_of_discharge: float

    # --- market ----------------------------------------------------------
    timezone: str
    delta_t_hours: float
    smard_region: str
    smard_series: dict = field(default_factory=dict)

    # --- economics -------------------------------------------------------
    capex_eur_per_kwh: float = 250.0

    # ------------------------------------------------------------------ #
    # Derived quantities                                                   #
    # ------------------------------------------------------------------ #
    @property
    def eta_ch(self) -> float:
        """Charge efficiency = sqrt(RTE).

        Round-trip efficiency is the product of the one-way charge and
        discharge efficiencies. Splitting it symmetrically as a square root
        is the standard neutral assumption when the datasheet only quotes the
        round-trip figure: eta_ch * eta_dis = RTE, eta_ch = eta_dis = sqrt(RTE).
        """
        return math.sqrt(self.round_trip_efficiency)

    @property
    def eta_dis(self) -> float:
        """Discharge efficiency = sqrt(RTE). See :attr:`eta_ch`."""
        return math.sqrt(self.round_trip_efficiency)

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
        """Energy between the SoC bounds (the range actually cyclable)."""
        return self.e_max - self.e_min

    @property
    def c_deg_eur_per_mwh(self) -> float:
        """Marginal degradation cost per MWh *discharged* (EUR/MWh).

        Derived from replacement cost, cycle life and rated depth of
        discharge -- see ``config/battery.yaml`` for the full reasoning.
        """
        throughput_per_kwh = self.cycle_life_cycles * self.depth_of_discharge
        return self.replacement_cost_eur_per_kwh * 1000.0 / throughput_per_kwh

    @property
    def daily_cycle_energy_mwh(self) -> float:
        """Max discharge energy per day from the cycle limit (MWh)."""
        return self.max_cycles_per_day * self.energy_mwh

    def __post_init__(self) -> None:
        # Fail loudly on nonsensical configs rather than producing a silently
        # wrong optimization later.
        if not 0.0 < self.round_trip_efficiency <= 1.0:
            raise ValueError("round_trip_efficiency must be in (0, 1].")
        if not 0.0 <= self.soc_min_frac < self.soc_max_frac <= 1.0:
            raise ValueError("require 0 <= soc_min_frac < soc_max_frac <= 1.")
        if not self.soc_min_frac <= self.soc_init_frac <= self.soc_max_frac:
            raise ValueError("soc_init_frac must lie within the SoC bounds.")
        if self.power_mw <= 0 or self.energy_mwh <= 0:
            raise ValueError("power_mw and energy_mwh must be positive.")

    @property
    def duration_hours(self) -> float:
        """Storage duration = energy / power (hours)."""
        return self.energy_mwh / self.power_mw


def load_config(path: str | Path = "config/battery.yaml") -> BatteryConfig:
    """Load and validate :class:`BatteryConfig` from a YAML file."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    bat = raw["battery"]
    deg = raw["degradation"]
    mkt = raw["market"]
    eco = raw.get("economics", {})

    return BatteryConfig(
        power_mw=float(bat["power_mw"]),
        energy_mwh=float(bat["energy_mwh"]),
        round_trip_efficiency=float(bat["round_trip_efficiency"]),
        soc_min_frac=float(bat["soc_min_frac"]),
        soc_max_frac=float(bat["soc_max_frac"]),
        soc_init_frac=float(bat["soc_init_frac"]),
        max_cycles_per_day=float(bat["max_cycles_per_day"]),
        replacement_cost_eur_per_kwh=float(deg["replacement_cost_eur_per_kwh"]),
        cycle_life_cycles=float(deg["cycle_life_cycles"]),
        depth_of_discharge=float(deg["depth_of_discharge"]),
        timezone=str(mkt["timezone"]),
        delta_t_hours=float(mkt["delta_t_hours"]),
        smard_region=str(mkt["smard_region"]),
        smard_series=dict(mkt["smard_series"]),
        capex_eur_per_kwh=float(eco.get("capex_eur_per_kwh", 250.0)),
    )
