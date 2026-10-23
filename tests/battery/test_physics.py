"""State equation and throughput accounting against hand calculations.

Reference battery ``H`` (all numbers chosen so results are exact decimals):
charge limit 5 MW, discharge limit 4 MW, nameplate 10 MWh, SoC window
1-9 MWh (usable 8 MWh), start 2 MWh, eta_charge 0.9, eta_discharge 0.8
(round trip 0.72), degradation 10 EUR/MWh.
"""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from gridflex.battery import (
    BatteryModel,
    Schedule,
    energy_trajectory,
    interval_hours_from_index,
    throughput,
)
from gridflex.config import GridFlexConfig

H = BatteryModel(
    charge_power_mw=5.0,
    discharge_power_mw=4.0,
    energy_capacity_mwh=10.0,
    soc_min_mwh=1.0,
    soc_max_mwh=9.0,
    initial_energy_mwh=2.0,
    eta_charge=0.9,
    eta_discharge=0.8,
    terminal_policy="free",
    terminal_energy_value_eur_per_mwh=0.0,
    degradation_cost_eur_per_mwh=10.0,
    degradation_basis="dc_discharge",
)

# Irregular intervals: 30 min, 15 min, 60 min, 15 min.
IRREGULAR = Schedule(
    charge_mw=[4.0, 0.0, 2.0, 0.0],
    discharge_mw=[0.0, 3.0, 0.0, 4.0],
    dt_hours=[0.5, 0.25, 1.0, 0.25],
)


def test_state_equation_irregular_intervals_asymmetric_efficiency() -> None:
    # t0: 2 + 0.9*4*0.5       = 2 + 1.8    = 3.8
    # t1: 3.8 - 3*0.25/0.8    = 3.8 - 0.9375 = 2.8625
    # t2: 2.8625 + 0.9*2*1.0  = 2.8625 + 1.8 = 4.6625
    # t3: 4.6625 - 4*0.25/0.8 = 4.6625 - 1.25 = 3.4125
    e = energy_trajectory(H, IRREGULAR)
    assert e.tolist() == pytest.approx([3.8, 2.8625, 4.6625, 3.4125], abs=1e-12)


def test_explicit_start_energy_overrides_initial() -> None:
    e = energy_trajectory(H, IRREGULAR, e_start_mwh=5.0)
    assert e[-1] == pytest.approx(3.4125 + 3.0)


def test_throughput_hand_values() -> None:
    f = throughput(H, IRREGULAR)
    assert f.ac_charge_mwh == pytest.approx(4.0)  # 4*0.5 + 2*1.0
    assert f.ac_discharge_mwh == pytest.approx(1.75)  # 3*0.25 + 4*0.25
    assert f.dc_charge_mwh == pytest.approx(3.6)  # 0.9 * 4.0
    assert f.dc_discharge_mwh == pytest.approx(2.1875)  # 1.75 / 0.8
    assert f.conversion_losses_mwh == pytest.approx(0.8375)  # 0.4 + 0.4375
    assert f.stored_energy_change_mwh == pytest.approx(1.4125)  # 3.4125 - 2
    assert f.efc_dc_per_usable == pytest.approx(0.2734375)  # 2.1875 / 8
    assert f.efc_ac_per_nameplate == pytest.approx(0.175)  # 1.75 / 10
    assert f.efc == pytest.approx(0.2734375)  # configured basis
    assert f.degradation_throughput_mwh == pytest.approx(2.1875)
    assert f.degradation_cost_eur == pytest.approx(21.875)  # 10 * 2.1875


def test_ac_bases_hand_values() -> None:
    h_ac = replace(
        H, degradation_basis="ac_discharge", efc_basis="ac_discharge_per_nameplate"
    )
    f = throughput(h_ac, IRREGULAR)
    assert f.efc == pytest.approx(0.175)
    assert f.degradation_cost_eur == pytest.approx(17.5)  # 10 * 1.75


def test_energy_balance_identity() -> None:
    # AC in - AC out = change in stored energy + conversion losses.
    f = throughput(H, IRREGULAR)
    assert f.ac_charge_mwh - f.ac_discharge_mwh == pytest.approx(
        f.stored_energy_change_mwh + f.conversion_losses_mwh
    )
    # 4.0 - 1.75 = 2.25 = 1.4125 + 0.8375
    assert f.ac_charge_mwh - f.ac_discharge_mwh == pytest.approx(2.25)


def test_energy_balance_identity_random_schedules() -> None:
    rng = np.random.default_rng(3)
    for _ in range(200):
        n = int(rng.integers(1, 50))
        s = Schedule(
            rng.uniform(0, 5, n), rng.uniform(0, 4, n), rng.uniform(0.05, 2, n)
        )
        f = throughput(H, s)
        e = energy_trajectory(H, s)
        assert e[-1] - H.initial_energy_mwh == pytest.approx(
            f.stored_energy_change_mwh, abs=1e-9
        )
        assert f.ac_charge_mwh - f.ac_discharge_mwh == pytest.approx(
            f.stored_energy_change_mwh + f.conversion_losses_mwh, abs=1e-9
        )
        assert f.conversion_losses_mwh >= 0


