"""Typed configuration models (pydantic v2).

Every physical, market and economic assumption is declared here once, with its
unit in the field name, and validated on load. Models are frozen and reject
unknown keys, so a typo in a YAML file fails loudly instead of silently
falling back to a default.

Conventions fixed by this module (see ``docs/DATA_DICTIONARY.md``):

* Power limits are measured at the AC grid connection.
* Efficiencies are *one-way* (charge, discharge). A round-trip figure may be
  supplied instead; it is then split symmetrically as ``sqrt(RTE)`` per leg and
  never applied twice.
* State-of-charge bounds are fractions of nameplate energy capacity.
* Calendar quantities (delivery days, gate closure, regime start dates) are in
  the market timezone; data timestamps are UTC (see ``gridflex.data``).
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveFloat = Annotated[float, Field(gt=0.0)]
Efficiency = Annotated[float, Field(gt=0.0, le=1.0)]


class StrictModel(BaseModel):
    """Base: immutable, unknown keys rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# Battery                                                                     #
# --------------------------------------------------------------------------- #
class RoundTripEfficiency(StrictModel):
    """AC-to-AC round-trip efficiency, split symmetrically across the two legs."""

    mode: Literal["round_trip_symmetric"]
    round_trip: Efficiency

    @property
    def charge(self) -> float:
        return math.sqrt(self.round_trip)

    @property
    def discharge(self) -> float:
        return math.sqrt(self.round_trip)


class OneWayEfficiency(StrictModel):
    """Separate one-way charge and discharge efficiencies (AC grid to stored energy)."""

    mode: Literal["one_way"]
    charge: Efficiency
    discharge: Efficiency

    @property
    def round_trip(self) -> float:
        return self.charge * self.discharge


EfficiencyConfig = Annotated[
    RoundTripEfficiency | OneWayEfficiency, Field(discriminator="mode")
]


DegradationBasis = Literal["dc_discharge", "ac_discharge"]
EfcBasis = Literal["dc_discharge_per_usable", "ac_discharge_per_nameplate"]


class DegradationConfig(StrictModel):
    """Throughput-based degradation cost inputs.

    ``cost_eur_per_mwh = replacement_cost_eur_per_kwh * 1000
    / (cycle_life_cycles * depth_of_discharge)``, charged per MWh measured on
    ``cost_basis``:

    * ``dc_discharge`` (default): energy leaving the cells,
      ``P_discharge * dt / eta_discharge``. Consistent with a cell cycle-life
      rating stated at a depth of discharge (audit D11).
    * ``ac_discharge``: energy delivered at the grid connection (v0.1
      convention), ``P_discharge * dt``.
    """

    replacement_cost_eur_per_kwh: Annotated[float, Field(ge=0.0)]
    cycle_life_cycles: PositiveFloat
    depth_of_discharge: Annotated[float, Field(gt=0.0, le=1.0)]
    cost_basis: DegradationBasis = "dc_discharge"
    source: str = Field(
        default="unsourced assumption",
        description="Citation or justification for the inputs.",
    )

    @property
    def cost_eur_per_mwh(self) -> float:
        throughput_per_kwh = self.cycle_life_cycles * self.depth_of_discharge
        return self.replacement_cost_eur_per_kwh * 1000.0 / throughput_per_kwh


