"""A small but complete run directory built offline from the SMARD fixture."""

import sys
from pathlib import Path

import pytest

from gridflex.config import GridFlexConfig
from gridflex.reporting.example import build_example_run
from tests_helpers import FIXTURE_CACHE, REPO_ROOT

APP_DIR = REPO_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


@pytest.fixture(scope="session")
def mini_run(tmp_path_factory: pytest.TempPathFactory, gf_cfg: GridFlexConfig) -> Path:
    return build_example_run(
        gf_cfg, FIXTURE_CACHE, tmp_path_factory.mktemp("runs") / "latest"
    )
