"""Optimal dispatch on small cases whose optimum is derived by hand.

Default toy battery (tests_helpers.toy_battery): 1 MW / 10 MWh, lossless,
starts empty, no degradation, cyclic terminal policy, no cycle cap.
"""

import numpy as np
import pandas as pd
import pytest

from gridflex.optimization import DispatchProblem, DispatchSolution, solve_dispatch
from tests_helpers import toy_battery

LOSSY = dict(eta_charge=0.9, eta_discharge=0.8)  # round trip 0.72


def solve(
    prices: list[float], dt: float | list[float] = 1.0, **battery: object
) -> DispatchSolution:
    sol = solve_dispatch(DispatchProblem(toy_battery(**battery), prices, dt))
    assert sol.success, sol.report
    return sol


def test_simple_arbitrage() -> None:
    # 10 MW / 10 MWh: buy 10 MWh at 10, sell at 100 -> 1000 - 100 = 900.
    sol = solve([10.0, 10.0, 100.0], charge_power_mw=10.0, discharge_power_mw=10.0)
    assert sol.objective_eur == pytest.approx(900.0, abs=1e-6)
    assert sol.schedule is not None
    assert sol.schedule.discharge_mw.tolist() == pytest.approx([0, 0, 10.0], abs=1e-9)


def test_efficiency_and_break_even() -> None:
    # Charge 1 MWh (stores 0.9), return it as 0.9 * 0.8 = 0.72 MWh AC.
    # Profit = 0.72 * p2 - p1 = 0.72 * 50 - 20 = 16.
    sol = solve([20.0, 50.0], **LOSSY)
    assert sol.objective_eur == pytest.approx(16.0, abs=1e-6)
    assert sol.schedule is not None
    assert sol.schedule.charge_mw.tolist() == pytest.approx([1.0, 0.0], abs=1e-9)
    assert sol.schedule.discharge_mw.tolist() == pytest.approx([0.0, 0.72], abs=1e-9)
    # Break-even sale price is 20 / 0.72 = 27.78: at 27 the battery idles,
    # at 28 it earns 0.72 * 28 - 20 = 0.16.
    assert solve([20.0, 27.0], **LOSSY).objective_eur == pytest.approx(0.0, abs=1e-9)
    assert solve([20.0, 28.0], **LOSSY).objective_eur == pytest.approx(0.16, abs=1e-6)


def test_degradation_cost_bases() -> None:
    # DC basis: 0.9 MWh leaves the cells -> 10 * 0.9 = 9 -> profit 16 - 9 = 7.
    dc = solve([20.0, 50.0], degradation_cost_eur_per_mwh=10.0, **LOSSY)
    assert dc.objective_eur == pytest.approx(7.0, abs=1e-6)
    assert dc.degradation_cost_eur == pytest.approx(9.0, abs=1e-6)
    # AC basis: 0.72 MWh delivered -> 7.2 -> profit 8.8.
    ac = solve(
        [20.0, 50.0],
        degradation_cost_eur_per_mwh=10.0,
        degradation_basis="ac_discharge",
        **LOSSY,
    )
    assert ac.objective_eur == pytest.approx(8.8, abs=1e-6)


def test_negative_prices() -> None:
    # Paid 50 to charge 1 MWh; cyclic end forces selling the 0.72 MWh back.
    assert solve([-50.0, 0.0], **LOSSY).objective_eur == pytest.approx(50.0, abs=1e-6)
    # Selling at -10 costs 0.72 * 10 = 7.2 -> 42.8 (still better than idling).
    assert solve([-50.0, -10.0], **LOSSY).objective_eur == pytest.approx(42.8, abs=1e-6)
    # With 10 EUR per DC MWh: 50 - 9 = 41.
    assert solve(
        [-50.0, 0.0], degradation_cost_eur_per_mwh=10.0, **LOSSY
    ).objective_eur == pytest.approx(41.0, abs=1e-6)


