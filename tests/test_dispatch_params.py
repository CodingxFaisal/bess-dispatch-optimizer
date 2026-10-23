"""The v0.1 parameter bridge converts configured conventions into v0.1 inputs."""

import math
from dataclasses import replace

import pytest

from gridflex.config import ConfigError, GridFlexConfig
from gridflex.optimize.params import DispatchParams

SQRT_086 = 0.9273618495495703  # sqrt(0.86), one-way efficiency


def test_bridge_matches_hand_values(cfg: DispatchParams) -> None:
    """Defaults: degradation on DC discharge, EFC on DC discharge / usable."""
    assert cfg.power_mw == 10.0
    assert cfg.energy_mwh == 20.0
    assert cfg.eta_ch == pytest.approx(SQRT_086)
    assert cfg.eta_dis == pytest.approx(SQRT_086)
    assert cfg.round_trip_efficiency == pytest.approx(0.86)
    assert (cfg.e_min, cfg.e_max, cfg.e_init) == pytest.approx((1.0, 19.0, 10.0))
    # 16.6667 EUR per DC MWh = 16.6667 / 0.92736 = 17.9721 EUR per AC MWh.
    assert cfg.c_deg_eur_per_mwh == pytest.approx(17.97212887)
    # 1.5 EFC * 18 MWh usable = 27 MWh from the cells = 27 * 0.92736 AC MWh.
    assert cfg.daily_cycle_energy_mwh == pytest.approx(25.03876994)
    assert cfg.duration_hours == pytest.approx(2.0)
    assert cfg.delta_t_hours == 1.0
    assert cfg.capex_eur_per_kwh is None


def test_bridge_reproduces_v01_with_v01_conventions(gf_cfg: GridFlexConfig) -> None:
    v01 = gf_cfg.model_copy(
        update={
            "battery": gf_cfg.battery.model_copy(
                update={
                    "efc_basis": "ac_discharge_per_nameplate",
                    "degradation": gf_cfg.battery.degradation.model_copy(
                        update={"cost_basis": "ac_discharge"}
                    ),
                }
            )
        }
    )
    p = DispatchParams.from_config(v01)
    assert p.c_deg_eur_per_mwh == pytest.approx(120_000 / 7_200)
    assert p.daily_cycle_energy_mwh == pytest.approx(30.0)  # 1.5 * 20 MWh


def test_availability_derates_power(gf_cfg: GridFlexConfig) -> None:
    derated = gf_cfg.model_copy(
        update={
            "battery": gf_cfg.battery.model_copy(update={"availability_fraction": 0.95})
        }
    )
    assert DispatchParams.from_config(derated).power_mw == pytest.approx(9.5)


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
