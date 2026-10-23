"""Two-window heuristic: hand-calculated choices and feasibility by construction."""

from dataclasses import replace

import numpy as np
import pytest

from gridflex.backtest import two_window_schedule
from gridflex.battery import BatteryModel, check_schedule
from gridflex.settlement import settle
from tests_helpers import toy_battery

# 1 MW, SoC window 0-2 MWh, lossless, empty start, no costs.
SMALL = toy_battery(energy_capacity_mwh=2.0, soc_max_mwh=2.0)


def test_charge_then_discharge_hand_case() -> None:
    # Q = 2 MWh -> 2 h blocks. Best: buy at 10 + 50 (hours 0-1) = 60, sell at
    # 100 + 100 (hours 3-4) = 200 -> 140.
    sched, value = two_window_schedule(
        SMALL, np.array([10, 50, 50, 100, 100.0]), np.ones(5), 0.0
    )
    assert value == pytest.approx(140.0)
    assert sched.charge_mw.tolist() == [1, 1, 0, 0, 0]
    assert sched.discharge_mw.tolist() == [0, 0, 0, 1, 1]


def test_discharge_first_when_peak_comes_first() -> None:
    # eta 0.9/0.8, start 1 MWh (window 0-2): releasing 1 MWh delivers 0.8 MWh AC
    # (1 h at 0.8 MW) at 100 = 80; restoring it takes 1/0.9 = 1.111 MWh AC over
    # 2 h at 10 = 11.11 -> value 80 - 100/9 = 68.89.
    b = replace(SMALL, eta_charge=0.9, eta_discharge=0.8, initial_energy_mwh=1.0)
    sched, value = two_window_schedule(
        b, np.array([100, 100, 10, 10.0]), np.ones(4), 1.0
    )
    assert value == pytest.approx(80 - 100 / 9)
    assert sched.discharge_mw.tolist() == pytest.approx([0.8, 0, 0, 0])
    assert sched.charge_mw.tolist() == pytest.approx([0, 0, 1 / 1.8, 1 / 1.8])


@pytest.mark.parametrize("prices", [[50.0] * 6, [10, 20, 30, 40, 50, 60.0][::-1]])
def test_idle_when_nothing_profitable(prices: list[float]) -> None:
    # Flat prices; or falling prices from an empty start (only charge-first is
    # possible and every later price is lower).
    sched, value = two_window_schedule(SMALL, np.array(prices), np.ones(6), 0.0)
    assert value == 0.0
    assert not sched.charge_mw.any() and not sched.discharge_mw.any()


def test_degradation_and_fees_can_make_idle() -> None:
    # Spread 40 per MWh on 2 MWh = 80; degradation 30/MWh * 2 = 60 and fee
    # 6/MWh * 4 = 24 -> 80 - 84 < 0 -> idle.
    b = replace(SMALL, degradation_cost_eur_per_mwh=30.0)
    prices = np.array([10, 10, 50, 50.0])
    assert two_window_schedule(b, prices, np.ones(4), 0.0)[1] == pytest.approx(20.0)
    sched, value = two_window_schedule(
        b, prices, np.ones(4), 0.0, trading_cost_eur_per_mwh=6.0
    )
    assert value == 0.0 and not sched.charge_mw.any()


def test_cycle_limit_shrinks_the_cycle() -> None:
    # Limit 1.5 EFC/day over 8 h -> 0.5 EFC * 2 MWh usable = 1 MWh per cycle.
    b = replace(SMALL, max_efc_per_day=1.5)
    sched, value = two_window_schedule(
        b, np.array([0, 0, 0, 0, 100, 100, 100, 100.0]), np.ones(8), 0.0
    )
    assert value == pytest.approx(100.0)
    assert sched.charge_mw.sum() == pytest.approx(1.0)


def test_irregular_durations() -> None:
    # 2 MWh at 1 MW needs 2 h: blocks of [0.5, 1.5] h and [1, 1] h.
    sched, value = two_window_schedule(
        SMALL, np.array([0, 0, 90, 90.0]), np.array([0.5, 1.5, 1.0, 1.0]), 0.0
    )
    assert value == pytest.approx(180.0)
    assert (sched.charge_mw * sched.dt_hours).sum() == pytest.approx(2.0)


@pytest.mark.parametrize("seed", range(60))
def test_random_schedules_feasible_cyclic_and_value_consistent(seed: int) -> None:
    rng = np.random.default_rng(seed)
    cap = float(rng.uniform(1, 30))
    lo, hi = sorted(rng.uniform(0, cap, 2))
    b = BatteryModel(
        charge_power_mw=float(rng.uniform(0.5, 10)),
        discharge_power_mw=float(rng.uniform(0.5, 10)),
        energy_capacity_mwh=cap,
        soc_min_mwh=float(lo),
        soc_max_mwh=float(hi),
        initial_energy_mwh=float(rng.uniform(lo, hi)),
        eta_charge=float(rng.uniform(0.8, 1)),
        eta_discharge=float(rng.uniform(0.8, 1)),
        availability_fraction=float(rng.choice([1.0, 0.9])),
        max_efc_per_day=float(rng.uniform(0.5, 3)) if rng.random() < 0.5 else None,
        efc_basis=str(
            rng.choice(["dc_discharge_per_usable", "ac_discharge_per_nameplate"])
        ),  # type: ignore[arg-type]
        degradation_cost_eur_per_mwh=float(rng.uniform(0, 20)),
        degradation_basis=str(rng.choice(["dc_discharge", "ac_discharge"])),  # type: ignore[arg-type]
    )
    n = int(rng.choice([23, 24, 25, 96]))
    dt = np.full(n, 0.25 if n == 96 else 1.0)
    prices = rng.normal(60, 100, n)
    fee = float(rng.uniform(0, 2))
    sched, value = two_window_schedule(
        b, prices, dt, b.initial_energy_mwh, trading_cost_eur_per_mwh=fee
    )
    check = check_schedule(b, sched, tol_mw=1e-6, tol_mwh=1e-6)
    assert check.ok, check.summary()  # includes cyclic end energy and cycle cap
    assert value >= 0
    planned = settle(
        sched, prices, b, e_start_mwh=b.initial_energy_mwh, trading_cost_eur_per_mwh=fee
    )
    assert planned.net_operating_revenue_eur == pytest.approx(value, abs=1e-6)
