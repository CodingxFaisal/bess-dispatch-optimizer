"""The v0.1 parameter bridge reproduces the v0.1 values from the new config."""

import math
from dataclasses import replace

import pytest

from gridflex.config import ConfigError, GridFlexConfig
from gridflex.optimize.params import DispatchParams


def test_bridge_matches_hand_values(cfg: DispatchParams) -> None:
    assert cfg.power_mw == 10.0
    assert cfg.energy_mwh == 20.0
    assert cfg.eta_ch == pytest.approx(math.sqrt(0.86))
    assert cfg.eta_dis == pytest.approx(math.sqrt(0.86))
    assert cfg.round_trip_efficiency == pytest.approx(0.86)
    assert (cfg.e_min, cfg.e_max, cfg.e_init) == pytest.approx((1.0, 19.0, 10.0))
    assert cfg.c_deg_eur_per_mwh == pytest.approx(120_000 / 7_200)
    assert cfg.daily_cycle_energy_mwh == pytest.approx(30.0)  # 1.5 * 20 MWh
    assert cfg.duration_hours == pytest.approx(2.0)
    assert cfg.delta_t_hours == 1.0
    assert cfg.capex_eur_per_kwh is None


def test_bridge_rejects_unsupported_settings(gf_cfg: GridFlexConfig) -> None:
    asym = gf_cfg.model_copy(
        update={
            "battery": gf_cfg.battery.model_copy(update={"discharge_power_mw": 5.0})
        }
    )
    with pytest.raises(ConfigError, match="charge_power_mw == discharge_power_mw"):
        DispatchParams.from_config(asym)
    free = gf_cfg.model_copy(
        update={
            "battery": gf_cfg.battery.model_copy(update={"terminal_soc_policy": "free"})
        }
    )
    with pytest.raises(ConfigError, match="cyclic"):
        DispatchParams.from_config(free)


def test_no_cycle_limit_maps_to_infinity(gf_cfg: GridFlexConfig) -> None:
    unl = gf_cfg.model_copy(
        update={
            "battery": gf_cfg.battery.model_copy(
                update={"max_equivalent_full_cycles_per_day": None}
            )
        }
    )
    assert math.isinf(DispatchParams.from_config(unl).max_cycles_per_day)


@pytest.mark.parametrize(
    "changes",
    [
        {"eta_charge": 1.5},
        {"eta_discharge": 0.0},
        {"soc_min_frac": 0.9, "soc_max_frac": 0.1},
        {"soc_init_frac": 0.99},
        {"power_mw": 0.0},
        {"c_deg_eur_per_mwh": -1.0},
        {"max_cycles_per_day": 0.0},
        {"delta_t_hours": 0.0},
    ],
)
def test_invalid_params_rejected(
    cfg: DispatchParams, changes: dict[str, float]
) -> None:
    with pytest.raises(ValueError):
        replace(cfg, **changes)
