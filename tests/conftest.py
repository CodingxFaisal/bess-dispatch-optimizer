"""Shared pytest fixtures."""

from dataclasses import replace

import pytest

from gridflex.config import GridFlexConfig, load_config
from gridflex.optimize.params import DispatchParams
from tests_helpers import DEFAULT_CONFIG


@pytest.fixture(scope="session")
def gf_cfg() -> GridFlexConfig:
    """The validated default GridFlex configuration."""
    return load_config(DEFAULT_CONFIG)


@pytest.fixture(scope="session")
def cfg(gf_cfg: GridFlexConfig) -> DispatchParams:
    """v0.1 optimiser parameters derived from the default configuration."""
    return DispatchParams.from_config(gf_cfg)


@pytest.fixture
def toy_cfg(cfg: DispatchParams) -> DispatchParams:
    """A clean, analytically tractable battery for hand-checkable tests.

    10 MW / 10 MWh (1h), lossless, no degradation, full SoC range, empty start,
    generous cycle limit. Every optimum here can be computed by hand.
    """
    return replace(
        cfg,
        power_mw=10.0,
        energy_mwh=10.0,
        eta_charge=1.0,
        eta_discharge=1.0,
        soc_min_frac=0.0,
        soc_max_frac=1.0,
        soc_init_frac=0.0,
        c_deg_eur_per_mwh=0.0,
        max_cycles_per_day=100.0,  # effectively unbound
    )
