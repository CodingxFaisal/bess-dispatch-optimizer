"""Optimisation config contract."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from gridflex.config import ConfigError, GridFlexConfig, load_config
from tests_helpers import DEFAULT_CONFIG


def _load(tmp_path: Path, **opt: Any) -> GridFlexConfig:
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["optimization"].update(opt)
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_defaults(gf_cfg: GridFlexConfig) -> None:
    o = gf_cfg.optimization
    assert (o.solver, o.threads, o.accept_time_limited) == ("highs", 1, False)
    assert o.mip_rel_gap == 1e-6 and o.time_limit_s == 60.0


@pytest.mark.parametrize(
    "opt",
    [
        {"solver": "gurobi"},
        {"threads": 0},
        {"mip_rel_gap": -1},
        {"time_limit_s": 0},
        {"feasibility_tol_mwh": 0},
        {"unknown": 1},
    ],
)
def test_invalid_rejected(tmp_path: Path, opt: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, **opt)


def test_no_time_limit_allowed(tmp_path: Path) -> None:
    assert _load(tmp_path, time_limit_s=None).optimization.time_limit_s is None
