"""Shared pytest fixtures."""

from dataclasses import replace
from pathlib import Path

import pytest

from bess.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def cfg():
    """The real default configuration."""
    return load_config(REPO_ROOT / "config" / "battery.yaml")


@pytest.fixture
def toy_cfg(cfg):
    """A clean, analytically tractable battery for hand-checkable tests.

    10 MW / 10 MWh (1h), lossless, no degradation, full SoC range, empty start,
    generous cycle limit. Every optimum here can be computed by hand.
    """
    return replace(
        cfg,
        power_mw=10.0,
        energy_mwh=10.0,
        round_trip_efficiency=1.0,
        soc_min_frac=0.0,
        soc_max_frac=1.0,
        soc_init_frac=0.0,
        replacement_cost_eur_per_kwh=0.0,  # -> c_deg = 0
        max_cycles_per_day=100.0,          # effectively unbound
    )
