"""Correctness tests for the dispatch MILP.

These are the invariants that separate a real optimizer from a plausible-looking
one: physical energy balance, SoC bounds, charge/discharge exclusivity, an
analytically hand-checked revenue, the cycle cap, and the negative-price trap.
"""

import numpy as np
import pytest

from bess.optimize import optimize_dispatch, settle

TOL = 1e-6


# --------------------------------------------------------------------------- #
# 1. Energy balance holds at every timestep                                   #
# --------------------------------------------------------------------------- #
def test_energy_balance(cfg):
    rng = np.random.default_rng(0)
    prices = rng.normal(80, 40, size=48)
    res = optimize_dispatch(prices, cfg, e_start=cfg.e_init, e_end="cyclic")
    assert res.solved

    prev = np.concatenate([[res.e_start], res.e[:-1]])
    expected = prev + cfg.eta_ch * res.p_ch - res.p_dis / cfg.eta_dis
    assert np.allclose(res.e, expected, atol=1e-4)


# --------------------------------------------------------------------------- #
# 2. SoC never breaches its bounds                                            #
# --------------------------------------------------------------------------- #
def test_soc_within_bounds(cfg):
    rng = np.random.default_rng(1)
    prices = rng.normal(80, 60, size=72)
    res = optimize_dispatch(prices, cfg)
    assert res.e.min() >= cfg.e_min - 1e-4
    assert res.e.max() <= cfg.e_max + 1e-4


# --------------------------------------------------------------------------- #
# 3. No simultaneous charge and discharge (the binary works)                  #
# --------------------------------------------------------------------------- #
def test_no_simultaneous_charge_discharge(cfg):
    rng = np.random.default_rng(2)
    # Include negative prices -- the case where a binary-free model cheats.
    prices = rng.normal(20, 90, size=96)
    res = optimize_dispatch(prices, cfg)
    simultaneous = (res.p_ch > 1e-4) & (res.p_dis > 1e-4)
    assert not simultaneous.any()


# --------------------------------------------------------------------------- #
# 4. Revenue matches a hand calculation on a 3-hour toy case                  #
# --------------------------------------------------------------------------- #
def test_revenue_matches_manual_calc(toy_cfg):
    # Lossless 10 MW / 10 MWh battery, empty start. Prices: cheap, cheap, dear.
    # Optimal: charge 10 MWh @ 10 in hour 0, discharge 10 MWh @ 100 in hour 2.
    # Net = 10*100 - 10*10 = 900. Cyclic end (0) is satisfied.
    prices = np.array([10.0, 10.0, 100.0])
    res = optimize_dispatch(prices, toy_cfg, e_start=0.0, e_end="cyclic")
    rev = settle(res, prices, toy_cfg)

    assert res.solved
    assert rev.net_revenue_eur == pytest.approx(900.0, abs=1e-3)
    assert rev.energy_discharged_mwh == pytest.approx(10.0, abs=1e-4)
    assert rev.degradation_cost_eur == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 5. Cycle limit is respected                                                 #
# --------------------------------------------------------------------------- #
def test_cycle_limit_respected(cfg):
    # A price series with many spreads would love to over-cycle; the cap binds.
    prices = np.tile([10.0, 100.0], 24)  # 48h of alternating cheap/dear
    res = optimize_dispatch(prices, cfg, e_start=cfg.e_init, e_end="cyclic")
    rev = settle(res, prices, cfg)

    n_days = len(prices) / 24
    max_cycles = cfg.max_cycles_per_day * n_days
    assert rev.equivalent_full_cycles <= max_cycles + 1e-4


def test_cycle_limit_actually_binds_without_cap(cfg):
    # Sanity: removing the cap lets it cycle more, proving the cap is active.
    prices = np.tile([10.0, 100.0], 24)
    capped = settle(optimize_dispatch(prices, cfg, e_end="cyclic"), prices, cfg)
    uncapped = settle(
        optimize_dispatch(prices, cfg, e_end="cyclic", cycle_limit_mwh=float("inf")),
        prices, cfg,
    )
    assert uncapped.equivalent_full_cycles > capped.equivalent_full_cycles


# --------------------------------------------------------------------------- #
# 6. The negative-price trap: without the binary the model cheats             #
# --------------------------------------------------------------------------- #
def test_binary_prevents_negative_price_loop(cfg):
    # Single hour, deeply negative price, cyclic SoC. Without the binary the
    # model can charge and discharge at once, burning energy through the
    # round-trip losses purely to be paid for the net consumption. That only
    # pays if the negative price beats the degradation cost of the churn, so we
    # zero out degradation here to isolate the physics of the trap.
    from dataclasses import replace
    cfg0 = replace(cfg, replacement_cost_eur_per_kwh=0.0)  # c_deg = 0
    prices = np.array([-100.0])

    with_binary = optimize_dispatch(prices, cfg0, e_end="cyclic", allow_binary=True)
    assert not ((with_binary.p_ch > 1e-4) & (with_binary.p_dis > 1e-4)).any()

    without_binary = optimize_dispatch(prices, cfg0, e_end="cyclic", allow_binary=False)
    # The pathology: simultaneous charge and discharge to profit from p < 0.
    assert ((without_binary.p_ch > 1e-4) & (without_binary.p_dis > 1e-4)).any()


# --------------------------------------------------------------------------- #
# 7. Terminal-SoC constraint prevents free end-of-horizon draining            #
# --------------------------------------------------------------------------- #
def test_terminal_constraint_prevents_draining(cfg):
    # Start half full; a rising price series tempts the model to end empty.
    prices = np.linspace(20, 120, 24)
    cyclic = optimize_dispatch(prices, cfg, e_start=cfg.e_init, e_end="cyclic")
    free = optimize_dispatch(prices, cfg, e_start=cfg.e_init, e_end=None)

    assert cyclic.e[-1] == pytest.approx(cfg.e_init, abs=1e-3)
    # Without the terminal constraint it drains below the starting SoC.
    assert free.e[-1] < cfg.e_init - 1e-3
