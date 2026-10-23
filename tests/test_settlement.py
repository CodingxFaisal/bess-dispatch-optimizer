"""Settlement arithmetic against hand calculations."""

import math
from dataclasses import replace

import pytest

from gridflex.battery import Schedule
from gridflex.settlement import SettlementError, settle
from tests_helpers import toy_battery

# eta 0.9 / 0.8, degradation 10 EUR per DC MWh discharged.
B = toy_battery(
    eta_charge=0.9,
    eta_discharge=0.8,
    degradation_cost_eur_per_mwh=10.0,
    initial_energy_mwh=2.0,
)


def test_hand_settlement_positive_prices() -> None:
    # Charge 1 MW for 2 h at 20 -> buy 2 MWh, pay 40.
    # Discharge 0.72 MW for 2.5 h at 50 -> sell 1.8 MWh, earn 90.
    s = Schedule([1.0, 0.0], [0.0, 0.72], [2.0, 2.5])
    r = settle(s, [20.0, 50.0], B, e_start_mwh=2.0, trading_cost_eur_per_mwh=0.5)
    assert r.purchases_eur == pytest.approx(40.0)
    assert r.sales_eur == pytest.approx(90.0)
    assert r.gross_revenue_eur == pytest.approx(50.0)
    # DC discharge = 1.8 / 0.8 = 2.25 MWh -> 22.5 EUR degradation.
    assert r.degradation_cost_eur == pytest.approx(22.5)
    # Trading cost 0.5 * (2 + 1.8) = 1.9.
    assert r.trading_cost_eur == pytest.approx(1.9)
    assert r.net_operating_revenue_eur == pytest.approx(50.0 - 22.5 - 1.9)
    assert (r.energy_bought_mwh, r.energy_sold_mwh) == pytest.approx((2.0, 1.8))
    # Energy: 2 + 0.9*2 - 2.25 = 1.55 MWh.
    assert r.e_end_mwh == pytest.approx(1.55)
    assert r.terminal_value_eur == 0.0  # cyclic policy: not valued


def test_negative_price_purchase_is_income() -> None:
    # Buying 1 MWh at -30 earns 30; selling 0.72 MWh at 0 earns nothing.
    s = Schedule([1.0, 0.0], [0.0, 0.72], [1.0, 1.0])
    r = settle(
        s, [-30.0, 0.0], replace(B, degradation_cost_eur_per_mwh=0.0), e_start_mwh=2.0
    )
    assert r.purchases_eur == pytest.approx(-30.0)
    assert r.gross_revenue_eur == pytest.approx(30.0)
    assert r.net_operating_revenue_eur == pytest.approx(30.0)


def test_idle_schedule_settles_to_zero() -> None:
    s = Schedule([0.0] * 24, [0.0] * 24, 1.0)
    r = settle(s, [100.0] * 24, B, e_start_mwh=2.0, trading_cost_eur_per_mwh=1.0)
    assert r.gross_revenue_eur == r.net_operating_revenue_eur == 0.0
    assert r.e_end_mwh == 2.0 and r.equivalent_full_cycles == 0.0


def test_free_terminal_value_reported_separately() -> None:
    free = replace(B, terminal_policy="free", terminal_energy_value_eur_per_mwh=40.0)
    s = Schedule([1.0], [0.0], [1.0])  # store 0.9 MWh
    r = settle(s, [10.0], free, e_start_mwh=2.0)
    assert r.net_operating_revenue_eur == pytest.approx(-10.0)  # cash only
    assert r.terminal_value_eur == pytest.approx(36.0)  # 40 * 0.9


def test_expected_vs_realised_differ_only_by_price() -> None:
    s = Schedule([1.0, 0.0], [0.0, 0.72], [1.0, 1.0])
    expected = settle(s, [20.0, 60.0], B, e_start_mwh=2.0)
    realised = settle(s, [20.0, 40.0], B, e_start_mwh=2.0)
    # Same physics and costs; revenue differs by 0.72 * (60 - 40) = 14.4.
    assert expected.degradation_cost_eur == realised.degradation_cost_eur
    assert expected.gross_revenue_eur - realised.gross_revenue_eur == pytest.approx(
        14.4
    )


@pytest.mark.parametrize(
    ("prices", "fee"),
    [([1.0], 0.0), ([1.0, math.nan], 0.0), ([1.0, math.inf], 0.0), ([1.0, 2.0], -1.0)],
)
def test_invalid_settlement_inputs(prices: list[float], fee: float) -> None:
    s = Schedule([0.0, 0.0], [0.0, 0.0], 1.0)
    with pytest.raises(SettlementError):
        settle(s, prices, B, e_start_mwh=2.0, trading_cost_eur_per_mwh=fee)
