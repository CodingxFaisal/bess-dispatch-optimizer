"""Tests for configuration loading, derived quantities and validation."""

import math
from dataclasses import replace

import pytest

from bess.config import BatteryConfig


def test_efficiency_split_is_sqrt(cfg):
    assert cfg.eta_ch == pytest.approx(math.sqrt(cfg.round_trip_efficiency))
    assert cfg.eta_dis == pytest.approx(math.sqrt(cfg.round_trip_efficiency))
    # Round trip is recovered by the product.
    assert cfg.eta_ch * cfg.eta_dis == pytest.approx(cfg.round_trip_efficiency)


def test_degradation_cost_derivation(cfg):
    expected = (
        cfg.replacement_cost_eur_per_kwh * 1000.0
        / (cfg.cycle_life_cycles * cfg.depth_of_discharge)
    )
    assert cfg.c_deg_eur_per_mwh == pytest.approx(expected)


def test_soc_bounds_and_duration(cfg):
    assert cfg.e_min == pytest.approx(cfg.soc_min_frac * cfg.energy_mwh)
    assert cfg.e_max == pytest.approx(cfg.soc_max_frac * cfg.energy_mwh)
    assert cfg.duration_hours == pytest.approx(cfg.energy_mwh / cfg.power_mw)


def test_invalid_efficiency_rejected(cfg):
    with pytest.raises(ValueError):
        replace(cfg, round_trip_efficiency=1.5)


def test_invalid_soc_bounds_rejected(cfg):
    with pytest.raises(ValueError):
        replace(cfg, soc_min_frac=0.9, soc_max_frac=0.1)


def test_init_soc_must_lie_in_bounds(cfg):
    with pytest.raises(ValueError):
        replace(cfg, soc_init_frac=0.99, soc_max_frac=0.95)