def test_round_trip_efficiency_applied_once() -> None:
    # Charge 1 MWh AC -> 0.9 MWh stored; returning to the start energy
    # delivers 0.9 * 0.8 = 0.72 MWh AC, i.e. exactly the round-trip efficiency.
    s = Schedule([1.0, 0.0], [0.0, 0.72], [1.0, 1.0])
    e = energy_trajectory(H, s)
    assert e.tolist() == pytest.approx([2.9, 2.0])
    f = throughput(H, s)
    assert f.ac_discharge_mwh / f.ac_charge_mwh == pytest.approx(
        H.round_trip_efficiency
    )


def test_lossless_battery_is_reversible() -> None:
    lossless = replace(H, eta_charge=1.0, eta_discharge=1.0)
    s = Schedule([4.0, 0.0], [0.0, 4.0], [1.5, 1.5])
    assert energy_trajectory(lossless, s).tolist() == pytest.approx([8.0, 2.0])
    assert throughput(lossless, s).conversion_losses_mwh == pytest.approx(0.0)


def test_default_config_model(gf_cfg: GridFlexConfig) -> None:
    b = BatteryModel.from_config(gf_cfg.battery)
    assert (b.soc_min_mwh, b.soc_max_mwh, b.initial_energy_mwh) == (1.0, 19.0, 10.0)
    assert b.usable_energy_mwh == pytest.approx(18.0)
    assert b.efc_reference_mwh == pytest.approx(18.0)
    assert b.round_trip_efficiency == pytest.approx(0.86)
    assert b.degradation_cost_eur_per_mwh == pytest.approx(120_000 / 7_200)
    assert b.degradation_basis == "dc_discharge"
    assert b.cycle_limit_efc(24.0) == pytest.approx(1.5)
    # Default 10 MW for 1 h: 10 * 0.92736 = 9.2736 MWh stored.
    e = energy_trajectory(b, Schedule([10.0], [0.0], [1.0]))
    assert e[0] == pytest.approx(10.0 + 9.273618495495703)


# --------------------------------------------------------------------------- #
# Interval durations from timestamps                                          #
# --------------------------------------------------------------------------- #
def test_interval_hours_irregular_index() -> None:
    idx = pd.DatetimeIndex(
        ["2024-06-03 00:00", "2024-06-03 00:15", "2024-06-03 01:00"], tz="UTC"
    )
    hours = interval_hours_from_index(idx, pd.Timestamp("2024-06-03 03:00", tz="UTC"))
    assert hours.tolist() == pytest.approx([0.25, 0.75, 2.0])


@pytest.mark.parametrize(
    ("day", "n_hours"),
    [("2024-03-31", 23), ("2024-10-27", 25), ("2024-06-03", 24)],
)
def test_dst_day_horizon(day: str, n_hours: int) -> None:
    start = pd.Timestamp(day).tz_localize("Europe/Berlin")
    end = start + pd.offsets.Day(1)  # calendar day in local time
    idx = pd.date_range(start, end, freq="h", inclusive="left").tz_convert("UTC")
    s = Schedule.from_index(
        idx, end.tz_convert("UTC"), np.zeros(len(idx)), np.zeros(len(idx))
    )
    assert s.n_intervals == n_hours
    assert s.horizon_hours == pytest.approx(n_hours)
    assert np.all(s.dt_hours == 1.0)
    # Daily cycle limit pro-rated to the horizon: 1.5 * 25 / 24 = 1.5625 on 27 Oct.
    b = replace(H, max_efc_per_day=1.5)
    assert b.cycle_limit_efc(s.horizon_hours) == pytest.approx(1.5 * n_hours / 24)


def test_quarter_hour_index() -> None:
    idx = pd.date_range("2025-10-01 00:00", periods=4, freq="15min", tz="Europe/Berlin")
    s = Schedule.from_index(
        idx, idx[-1] + pd.Timedelta(minutes=15), [4.0, 4.0, 0, 0], [0, 0, 0, 0]
    )
    assert s.dt_hours.tolist() == [0.25] * 4
    # 2 + 0.9*4*0.25 per interval = 2.9, 3.8, 3.8, 3.8
    assert energy_trajectory(H, s).tolist() == pytest.approx([2.9, 3.8, 3.8, 3.8])


@pytest.mark.parametrize(
    "bad",
    [
        (
            pd.DatetimeIndex(["2024-06-03 01:00", "2024-06-03 00:00"], tz="UTC"),
            pd.Timestamp("2024-06-03 02:00", tz="UTC"),
        ),
        (
            pd.DatetimeIndex(["2024-06-03 00:00"], tz="UTC"),
            pd.Timestamp("2024-06-03 00:00", tz="UTC"),
        ),
        (pd.DatetimeIndex(["2024-06-03 00:00"]), pd.Timestamp("2024-06-03 01:00")),
        (pd.DatetimeIndex([], tz="UTC"), pd.Timestamp("2024-06-03 01:00", tz="UTC")),
    ],
)
def test_invalid_index_rejected(bad: tuple[pd.DatetimeIndex, pd.Timestamp]) -> None:
    with pytest.raises(ValueError):
        interval_hours_from_index(*bad)


def test_schedule_shape_validation_and_immutability() -> None:
    with pytest.raises(ValueError, match="same length"):
        Schedule([1.0, 2.0], [0.0], [1.0, 1.0])
    with pytest.raises(ValueError, match="one-dimensional"):
        Schedule([[1.0]], [[0.0]], [[1.0]])
    s = Schedule([1.0, 0.0], [0.0, 1.0], 0.25)  # scalar dt broadcast
    assert s.dt_hours.tolist() == [0.25, 0.25]
    with pytest.raises(ValueError):
        s.charge_mw[0] = 3.0  # read-only array