class BatteryConfig(StrictModel):
    """Physical battery asset. Powers in MW (AC), energy in MWh."""

    charge_power_mw: PositiveFloat
    discharge_power_mw: PositiveFloat
    energy_capacity_mwh: PositiveFloat
    soc_min_fraction: Fraction
    soc_max_fraction: Fraction
    soc_initial_fraction: Fraction
    efficiency: EfficiencyConfig
    max_equivalent_full_cycles_per_day: Annotated[float, Field(gt=0.0)] | None = None
    efc_basis: EfcBasis = "dc_discharge_per_usable"
    availability_fraction: Annotated[float, Field(gt=0.0, le=1.0)] = 1.0
    terminal_soc_policy: Literal["cyclic", "fixed", "free"] = "cyclic"
    terminal_soc_fraction: Fraction | None = None
    terminal_energy_value_eur_per_mwh: float | None = None
    power_measurement_point: Literal["ac_grid_connection"] = "ac_grid_connection"
    degradation: DegradationConfig

    @model_validator(mode="after")
    def _check_soc(self) -> BatteryConfig:
        if not self.soc_min_fraction < self.soc_max_fraction:
            raise ValueError("require soc_min_fraction < soc_max_fraction")
        if (
            not self.soc_min_fraction
            <= self.soc_initial_fraction
            <= self.soc_max_fraction
        ):
            raise ValueError("soc_initial_fraction must lie within the SoC bounds")
        if self.terminal_soc_policy == "fixed":
            if self.terminal_soc_fraction is None:
                raise ValueError(
                    "terminal_soc_policy 'fixed' needs terminal_soc_fraction"
                )
            if not (
                self.soc_min_fraction
                <= self.terminal_soc_fraction
                <= self.soc_max_fraction
            ):
                raise ValueError("terminal_soc_fraction must lie within the SoC bounds")
        elif self.terminal_soc_fraction is not None:
            raise ValueError(
                "terminal_soc_fraction is only allowed with terminal_soc_policy 'fixed'"
            )
        if self.terminal_soc_policy == "free":
            if self.terminal_energy_value_eur_per_mwh is None:
                raise ValueError(
                    "terminal_soc_policy 'free' needs an explicit "
                    "terminal_energy_value_eur_per_mwh (stored energy is otherwise "
                    "worth nothing and the optimiser would sell it for free)"
                )
        elif self.terminal_energy_value_eur_per_mwh is not None:
            raise ValueError(
                "terminal_energy_value_eur_per_mwh is only allowed with "
                "terminal_soc_policy 'free'"
            )
        return self

    @property
    def soc_min_mwh(self) -> float:
        return self.soc_min_fraction * self.energy_capacity_mwh

    @property
    def soc_max_mwh(self) -> float:
        return self.soc_max_fraction * self.energy_capacity_mwh

    @property
    def soc_initial_mwh(self) -> float:
        return self.soc_initial_fraction * self.energy_capacity_mwh

    @property
    def usable_energy_mwh(self) -> float:
        return self.soc_max_mwh - self.soc_min_mwh


# --------------------------------------------------------------------------- #
# Market                                                                      #
# --------------------------------------------------------------------------- #
Resolution = Literal["hour", "quarterhour"]
RESOLUTION_MINUTES: dict[str, int] = {"hour": 60, "quarterhour": 15}


class MtuRegime(StrictModel):
    """Day-ahead market time unit in force from ``start_delivery_day`` (local)."""

    start_delivery_day: dt.date
    resolution: Resolution
    source: str


class PriceLimit(StrictModel):
    """Harmonised day-ahead clearing-price bounds from ``start_delivery_day``."""

    start_delivery_day: dt.date
    min_eur_per_mwh: float
    max_eur_per_mwh: float
    source: str

    @model_validator(mode="after")
    def _ordered(self) -> PriceLimit:
        if not self.min_eur_per_mwh < self.max_eur_per_mwh:
            raise ValueError("min_eur_per_mwh must be below max_eur_per_mwh")
        return self


def _sorted_unique_starts(items: list[MtuRegime] | list[PriceLimit]) -> None:
    starts = [i.start_delivery_day for i in items]
    if not items:
        raise ValueError("at least one entry is required")
    if starts != sorted(starts) or len(set(starts)) != len(starts):
        raise ValueError("entries must have strictly increasing start_delivery_day")


class MarketConfig(StrictModel):
    """Bidding zone, clock and day-ahead market rules."""

    bidding_zone: Literal["DE-LU"] = "DE-LU"
    timezone: str = "Europe/Berlin"
    day_ahead_gate_closure_local: dt.time = dt.time(12, 0)
    mtu_regimes: list[MtuRegime]
    price_limits: list[PriceLimit]

    @field_validator("timezone")
    @classmethod
    def _known_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {value!r}") from exc
        return value

    @field_validator("mtu_regimes", "price_limits")
    @classmethod
    def _ordered(
        cls, value: list[MtuRegime] | list[PriceLimit]
    ) -> list[MtuRegime] | list[PriceLimit]:
        _sorted_unique_starts(value)
        return value

    def regime_for(self, delivery_day: dt.date) -> MtuRegime:
        """MTU regime in force on a local delivery day."""
        return _entry_for(self.mtu_regimes, delivery_day)

    def price_limit_for(self, delivery_day: dt.date) -> PriceLimit:
        """Clearing-price bounds in force on a local delivery day."""
        return _entry_for(self.price_limits, delivery_day)


def _entry_for[T: (MtuRegime, PriceLimit)](items: list[T], day: dt.date) -> T:
    eligible = [i for i in items if i.start_delivery_day <= day]
    if not eligible:
        raise ValueError(f"no entry configured for delivery day {day}")
    return eligible[-1]


