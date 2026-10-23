"""Feasibility checker: each violation code against a hand-built case.

Uses reference battery ``H`` from test_physics (5/4 MW, 1-9 MWh window,
start 2 MWh, eta 0.9/0.8).
"""

import math
from dataclasses import replace

import numpy as np
import pytest
from test_physics import IRREGULAR, H

from gridflex.battery import (
    BatteryModel,
    InfeasibleScheduleError,
    Schedule,
    check_schedule,
)


def _one(pc: float, pd_: float, dt: float = 1.0) -> Schedule:
    return Schedule([pc], [pd_], [dt])


def test_feasible_schedule_passes_with_trajectory() -> None:
    r = check_schedule(H, IRREGULAR)
    assert r.ok and r.summary() == "feasible"
    assert r.energy_mwh is not None and r.energy_mwh[-1] == pytest.approx(3.4125)
    assert r.throughput is not None and r.throughput.ac_charge_mwh == pytest.approx(4.0)
    r.raise_for_violations()  # no exception


def test_power_limits_and_negative_power() -> None:
    assert check_schedule(H, _one(5.0, 0.0)).ok  # exactly at the limit
    assert "CHARGE_POWER_LIMIT" in check_schedule(H, _one(5.01, 0.0)).codes()
    assert "DISCHARGE_POWER_LIMIT" in check_schedule(H, _one(0.0, 4.01, 0.1)).codes()
    assert "NEGATIVE_POWER" in check_schedule(H, _one(-0.5, 0.0)).codes()
    assert "NEGATIVE_POWER" in check_schedule(H, _one(0.0, -0.5)).codes()


def test_simultaneous_charge_and_discharge() -> None:
    r = check_schedule(H, _one(1.0, 1.0, 0.25))
    v = next(v for v in r.violations if v.code == "SIMULTANEOUS_CHARGE_DISCHARGE")
    assert v.worst == pytest.approx(1.0 - 1e-6)  # overlap beyond tolerance
    assert check_schedule(H, _one(1.0, 5e-7, 0.25)).ok  # within tolerance


def test_soc_bounds_hand_cases() -> None:
    # From 2 MWh: charging 5 MW for 1.5 h stores 0.9*5*1.5 = 6.75 -> 8.75 (ok);
    # for 2 h stores 9.0 -> 11.0 (2 MWh above the 9 MWh maximum).
    assert check_schedule(H, _one(5.0, 0.0, 1.5)).ok
    over = check_schedule(H, _one(5.0, 0.0, 2.0))
    v = next(v for v in over.violations if v.code == "SOC_ABOVE_MAX")
    assert v.worst == pytest.approx(2.0 - 1e-6)
    # Discharging 4 MW for 0.25 h removes 4*0.25/0.8 = 1.25 -> 0.75 (< 1 MWh).
    under = check_schedule(H, _one(0.0, 4.0, 0.25))
    v = next(v for v in under.violations if v.code == "SOC_BELOW_MIN")
    assert v.worst == pytest.approx(0.25 - 1e-6)
    assert v.first_interval == 0
    # Removing exactly 1 MWh (0.64 MW * 1.25 h / 0.8 = 1.0) lands on the bound.
    assert check_schedule(H, _one(0.0, 0.64, 1.25)).ok


def test_soc_tolerance_edge() -> None:
    # 9 MWh exactly is allowed; 2e-6 MWh beyond is not (tolerance 1e-6).
    exact = 7.0 / (0.9 * 5.0)  # hours to store 7 MWh at 5 MW
    assert check_schedule(H, _one(5.0, 0.0, exact)).ok
    over = exact + 2e-6 / (0.9 * 5.0)
    assert "SOC_ABOVE_MAX" in check_schedule(H, _one(5.0, 0.0, over)).codes()


def test_initial_energy_out_of_bounds() -> None:
    r = check_schedule(H, _one(0.0, 0.0), e_start_mwh=0.5)
    assert "INITIAL_ENERGY_OUT_OF_BOUNDS" in r.codes()
    assert "SOC_BELOW_MIN" in r.codes()


def test_terminal_policies() -> None:
    # Cyclic: charge 1 MWh AC, return 0.72 MWh AC -> back to 2 MWh.
    back = Schedule([1.0, 0.0], [0.0, 0.72], [1.0, 1.0])
    cyclic = replace(
        H, terminal_policy="cyclic", terminal_energy_value_eur_per_mwh=None
    )
    assert check_schedule(cyclic, back).ok
    assert "TERMINAL_ENERGY" in check_schedule(cyclic, _one(1.0, 0.0)).codes()
    # Cyclic is relative to the horizon's own start energy.
    assert check_schedule(cyclic, back, e_start_mwh=6.0).ok
    # Fixed: end at 2.9 MWh exactly after charging 1 MWh AC.
    fixed = replace(cyclic, terminal_policy="fixed", terminal_energy_mwh=2.9)
    assert check_schedule(fixed, _one(1.0, 0.0)).ok
    r = check_schedule(fixed, back)
    v = next(v for v in r.violations if v.code == "TERMINAL_ENERGY")
    assert v.worst == pytest.approx(0.9)
    # Free: no terminal requirement.
    assert check_schedule(H, _one(1.0, 0.0)).ok
    # Opt-out for intermediate checks.
    assert check_schedule(cyclic, _one(1.0, 0.0), check_terminal=False).ok


