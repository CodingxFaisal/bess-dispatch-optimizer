"""Shared pytest fixtures."""

import pytest

from gridflex.config import GridFlexConfig, load_config
from tests_helpers import DEFAULT_CONFIG


@pytest.fixture(scope="session")
def gf_cfg() -> GridFlexConfig:
    """The validated default GridFlex configuration."""
    return load_config(DEFAULT_CONFIG)
