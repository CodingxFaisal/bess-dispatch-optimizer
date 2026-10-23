"""Stage 3 config contracts: degradation/EFC bases and terminal valuation."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from gridflex.config import ConfigError, load_config
from tests_helpers import DEFAULT_CONFIG


def _load(tmp_path: Path, **battery: Any) -> Any:
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["battery"].update(battery)
    # These tests concern battery conventions; the default backtest includes a
    # heuristic strategy that only supports the cyclic policy.
    raw.pop("backtest")
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_defaults_are_dc_based(tmp_path: Path) -> None:
    cfg = _load(tmp_path)
    assert cfg.battery.degradation.cost_basis == "dc_discharge"
    assert cfg.battery.efc_basis == "dc_discharge_per_usable"


def test_v01_conventions_accepted(tmp_path: Path) -> None:
    cfg = _load(tmp_path, efc_basis="ac_discharge_per_nameplate")
    assert cfg.battery.efc_basis == "ac_discharge_per_nameplate"


def test_free_terminal_requires_explicit_value(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="terminal_energy_value_eur_per_mwh"):
        _load(tmp_path, terminal_soc_policy="free")
    cfg = _load(
        tmp_path, terminal_soc_policy="free", terminal_energy_value_eur_per_mwh=55.0
    )
    assert cfg.battery.terminal_energy_value_eur_per_mwh == 55.0


@pytest.mark.parametrize(
    "battery",
    [
        {"terminal_energy_value_eur_per_mwh": 10.0},  # not 'free'
        {"efc_basis": "dc_charge"},
        {
            "degradation": {
                "replacement_cost_eur_per_kwh": 1,
                "cycle_life_cycles": 1,
                "depth_of_discharge": 1,
                "cost_basis": "cell_magic",
            }
        },
    ],
)
def test_invalid_conventions_rejected(tmp_path: Path, battery: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, **battery)