def test_cycle_limit_depends_on_basis() -> None:
    # Start 9 MWh; discharge 4 MW 1 h (-5), charge 4 MW 1 h (+3.6), discharge
    # 4 MW 1 h (-5): energy 4.0, 7.6, 2.6. Then 21 idle hours: 24 h horizon.
    s = Schedule([0, 4, 0] + [0] * 21, [4, 0, 4] + [0] * 21, 1.0)
    limited = replace(H, max_efc_per_day=1.0)
    # DC discharge = 8 / 0.8 = 10 MWh over usable 8 MWh -> 1.25 EFC > 1.0.
    r = check_schedule(limited, s, e_start_mwh=9.0)
    v = next(v for v in r.violations if v.code == "CYCLE_LIMIT")
    assert v.worst == pytest.approx(0.25)
    # AC discharge = 8 MWh over nameplate 10 MWh -> 0.8 EFC: within the limit.
    ac = replace(limited, efc_basis="ac_discharge_per_nameplate")
    assert check_schedule(ac, s, e_start_mwh=9.0).ok
    # Over a 12 h horizon the same discharge breaks a pro-rated 0.5 limit.
    short = Schedule([0, 4, 0] + [0] * 9, [4, 0, 4] + [0] * 9, 1.0)
    assert "CYCLE_LIMIT" in check_schedule(ac, short, e_start_mwh=9.0).codes()
    assert check_schedule(limited, s, e_start_mwh=9.0, check_cycles=False).ok


def test_availability_profile_and_scalar() -> None:
    s = Schedule([4.0, 4.0], [0.0, 0.0], [0.25, 0.25])
    assert check_schedule(H, s).ok
    r = check_schedule(H, s, availability=np.array([1.0, 0.5]))  # limit 2.5 MW at t1
    v = next(v for v in r.violations if v.code == "CHARGE_POWER_LIMIT")
    assert v.first_interval == 1 and v.worst == pytest.approx(1.5 - 1e-6)
    derated = replace(H, availability_fraction=0.7)  # 3.5 MW
    assert "CHARGE_POWER_LIMIT" in check_schedule(derated, s).codes()
    assert check_schedule(derated, s, availability=1.0).ok  # explicit override
    with pytest.raises(ValueError):
        check_schedule(H, s, availability=np.array([1.0, 1.2]))


def test_non_finite_and_bad_durations() -> None:
    assert check_schedule(H, _one(math.nan, 0.0)).codes() == {"NON_FINITE"}
    assert check_schedule(H, _one(0.0, math.inf)).codes() == {"NON_FINITE"}
    assert check_schedule(H, _one(0.0, 0.0), e_start_mwh=math.nan).codes() == {
        "NON_FINITE"
    }
    assert check_schedule(H, _one(1.0, 0.0, 0.0)).codes() == {"BAD_INTERVAL_DURATION"}
    assert check_schedule(H, _one(1.0, 0.0, -1.0)).codes() == {"BAD_INTERVAL_DURATION"}


def test_raise_for_violations() -> None:
    r = check_schedule(H, _one(9.0, 0.0))
    with pytest.raises(InfeasibleScheduleError, match="CHARGE_POWER_LIMIT") as exc:
        r.raise_for_violations()
    assert exc.value.report is r


@pytest.mark.parametrize(
    "changes",
    [
        {"charge_power_mw": 0.0},
        {"eta_charge": 0.0},
        {"eta_discharge": 1.1},
        {"availability_fraction": 0.0},
        {"soc_min_mwh": 9.5},
        {"soc_max_mwh": 11.0},
        {"initial_energy_mwh": 0.5},
        {"max_efc_per_day": 0.0},
        {"degradation_cost_eur_per_mwh": -1.0},
        {"terminal_energy_value_eur_per_mwh": None},  # free needs a value
        {"terminal_policy": "fixed", "terminal_energy_value_eur_per_mwh": None},
        {
            "terminal_policy": "fixed",
            "terminal_energy_value_eur_per_mwh": None,
            "terminal_energy_mwh": 9.5,
        },
        {"terminal_policy": "cyclic"},  # value given without policy 'free'
        {"terminal_energy_mwh": 3.0},  # target given without policy 'fixed'
    ],
)
def test_invalid_models_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(H, **changes)


def test_model_is_hashable_and_frozen() -> None:
    assert isinstance(hash(H), int)
    with pytest.raises(AttributeError):
        H.eta_charge = 0.5  # type: ignore[misc]
    assert isinstance(H, BatteryModel)