@pytest.mark.parametrize("price", [0.0, 50.0])
def test_flat_prices_do_nothing_when_cycling_costs(price: float) -> None:
    # At a flat non-negative price, losses and degradation make any cycle lose.
    sol = solve(
        [price] * 6, initial_energy_mwh=5.0, degradation_cost_eur_per_mwh=1.0, **LOSSY
    )
    assert sol.objective_eur == pytest.approx(0.0, abs=1e-9)
    assert sol.throughput is not None
    assert sol.throughput.ac_charge_mwh == pytest.approx(0.0, abs=1e-9)
    assert sol.throughput.ac_discharge_mwh == pytest.approx(0.0, abs=1e-9)


def test_flat_negative_price_pays_for_losses() -> None:
    # At a flat -30 EUR/MWh the market pays for net consumption, and conversion
    # losses *are* net consumption. Per hour charging at 1 MW: +30; returning the
    # stored 0.9 MWh as 0.72 MWh AC costs 21.6 plus 0.9 * 1 EUR degradation
    # -> +7.5 per cycle. Six hours fit three charge/discharge pairs -> 22.5.
    # Sequential (not simultaneous) operation: physically valid.
    sol = solve(
        [-30.0] * 6, initial_energy_mwh=5.0, degradation_cost_eur_per_mwh=1.0, **LOSSY
    )
    assert sol.objective_eur == pytest.approx(22.5, abs=1e-6)
    assert sol.throughput is not None
    assert sol.throughput.ac_charge_mwh == pytest.approx(3.0, abs=1e-6)
    assert sol.throughput.ac_discharge_mwh == pytest.approx(2.16, abs=1e-6)
    assert sol.schedule is not None
    assert not np.any((sol.schedule.charge_mw > 0) & (sol.schedule.discharge_mw > 0))


def test_flat_prices_lossless_is_worthless() -> None:
    # Lossless and free to cycle: many optima, all worth exactly 0.
    assert solve([42.0] * 5, initial_energy_mwh=5.0).objective_eur == pytest.approx(
        0.0, abs=1e-9
    )


def test_irregular_interval_durations() -> None:
    # 4 MW / 2 MWh lossless: charge 4 MW for 0.5 h = 2 MWh at 10 (cost 20),
    # sell 2 MWh over 2 h (1 MW) at 100 (+200) -> 180.
    sol = solve(
        [10.0, 100.0],
        dt=[0.5, 2.0],
        charge_power_mw=4.0,
        discharge_power_mw=4.0,
        energy_capacity_mwh=2.0,
        soc_max_mwh=2.0,
    )
    assert sol.objective_eur == pytest.approx(180.0, abs=1e-6)
    assert sol.schedule is not None
    assert sol.schedule.discharge_mw[1] == pytest.approx(1.0, abs=1e-9)


def test_fixed_terminal_energy() -> None:
    # Must end with 1 MWh: buy it at 10 -> -10.
    sol = solve([10.0], terminal_policy="fixed", terminal_energy_mwh=1.0)
    assert sol.objective_eur == pytest.approx(-10.0, abs=1e-6)
    assert sol.energy_mwh is not None and sol.energy_mwh[-1] == pytest.approx(1.0)


@pytest.mark.parametrize(("value", "expected"), [(15.0, 10.0), (5.0, 0.0)])
def test_free_terminal_energy_is_valued(value: float, expected: float) -> None:
    # 4 MW / 2 MWh from empty: storing 2 MWh costs 20 and is worth 2 * value.
    sol = solve(
        [10.0],
        charge_power_mw=4.0,
        discharge_power_mw=4.0,
        energy_capacity_mwh=2.0,
        soc_max_mwh=2.0,
        terminal_policy="free",
        terminal_energy_value_eur_per_mwh=value,
    )
    assert sol.objective_eur == pytest.approx(expected, abs=1e-6)
    assert sol.terminal_value_eur == pytest.approx(
        expected + (20.0 if expected else 0.0), abs=1e-6
    )


def test_free_terminal_values_the_start_energy() -> None:
    # Start with 2 MWh, nothing to trade at price 10 vs terminal value 10:
    # selling the stock earns 10/MWh but loses 10/MWh of terminal value -> 0.
    sol = solve(
        [10.0],
        energy_capacity_mwh=2.0,
        soc_max_mwh=2.0,
        initial_energy_mwh=2.0,
        terminal_policy="free",
        terminal_energy_value_eur_per_mwh=10.0,
    )
    assert sol.objective_eur == pytest.approx(0.0, abs=1e-6)