# --------------------------------------------------------------------------- #
# Data sources                                                                #
# --------------------------------------------------------------------------- #
class AvailabilityRule(StrictModel):
    """When values for a local delivery day D become public.

    Available from ``(D + day_offset) at local_time`` in the market timezone.
    Example: ENTSO-E day-ahead wind/solar forecasts -> ``day_offset=-1``,
    ``local_time=18:00``.
    """

    day_offset: int
    local_time: dt.time
    source: str


class SeriesConfig(StrictModel):
    """One SMARD chart-data series and how to interpret it."""

    smard_id: Annotated[int, Field(gt=0)]
    description: str
    kind: Literal["price", "energy"]
    raw_unit: Literal["EUR/MWh", "MWh per interval"]
    nature: Literal["realised", "forecast"]
    availability: AvailabilityRule
    validation_min: float | None = None
    validation_max: float | None = None

    @model_validator(mode="after")
    def _unit_matches_kind(self) -> SeriesConfig:
        expected = {"price": "EUR/MWh", "energy": "MWh per interval"}[self.kind]
        if self.raw_unit != expected:
            raise ValueError(f"kind {self.kind!r} requires raw_unit {expected!r}")
        return self

    @property
    def clean_unit(self) -> str:
        """Unit after cleaning: prices unchanged, energy converted to mean MW."""
        return "EUR/MWh" if self.kind == "price" else "MW"


class CachePolicy(StrictModel):
    """Raw-cache invalidation rules (see ``gridflex.data.cache``)."""

    index_max_age_minutes: Annotated[float, Field(ge=0.0)] = 60.0
    open_chunk_max_age_minutes: Annotated[float, Field(ge=0.0)] = 360.0
    finalisation_lag_days: Annotated[float, Field(ge=0.0)] = 14.0


class HttpConfig(StrictModel):
    timeout_s: PositiveFloat = 30.0
    max_retries: Annotated[int, Field(ge=0, le=10)] = 3
    backoff_s: Annotated[float, Field(ge=0.0)] = 1.0
    backoff_max_s: Annotated[float, Field(ge=0.0)] = 30.0
    min_interval_s: Annotated[float, Field(ge=0.0)] = 0.05


class DataConfig(StrictModel):
    """Where data comes from, how it is cached, and what each series means."""

    smard_base_url: str = "https://www.smard.de/app/chart_data"
    smard_region: Literal["DE"] = "DE"
    cache_dir: str = "data/cache"
    offline: bool = False
    http: HttpConfig = HttpConfig()
    cache: CachePolicy = CachePolicy()
    series: dict[str, SeriesConfig]

    @model_validator(mode="after")
    def _price_present(self) -> DataConfig:
        if "price" not in self.series or self.series["price"].kind != "price":
            raise ValueError("data.series must define a 'price' series of kind 'price'")
        return self


# --------------------------------------------------------------------------- #
# Optimisation                                                                #
# --------------------------------------------------------------------------- #
class OptimizationConfig(StrictModel):
    """Solver settings for the dispatch MILP (see ``gridflex.optimization``).

    A solve counts as successful only when the solver proves optimality within
    ``mip_rel_gap``. Time-limited incumbents are rejected unless
    ``accept_time_limited`` is set, and even then only within
    ``max_accepted_gap``. Every accepted schedule is re-verified by the
    independent battery feasibility checker using the tolerances below.
    """

    solver: Literal["highs", "cbc"] = "highs"
    time_limit_s: PositiveFloat | None = 60.0
    mip_rel_gap: Annotated[float, Field(ge=0.0, le=1.0)] = 1e-6
    threads: Annotated[int, Field(ge=1)] = 1
    accept_time_limited: bool = False
    max_accepted_gap: Annotated[float, Field(ge=0.0, le=1.0)] = 1e-3
    feasibility_tol_mw: Annotated[float, Field(gt=0.0)] = 1e-6
    feasibility_tol_mwh: Annotated[float, Field(gt=0.0)] = 1e-6
    solver_log: bool = False


# --------------------------------------------------------------------------- #
# Economics                                                                   #
# --------------------------------------------------------------------------- #
class EconomicsConfig(StrictModel):
    """Investment inputs. Investment metrics are only computed when supplied."""

    capex_eur_per_kwh: Annotated[float, Field(gt=0.0)] | None = None


# --------------------------------------------------------------------------- #
# Root                                                                        #
# --------------------------------------------------------------------------- #
class GridFlexConfig(StrictModel):
    schema_version: Literal[1] = 1
    battery: BatteryConfig
    market: MarketConfig
    data: DataConfig
    optimization: OptimizationConfig = OptimizationConfig()
    economics: EconomicsConfig = EconomicsConfig()