def test_cycle_limit_binds() -> None:
    # 1 MWh lossless, prices 0/100 x4 over 8 h: uncapped 4 cycles -> 400.
    prices = [0.0, 100.0] * 4
    small = dict(energy_capacity_mwh=1.0, soc_max_mwh=1.0)
    assert solve(prices, **small).objective_eur == pytest.approx(400.0, abs=1e-6)
    # Cap 1.5 EFC/day pro-rated to 8 h = 0.5 EFC = 0.5 MWh discharged -> 50.
    capped = solve(prices, max_efc_per_day=1.5, **small)
    assert capped.objective_eur == pytest.approx(50.0, abs=1e-6)
    assert capped.throughput is not None
    assert capped.throughput.efc == pytest.approx(0.5, abs=1e-9)


def test_cycle_limit_on_25_hour_day() -> None:
    # 25 local hours on 27 Oct 2024; cap 1 EFC/day -> 25/24 MWh at 100.
    start = pd.Timestamp("2024-10-27").tz_localize("Europe/Berlin")
    end = start + pd.offsets.Day(1)
    idx = pd.date_range(start, end, freq="h", inclusive="left").tz_convert("UTC")
    assert len(idx) == 25
    prices = np.where(np.arange(25) % 2 == 0, 0.0, 100.0)
    b = toy_battery(energy_capacity_mwh=1.0, soc_max_mwh=1.0, max_efc_per_day=1.0)
    sol = solve_dispatch(
        DispatchProblem.from_index(b, idx, end.tz_convert("UTC"), prices)
    )
    assert sol.success
    assert sol.objective_eur == pytest.approx(100.0 * 25 / 24, abs=1e-6)


def test_asymmetric_power() -> None:
    # Charge 2 MW, discharge 1 MW, 2 MWh: buy 2 MWh at 0, sell 1 + 1 at 100 -> 200.
    two = dict(charge_power_mw=2.0, energy_capacity_mwh=2.0, soc_max_mwh=2.0)
    assert solve([0.0, 100.0, 100.0], **two).objective_eur == pytest.approx(200.0)
    # Discharge limited to 0.5 MW: only 1 MWh can be sold back -> 100.
    assert solve(
        [0.0, 100.0, 100.0], discharge_power_mw=0.5, **two
    ).objective_eur == pytest.approx(100.0)


def test_availability_profile() -> None:
    # As above, but the battery is unavailable in the last interval -> 100.
    b = toy_battery(charge_power_mw=2.0, energy_capacity_mwh=2.0, soc_max_mwh=2.0)
    sol = solve_dispatch(
        DispatchProblem(b, [0.0, 100.0, 100.0], 1.0, availability=[1.0, 1.0, 0.0])
    )
    assert sol.success and sol.objective_eur == pytest.approx(100.0)
    assert sol.schedule is not None and sol.schedule.discharge_mw[2] == 0.0


def test_start_energy_is_respected() -> None:
    # Start with 1 MWh, cyclic: it can be sold at 100 and rebought at 0.
    sol = solve([100.0, 0.0], initial_energy_mwh=1.0)
    assert sol.objective_eur == pytest.approx(100.0)
    # Same prices from empty: nothing to sell first, buying first earns nothing.
    assert solve([100.0, 0.0]).objective_eur == pytest.approx(0.0, abs=1e-9)


def test_trading_cost() -> None:
    # Efficiency case (profit 16) with 2 EUR/MWh on 1 + 0.72 MWh traded:
    # 16 - 2 * 1.72 = 12.56.
    b = toy_battery(**LOSSY)
    sol = solve_dispatch(
        DispatchProblem(b, [20.0, 50.0], 1.0, trading_cost_eur_per_mwh=2.0)
    )
    assert sol.success and sol.objective_eur == pytest.approx(12.56, abs=1e-6)
    assert sol.trading_cost_eur == pytest.approx(3.44, abs=1e-6)
    # Above the break-even fee 16 / 1.72 = 9.30 the battery idles.
    idle = solve_dispatch(
        DispatchProblem(b, [20.0, 50.0], 1.0, trading_cost_eur_per_mwh=10.0)
    )
    assert idle.objective_eur == pytest.approx(0.0, abs=1e-9)
